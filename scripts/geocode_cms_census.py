"""
Geocode CMS hospital provider records using the US Census Bureau Batch API,
with a Nominatim fallback for US territories that Census cannot handle.
After geocoding, a GeoParquet is built from all records with valid coordinates.

The input parquet is read-only; geocode results are written to a new output
file.  Records already successfully geocoded (``geocode_status == 'ok'``) are
skipped by default.

Usage
-----
    # Full pipeline: geocode → GeoParquet
    python scripts/geocode_cms_census.py

    # Explicit paths
    python scripts/geocode_cms_census.py \\
        --in-path    E:/lifelinepois/data/bronze/cms/cms_hospital_providers.parquet \\
        --out-path   E:/lifelinepois/data/bronze/cms/cms_hospital_providers_geocoded.parquet \\
        --geo-out-path E:/lifelinepois/data/bronze/cms/cms_hospital_providers_points.parquet

    # Re-run only failed records
    python scripts/geocode_cms_census.py --statuses-to-retry no_match error score_reject

    # Force re-geocode everything
    python scripts/geocode_cms_census.py --force

    # Skip Nominatim (Census-only)
    python scripts/geocode_cms_census.py --no-nominatim

    # Skip geocoding — build GeoParquet from an already-geocoded parquet
    python scripts/geocode_cms_census.py \\
        --geocoded-path E:/lifelinepois/data/bronze/cms/cms_hospital_providers_geocoded.parquet \\
        --geo-out-path  E:/lifelinepois/data/bronze/cms/cms_hospital_providers_points.parquet

    # Geocode only — skip GeoParquet build
    python scripts/geocode_cms_census.py --skip-geo

Output columns added / updated (geocoded flat parquet)
-------------------------------------------------------
    geocoded_lon    WGS-84 longitude
    geocoded_lat    WGS-84 latitude
    geocode_score   jaro_winkler score (NaN for Census/Nominatim — no per-record score)
    geocode_status  "ok" | "no_match" | "bad_address" | "foreign" | "error"
    geocode_source  "census" | "nominatim"

GeoParquet output
-----------------
    All rows where geocoded_lon/lat are not null, with a ``geometry`` column
    (Point, EPSG:4326) built from the geocoded coordinates.  All other
    attributes from the flat parquet are preserved.

Notes
-----
* Census Batch API URL:
      https://geocoding.geo.census.gov/geocoder/locations/addressbatch
  - Up to 9,999 records per POST; CSV format: ID, address, city, state, zip
  - Response CSV: no header; "Match" rows have lon,lat in column index 5
  - Census returns coordinates as "longitude,latitude" (x,y order).

* Nominatim is rate-limited to 1 request/second per OSM usage policy.
  Use --max-nominatim to cap requests (default: 500).

* This script is self-contained — it does not import from lib/.
"""
from __future__ import annotations

import argparse
import csv
import io
import re
import time
from pathlib import Path
from typing import Optional

import httpx
import pandas as pd

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_IN = Path("E:/lifelinepois/data/bronze/cms/cms_hospital_providers.parquet")
_CENSUS_BATCH_URL = "https://geocoding.geo.census.gov/geocoder/locations/addressbatch"
_CENSUS_CHUNK = 9_999
_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_NOMINATIM_USER_AGENT = "LifelinePOIs/1.0 (geocode_cms_census.py)"
_TERRITORY_CODES = frozenset({"VI", "GU", "MP", "AS"})

# Default statuses eligible for fallback retry (excludes "ok", "foreign", "bad_address")
_DEFAULT_RETRY = ("no_match", "score_reject", "error")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_zip(zipcode: str) -> str:
    """Return 5-digit ZIP, zero-padded. Drops ZIP+4 suffix."""
    digits = re.sub(r"[^0-9]", "", zipcode.split("-")[0].split(" ")[0])
    return digits[:5].zfill(5) if digits else ""


def _cities_plausible(city_a: str, city_b: str, threshold: float = 0.75) -> bool:
    """
    Return True if two city name strings are plausibly the same location.

    Prefix / substring fast path first (handles "ST. PAUL" vs "SAINT PAUL"),
    then jaro_winkler via rapidfuzz if available, otherwise a word-overlap
    heuristic.
    """
    if not city_a or not city_b:
        return True  # missing data: don't reject on city check
    if city_a == city_b:
        return True
    short, long = (city_a, city_b) if len(city_a) <= len(city_b) else (city_b, city_a)
    if long.startswith(short[:4]):
        return True
    try:
        from rapidfuzz.distance import JaroWinkler  # noqa: PLC0415
        return JaroWinkler.normalized_similarity(city_a, city_b) >= threshold
    except ImportError:
        return bool(set(city_a.split()) & set(city_b.split()))


def _init_geocode_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Add geocode result columns if they don't exist."""
    if "geocoded_lon" not in df.columns:
        df["geocoded_lon"] = float("nan")
    if "geocoded_lat" not in df.columns:
        df["geocoded_lat"] = float("nan")
    if "geocode_score" not in df.columns:
        df["geocode_score"] = float("nan")
    if "geocode_status" not in df.columns:
        df["geocode_status"] = pd.NA
    if "geocode_source" not in df.columns:
        df["geocode_source"] = pd.NA
    return df


def _atomic_write(df: pd.DataFrame, out_path: Path) -> None:
    """Write *df* to *out_path* atomically via a temp-file rename."""
    tmp = out_path.with_suffix(".tmp.parquet")
    try:
        df.to_parquet(tmp, index=False)
        tmp.replace(out_path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


# ---------------------------------------------------------------------------
# Census Batch Geocoder
# ---------------------------------------------------------------------------

def _run_census_batch(
    df: pd.DataFrame,
    pending_mask: pd.Series,
    counts: dict[str, int],
) -> dict[int, tuple[float, float]]:
    """
    Submit pending rows to the Census Batch API and return a dict of
    ``{dataframe_index: (lon, lat)}`` for matched records.
    """
    pending = df[pending_mask][["ST_ADR", "CITY_NAME", "STATE_CD", "ZIP_CD"]].copy()
    all_indices = list(pending.index)
    census_matches: dict[int, tuple[float, float]] = {}
    n_total = len(all_indices)

    for chunk_start in range(0, n_total, _CENSUS_CHUNK):
        chunk_indices = all_indices[chunk_start: chunk_start + _CENSUS_CHUNK]
        chunk = pending.loc[chunk_indices]
        n_chunk = len(chunk_indices)
        end = min(chunk_start + n_chunk, n_total)
        print(f"  [Census] Submitting rows {chunk_start + 1:,}–{end:,} of {n_total:,}…")

        buf = io.StringIO()
        writer = csv.writer(buf)
        for idx, row in chunk.iterrows():
            zip5 = _normalize_zip(str(row.get("ZIP_CD") or "").strip())
            writer.writerow([
                idx,
                str(row.get("ST_ADR") or "").strip(),
                str(row.get("CITY_NAME") or "").strip(),
                str(row.get("STATE_CD") or "").strip().upper(),
                zip5 or str(row.get("ZIP_CD") or "").strip(),
            ])

        try:
            resp = httpx.post(
                _CENSUS_BATCH_URL,
                data={"benchmark": "Public_AR_Current"},
                files={"addressFile": ("addresses.csv", buf.getvalue(), "text/csv")},
                timeout=300,
            )
            resp.raise_for_status()
        except Exception as exc:
            print(f"  [Census] Batch request failed: {exc}")
            counts["error"] = counts.get("error", 0) + n_chunk
            continue

        chunk_matches = 0
        for row in csv.reader(io.StringIO(resp.text)):
            if len(row) < 3:
                continue
            try:
                row_idx = int(row[0])
            except ValueError:
                continue
            match_flag = row[2].strip()
            if match_flag != "Match" or len(row) < 6:
                continue
            coords_str = row[5].strip()
            if not coords_str:
                continue
            try:
                lon_str, lat_str = coords_str.split(",", 1)
                lon, lat = float(lon_str.strip()), float(lat_str.strip())
            except ValueError:
                continue

            # City plausibility check
            cms_city = str(df.at[row_idx, "CITY_NAME"] if "CITY_NAME" in df.columns else "").strip().upper()
            census_city = ""
            if len(row) >= 5 and row[4].strip():
                parts = [p.strip() for p in row[4].split(",")]
                census_city = parts[-3].strip().upper() if len(parts) >= 3 else parts[0].upper()
            if not _cities_plausible(cms_city, census_city):
                continue

            census_matches[row_idx] = (lon, lat)
            chunk_matches += 1

        print(f"    → {chunk_matches:,} matched in this chunk.")

    return census_matches


# ---------------------------------------------------------------------------
# Nominatim Geocoder
# ---------------------------------------------------------------------------

def _run_nominatim(
    df: pd.DataFrame,
    statuses_to_retry: tuple[str, ...],
    max_requests: int,
    counts: dict[str, int],
) -> None:
    """
    Geocode remaining territory records in-place using the Nominatim API.

    Only processes rows whose ``STATE_CD`` is in _TERRITORY_CODES and whose
    ``geocode_status`` is still in ``statuses_to_retry`` (not yet matched).
    """
    still_unmatched_mask = (
        (df["geocode_status"].isin(statuses_to_retry) | df["geocode_status"].isna())
        & df["STATE_CD"].astype(str).str.upper().str.strip().isin(_TERRITORY_CODES)
    )
    n_total = int(still_unmatched_mask.sum())
    if n_total == 0:
        print("  [Nominatim] No territory records remaining — skipping.")
        return

    n_capped = min(n_total, max_requests)
    if n_total > max_requests:
        print(f"  [Nominatim] {n_total:,} territory records, capped at {max_requests:,} (1 req/sec).")
    else:
        print(f"  [Nominatim] {n_total:,} territory records (1 req/sec)…")

    indices = list(df[still_unmatched_mask].index[:n_capped])
    headers = {"User-Agent": _NOMINATIM_USER_AGENT}

    for i, row_idx in enumerate(indices, 1):
        row = df.loc[row_idx]
        zip5 = _normalize_zip(str(row.get("ZIP_CD") or "").strip())
        street = str(row.get("ST_ADR") or "").strip()
        city = str(row.get("CITY_NAME") or "").strip()
        state = str(row.get("STATE_CD") or "").strip().upper()

        if not street:
            counts["error"] = counts.get("error", 0) + 1
            continue

        time.sleep(1.1)  # OSM policy: max 1 req/sec
        if i % 50 == 0:
            print(f"    [Nominatim] {i}/{n_capped} processed…")

        try:
            resp = httpx.get(
                _NOMINATIM_URL,
                params={
                    "street": street,
                    "city": city,
                    "state": state,
                    "postalcode": zip5 or "",
                    "countrycodes": "us",
                    "format": "jsonv2",
                    "limit": 1,
                    "addressdetails": 0,
                },
                headers=headers,
                timeout=30,
            )
            resp.raise_for_status()
            hits = resp.json()
        except Exception as exc:
            print(f"    [Nominatim] Error at row {row_idx}: {exc}")
            counts["error"] = counts.get("error", 0) + 1
            continue

        if not hits:
            counts["no_match"] = counts.get("no_match", 0) + 1
            continue

        hit = hits[0]
        try:
            lat = float(hit["lat"])
            lon = float(hit["lon"])
        except (KeyError, ValueError):
            counts["error"] = counts.get("error", 0) + 1
            continue

        cms_city = city.upper()
        nom_city = str(hit.get("display_name") or "").split(",")[0].strip().upper()
        if not _cities_plausible(cms_city, nom_city):
            counts["no_match"] = counts.get("no_match", 0) + 1
            continue

        df.at[row_idx, "geocoded_lon"] = lon
        df.at[row_idx, "geocoded_lat"] = lat
        df.at[row_idx, "geocode_score"] = float("nan")
        df.at[row_idx, "geocode_status"] = "ok"
        df.at[row_idx, "geocode_source"] = "nominatim"
        counts["ok_nominatim"] = counts.get("ok_nominatim", 0) + 1

    print(f"  [Nominatim] Done: {counts.get('ok_nominatim', 0):,} matched.")


# ---------------------------------------------------------------------------
# Main geocoding pipeline
# ---------------------------------------------------------------------------

def geocode_cms_census(
    in_path: Path,
    out_path: Path,
    statuses_to_retry: tuple[str, ...] = _DEFAULT_RETRY,
    force: bool = False,
    use_nominatim: bool = True,
    max_nominatim: int = 500,
    geo_out_path: Optional[Path] = None,
) -> dict[str, int]:
    """
    Geocode CMS provider records using Census Batch + optional Nominatim,
    then build a GeoParquet from all rows with valid coordinates.

    Parameters
    ----------
    in_path:
        Input CMS parquet (read-only).
    out_path:
        Output parquet path for geocoded flat results.
    statuses_to_retry:
        ``geocode_status`` values to re-attempt.  Records with status ``"ok"``
        are never retried unless ``force=True``.
    force:
        If ``True``, re-geocode all records regardless of current status.
    use_nominatim:
        Run Nominatim for territory records still unmatched after Census.
    max_nominatim:
        Hard cap on Nominatim requests per run.
    geo_out_path:
        If given, build a GeoParquet from the geocoded results and write it
        here.  Pass ``None`` to skip the GeoParquet step.

    Returns
    -------
    dict[str, int]
        Counts by disposition: ``ok_census``, ``ok_nominatim``, ``no_match``,
        ``bad_address``, ``foreign``, ``error``.
    """
    in_path = Path(in_path)
    out_path = Path(out_path)

    if not in_path.exists():
        raise FileNotFoundError(f"Input parquet not found: {in_path}")

    print(f"[CMS census geocoder] Reading: {in_path}")
    df = pd.read_parquet(in_path)
    df = _init_geocode_columns(df)
    print(f"  Loaded {len(df):,} rows, {len(df.columns)} columns.")

    # ------------------------------------------------------------------
    # Determine which records to process
    # ------------------------------------------------------------------
    if force:
        pending_mask = pd.Series([True] * len(df), index=df.index)
        # Reset geocode columns for forced records
        df["geocoded_lon"] = float("nan")
        df["geocoded_lat"] = float("nan")
        df["geocode_score"] = float("nan")
        df["geocode_status"] = pd.NA
        df["geocode_source"] = pd.NA
    else:
        pending_mask = (
            df["geocode_status"].isin(statuses_to_retry)
            | df["geocode_status"].isna()
        )

    # Pre-filter: skip records with missing required fields
    bad_addr_mask = (
        df["ST_ADR"].isna() | (df["ST_ADR"].astype(str).str.strip() == "")
        | df["STATE_CD"].isna() | (df["STATE_CD"].astype(str).str.strip() == "")
        | df["ZIP_CD"].isna() | (df["ZIP_CD"].astype(str).str.strip() == "")
    )
    new_bad = pending_mask & bad_addr_mask
    if int(new_bad.sum()) > 0:
        df.loc[new_bad, "geocode_status"] = "bad_address"
        pending_mask = pending_mask & ~bad_addr_mask
        print(f"  Marked {int(new_bad.sum()):,} records as 'bad_address' (missing addr/state/zip).")

    n_pending = int(pending_mask.sum())
    if n_pending == 0:
        print("  No records to geocode — all already processed.")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(df, out_path)
        print(f"  Written → {out_path}")
        return {}

    already_ok = int((df["geocode_status"] == "ok").sum())
    print(f"  Already geocoded (ok): {already_ok:,}")
    print(f"  Pending geocode:       {n_pending:,}")

    counts: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Stage 1: Census Batch Geocoder
    # ------------------------------------------------------------------
    print("\n[Stage 1] Census Bureau Batch Geocoder")
    census_matches = _run_census_batch(df, pending_mask, counts)

    for row_idx, (lon, lat) in census_matches.items():
        df.at[row_idx, "geocoded_lon"] = lon
        df.at[row_idx, "geocoded_lat"] = lat
        df.at[row_idx, "geocode_score"] = float("nan")
        df.at[row_idx, "geocode_status"] = "ok"
        df.at[row_idx, "geocode_source"] = "census"
        counts["ok_census"] = counts.get("ok_census", 0) + 1

    census_ok = counts.get("ok_census", 0)
    print(f"  Census stage complete: {census_ok:,}/{n_pending:,} matched.")

    # Mark remaining pending records as no_match for Nominatim eligibility check
    still_pending_mask = pending_mask & (df["geocode_status"].isna() | df["geocode_status"].isin(statuses_to_retry))
    df.loc[still_pending_mask, "geocode_status"] = "no_match"

    # Atomic write after Census — preserves progress if Nominatim is interrupted
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(df, out_path)
    print(f"  Checkpoint written → {out_path}")

    # ------------------------------------------------------------------
    # Stage 2: Nominatim fallback (territory records only)
    # ------------------------------------------------------------------
    if use_nominatim:
        print("\n[Stage 2] Nominatim (OSM) — territory fallback")
        _run_nominatim(df, ("no_match",), max_nominatim, counts)
        # Final write after Nominatim
        _atomic_write(df, out_path)

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    ok_total = counts.get("ok_census", 0) + counts.get("ok_nominatim", 0)
    pct = ok_total / n_pending * 100 if n_pending > 0 else 0.0
    print(f"\n[CMS census geocoder] Summary: {ok_total:,}/{n_pending:,} geocoded ({pct:.1f}%)")
    print(f"  ok_census    : {counts.get('ok_census', 0):>8,}")
    print(f"  ok_nominatim : {counts.get('ok_nominatim', 0):>8,}")
    print(f"  no_match     : {counts.get('no_match', 0):>8,}")
    print(f"  bad_address  : {counts.get('bad_address', 0):>8,}")
    print(f"  foreign      : {counts.get('foreign', 0):>8,}")
    print(f"  error        : {counts.get('error', 0):>8,}")
    print(f"\n  Output → {out_path}")

    if geo_out_path is not None:
        build_geoparquet(out_path, geo_out_path)

    return counts


# ---------------------------------------------------------------------------
# GeoParquet builder
# ---------------------------------------------------------------------------

def build_geoparquet(
    geocoded_path: Path,
    geo_out_path: Path,
) -> int:
    """
    Build a GeoParquet from a flat geocoded CMS parquet.

    Reads *geocoded_path*, filters to rows where ``geocoded_lon`` and
    ``geocoded_lat`` are both non-null, creates a Point geometry column
    (WGS-84 / EPSG:4326), and writes a GeoParquet to *geo_out_path*.

    All existing columns are preserved.

    Parameters
    ----------
    geocoded_path:
        Path to the flat geocoded parquet produced by ``geocode_cms_census()``.
    geo_out_path:
        Destination GeoParquet path.

    Returns
    -------
    int
        Number of rows written to the GeoParquet.
    """
    import geopandas as gpd  # noqa: PLC0415
    from shapely.geometry import Point  # noqa: PLC0415

    geocoded_path = Path(geocoded_path)
    geo_out_path = Path(geo_out_path)

    if not geocoded_path.exists():
        raise FileNotFoundError(f"Geocoded parquet not found: {geocoded_path}")

    print(f"\n[GeoParquet] Reading geocoded parquet: {geocoded_path}")
    df = pd.read_parquet(geocoded_path)
    total = len(df)

    # Filter to rows with valid coordinates
    valid_mask = df["geocoded_lon"].notna() & df["geocoded_lat"].notna()
    n_valid = int(valid_mask.sum())
    n_skipped = total - n_valid
    print(f"  Total rows     : {total:,}")
    print(f"  With coords    : {n_valid:,}")
    print(f"  Without coords : {n_skipped:,} (excluded)")

    if n_valid == 0:
        print("  WARNING: No rows with valid coordinates — GeoParquet not written.")
        return 0

    valid_df = df[valid_mask].copy()
    geoms = [
        Point(lon, lat)
        for lon, lat in zip(valid_df["geocoded_lon"], valid_df["geocoded_lat"])
    ]
    gdf = gpd.GeoDataFrame(valid_df, geometry=geoms, crs="EPSG:4326")

    geo_out_path.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_parquet(geo_out_path, index=False)

    mb = geo_out_path.stat().st_size / 1_048_576
    print(f"  Written {n_valid:,} rows → {geo_out_path} ({mb:.1f} MB)")
    return n_valid


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _default_out_path(in_path: Path) -> Path:
    return in_path.parent / f"{in_path.stem}_geocoded{in_path.suffix}"


def _default_geo_out_path(geocoded_path: Path) -> Path:
    return geocoded_path.parent / f"{geocoded_path.stem}_points{geocoded_path.suffix}"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Geocode CMS hospital provider records using the US Census Bureau "
            "Batch API, with Nominatim fallback for US territories. "
            "Optionally builds a GeoParquet from geocoded results."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # --- Geocoding args ---
    parser.add_argument(
        "--in-path",
        type=Path,
        default=_DEFAULT_IN,
        help=f"Input CMS parquet (default: {_DEFAULT_IN})",
    )
    parser.add_argument(
        "--out-path",
        type=Path,
        default=None,
        help=(
            "Output flat parquet path for geocoded results. "
            "Default: <in_dir>/<in_stem>_geocoded.parquet"
        ),
    )
    parser.add_argument(
        "--statuses-to-retry",
        nargs="+",
        default=list(_DEFAULT_RETRY),
        metavar="STATUS",
        help=(
            "geocode_status values to re-attempt (space-separated). "
            f"Default: {' '.join(_DEFAULT_RETRY)}"
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-geocode all records, including those already marked 'ok'.",
    )
    parser.add_argument(
        "--no-nominatim",
        action="store_true",
        help="Skip Nominatim stage (Census only).",
    )
    parser.add_argument(
        "--max-nominatim",
        type=int,
        default=500,
        metavar="N",
        help="Hard cap on Nominatim requests per run (default: 500).",
    )

    # --- GeoParquet args ---
    parser.add_argument(
        "--geo-out-path",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Output GeoParquet path (Point geometries, EPSG:4326). "
            "Default: <out_dir>/<out_stem>_points.parquet. "
            "Pass 'none' to disable."
        ),
    )
    parser.add_argument(
        "--skip-geo",
        action="store_true",
        help="Skip GeoParquet build step (geocode only).",
    )

    # --- Shortcut: skip geocoding, build GeoParquet from existing file ---
    parser.add_argument(
        "--geocoded-path",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Path to an already-geocoded flat parquet. "
            "When provided, the geocoding step is skipped entirely and only "
            "the GeoParquet is built from this file."
        ),
    )

    args = parser.parse_args()

    # ------------------------------------------------------------------
    # Mode: geo-only from an existing geocoded parquet
    # ------------------------------------------------------------------
    if args.geocoded_path is not None:
        geocoded_path = args.geocoded_path
        geo_out = args.geo_out_path or _default_geo_out_path(geocoded_path)
        if str(geo_out).lower() == "none":
            parser.error("--geo-out-path cannot be 'none' when using --geocoded-path.")
        build_geoparquet(geocoded_path, geo_out)
        return

    # ------------------------------------------------------------------
    # Mode: full pipeline (geocode + optional GeoParquet)
    # ------------------------------------------------------------------
    in_path = args.in_path
    out_path = args.out_path or _default_out_path(in_path)

    if args.skip_geo or str(args.geo_out_path or "").lower() == "none":
        geo_out = None
    else:
        geo_out = args.geo_out_path or _default_geo_out_path(out_path)

    geocode_cms_census(
        in_path=in_path,
        out_path=out_path,
        statuses_to_retry=tuple(args.statuses_to_retry),
        force=args.force,
        use_nominatim=not args.no_nominatim,
        max_nominatim=args.max_nominatim,
        geo_out_path=geo_out,
    )


if __name__ == "__main__":
    main()
