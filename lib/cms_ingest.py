"""
CMS Hospital & Non-Hospital Provider download helper.

Extracted from ``flows/01_ingest.py`` so the notebook cell stays thin and
this logic can be unit-tested or reused outside of marimo.

Usage (from a flow cell)::

    from lib.cms_ingest import download_cms_providers, geocode_cms_providers, fallback_geocode_cms_providers
    count = download_cms_providers(cfg.cms.api_url, cfg.cms.page_size, out_path)
    stats = geocode_cms_providers(out_path, cfg.cms.geocode_address_path)
    # Fallback: Census Bureau batch + Nominatim for territories
    if cfg.cms.geocode_census_fallback:
        fallback_geocode_cms_providers(out_path)
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional


def download_cms_providers(
    api_url: str,
    page_size: int,
    out_path: Path,
) -> int:
    """
    Download CMS Hospital & Non-Hospital Provider records to a Parquet file.

    Paginates through the CMS Open Data API until all records are fetched,
    writes to ``out_path`` via pandas, and returns the record count.

    Parameters
    ----------
    api_url:
        Base URL for the CMS dataset API endpoint (no query params).
    page_size:
        Number of records to request per API page.
    out_path:
        Destination ``.parquet`` file path (parent directory must exist).

    Returns
    -------
    int
        Number of records written.

    Raises
    ------
    ValueError
        If the API returns no records.
    httpx.HTTPStatusError
        If any API request fails with a non-2xx status.
    """
    import httpx as _httpx
    import pandas as _pd

    out_path = Path(out_path)
    all_rows: list[dict] = []
    offset = 0

    print(f"[CMS] Downloading hospital provider data (page_size={page_size})")
    while True:
        url = f"{api_url}?offset={offset}&size={page_size}"
        resp = _httpx.get(url, timeout=120, follow_redirects=True)
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        # The CMS API may return each record as a list of [key, value] pairs
        # rather than a standard JSON object.  Normalise to dicts either way.
        if batch and not isinstance(batch[0], dict):
            batch = [dict(row) for row in batch]
        all_rows.extend(batch)
        offset += len(batch)
        print(f"  fetched {offset:,} records so far...")
        if len(batch) < page_size:
            break

    if not all_rows:
        raise ValueError("CMS API returned no records")

    # Write via pandas — avoids DuckDB read_json's ~200-column auto-detect
    # limit, which collapses wide records (CMS has ~473 fields) into a single
    # opaque 'json' column.
    df = _pd.DataFrame(all_rows)
    df.to_parquet(out_path, index=False)
    count = len(df)

    mb = out_path.stat().st_size / 1_048_576
    print(f"  CMS providers: {count:,} records → {out_path.name} ({mb:.1f} MB)")
    return count


def geocode_cms_providers(
    parquet_path: Path,
    overture_address_path: str | Path,
    min_score: float = 0.80,
    country: str = "US",
    provider_categories: Optional[list[str]] = None,
    force: bool = False,
) -> dict[str, int]:
    """
    Geocode CMS provider records in-place using Overture address parquet.

    Reads the parquet at ``parquet_path``, attempts to geocode each record
    using its ``ST_ADR``, ``STATE_CD``, and ``ZIP_CD`` fields, and writes
    back with the following columns added/updated:

        geocoded_lon   – WGS-84 longitude of the best Overture address match
        geocoded_lat   – WGS-84 latitude
        geocode_score  – jaro_winkler_similarity of the matched street name
        geocode_status – disposition code (see below)

    Disposition codes stored in ``geocode_status``:
        "ok"           – geocoded successfully
        "no_match"     – no Overture address found for this ZIP + state
        "score_reject" – best match is below ``min_score``
        "bad_address"  – empty or unparseable ZIP, state, or street
        "foreign"      – state code is not a recognised US state / territory
        "error"        – unexpected exception during geocode attempt

    Records that already have a ``geocode_status`` are skipped unless
    ``force=True``.

    The parquet is written atomically (temp file → rename) to guard against
    partial writes on interrupt.

    Parameters
    ----------
    parquet_path:
        Path to the CMS bronze parquet (will be overwritten in-place).
    overture_address_path:
        Root of the hive-partitioned Overture address parquet tree,
        structured as ``country={CC}/state_code={ST}/*.parquet``.
    min_score:
        Minimum jaro_winkler_similarity (0–1) to accept an address match.
    country:
        ISO 3166-1 alpha-2 country code for the geocoder (default "US").
    provider_categories:
        Optional list of ``PRVDR_CTGRY_CD`` values to restrict geocoding to.
        ``None`` geocodes all records; pass ``["01"]`` for hospitals only.
    force:
        If ``True``, re-geocode records that already have a ``geocode_status``
        (useful after updating the Overture address dataset).

    Returns
    -------
    dict[str, int]
        Mapping of disposition code → count for this run, e.g.:
        ``{"ok": 12_345, "no_match": 500, "bad_address": 30, ...}``
    """
    import pandas as _pd
    import duckdb as _duckdb
    from shapely import wkt as _shapely_wkt
    from lib.geocoder import geocode, parse_street_address, normalize_zip, US_STATE_NAMES

    parquet_path = Path(parquet_path)
    if not parquet_path.exists():
        raise FileNotFoundError(f"CMS parquet not found: {parquet_path}")

    df = _pd.read_parquet(parquet_path)

    # Initialise geocode columns when absent
    if "geocoded_lon" not in df.columns:
        df["geocoded_lon"] = float("nan")
    if "geocoded_lat" not in df.columns:
        df["geocoded_lat"] = float("nan")
    if "geocode_score" not in df.columns:
        df["geocode_score"] = float("nan")
    if "geocode_status" not in df.columns:
        df["geocode_status"] = _pd.NA
    if "geocode_source" not in df.columns:
        df["geocode_source"] = _pd.NA

    # Rows to process: not yet attempted (or forced)
    if force:
        pending_mask = _pd.Series([True] * len(df), index=df.index)
    else:
        pending_mask = df["geocode_status"].isna()

    # Filter to requested provider categories
    if provider_categories is not None and "PRVDR_CTGRY_CD" in df.columns:
        category_mask = df["PRVDR_CTGRY_CD"].astype(str).isin(provider_categories)
        pending_mask = pending_mask & category_mask

    n_pending = int(pending_mask.sum())
    if n_pending == 0:
        print("[CMS geocode] No pending records to geocode — skipping.")
        return {}

    print(f"[CMS geocode] Geocoding {n_pending:,} records via Overture addresses…")

    # Valid US state/territory codes from the geocoder registry
    valid_states = set(US_STATE_NAMES.keys())

    counts: dict[str, int] = {}

    conn = _duckdb.connect()
    conn.execute("INSTALL spatial; LOAD spatial;")
    try:
        for idx, row in df[pending_mask].iterrows():
            st_adr = str(row.get("ST_ADR") or "").strip()
            state = str(row.get("STATE_CD") or "").strip().upper()[:2]
            zip_raw = str(row.get("ZIP_CD") or "").strip()
            zip5 = normalize_zip(zip_raw)

            # Foreign record: state not in US / territories
            if state and state not in valid_states:
                _set_status(df, idx, "foreign")
                counts["foreign"] = counts.get("foreign", 0) + 1
                continue

            # Bad address: missing required fields or unparseable street
            if not zip5 or not state or not st_adr:
                _set_status(df, idx, "bad_address")
                counts["bad_address"] = counts.get("bad_address", 0) + 1
                continue

            housenumber, street = parse_street_address(st_adr)
            if not street:
                _set_status(df, idx, "bad_address")
                counts["bad_address"] = counts.get("bad_address", 0) + 1
                continue

            # Geocode
            try:
                hits = geocode(
                    street=street,
                    housenumber=housenumber,
                    postcode=zip5,
                    state=state,
                    country=country,
                    base_path=overture_address_path,
                    limit=1,
                    conn=conn,
                )
            except Exception:
                _set_status(df, idx, "error")
                counts["error"] = counts.get("error", 0) + 1
                continue

            if not hits:
                _set_status(df, idx, "no_match")
                counts["no_match"] = counts.get("no_match", 0) + 1
                continue

            best = hits[0]
            score = float(best.get("score") or 0.0)
            if score < min_score:
                _set_status(df, idx, "score_reject")
                counts["score_reject"] = counts.get("score_reject", 0) + 1
                continue

            # Cross-check city for sanity: reject if geocoded city is completely
            # different from the CMS city (catches wrong-ZIP false positives).
            cms_city = str(row.get("CITY_NAME") or "").strip().upper()
            geo_city = str(best.get("postal_city") or "").strip().upper()
            if cms_city and geo_city and not _cities_plausible(cms_city, geo_city):
                _set_status(df, idx, "score_reject")
                counts["score_reject"] = counts.get("score_reject", 0) + 1
                continue

            try:
                geom = _shapely_wkt.loads(best["wkt"])
                df.at[idx, "geocoded_lon"] = geom.x
                df.at[idx, "geocoded_lat"] = geom.y
                df.at[idx, "geocode_score"] = round(score, 4)
                df.at[idx, "geocode_status"] = "ok"
                df.at[idx, "geocode_source"] = "overture"
                counts["ok"] = counts.get("ok", 0) + 1
            except Exception:
                _set_status(df, idx, "error")
                counts["error"] = counts.get("error", 0) + 1
    finally:
        conn.close()

    # Atomic write: temp file → rename
    _atomic_write(df, parquet_path)

    ok = counts.get("ok", 0)
    total = sum(counts.values())
    pct = ok / total * 100 if total > 0 else 0.0
    print(f"  CMS geocode: {ok:,}/{total:,} geocoded ({pct:.1f}%)")
    for status, n in sorted(counts.items()):
        if status != "ok":
            print(f"    {status}: {n:,}")
    return counts


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _set_status(df, idx, status: str) -> None:
    """Set geocode_status on a single row without touching coord columns."""
    df.at[idx, "geocode_status"] = status


def _atomic_write(df, parquet_path: Path) -> None:
    """Write *df* to *parquet_path* atomically via a temp-file rename."""
    tmp_path = parquet_path.with_suffix(".tmp.parquet")
    try:
        df.to_parquet(tmp_path, index=False)
        tmp_path.replace(parquet_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def _print_fallback_summary(counts: dict[str, int]) -> None:
    ok_total = counts.get("ok_census", 0) + counts.get("ok_nominatim", 0)
    total = sum(counts.values())
    pct = ok_total / total * 100 if total > 0 else 0.0
    print(f"  CMS fallback geocode: {ok_total:,}/{total:,} matched ({pct:.1f}%)")
    for k, v in sorted(counts.items()):
        if k not in ("ok_census", "ok_nominatim"):
            print(f"    {k}: {v:,}")
        else:
            print(f"    {k}: {v:,}")


def _cities_plausible(city_a: str, city_b: str, threshold: float = 0.75) -> bool:
    """
    Return True if two city name strings are plausibly the same location.

    Uses a simple prefix / substring check first (fast path for common
    abbreviations like "ST. PAUL" vs "SAINT PAUL"), then falls back to a
    jaro_winkler similarity threshold.
    """
    if city_a == city_b:
        return True
    # Prefix match handles most short-name vs. full-name cases
    short, long = (city_a, city_b) if len(city_a) <= len(city_b) else (city_b, city_a)
    if long.startswith(short[:4]):
        return True
    try:
        from rapidfuzz.distance import JaroWinkler
        score = JaroWinkler.normalized_similarity(city_a, city_b)
        return score >= threshold
    except ImportError:
        # Without rapidfuzz fall back to a simple character overlap check
        common = set(city_a.split()) & set(city_b.split())
        return len(common) > 0


def fallback_geocode_cms_providers(
    parquet_path: Path,
    statuses_to_retry: Optional[tuple[str, ...]] = None,
    provider_categories: Optional[list[str]] = None,
    use_nominatim: bool = True,
    nominatim_user_agent: str = "LifelinePOIs/1.0",
    max_nominatim_requests: int = 500,
) -> dict[str, int]:
    """
    Fallback geocoder for CMS records that Overture could not match.

    Uses two free, sequential strategies:

    1. **US Census Bureau Batch Geocoder** — single HTTP POST for up to 9,999
       records; covers all 50 US states + DC + Puerto Rico well.  Results are
       accepted without a street-score threshold (Census already enforces its
       own matching logic), but are still city-plausibility checked.

    2. **Nominatim (OpenStreetMap)** — rate-limited to 1 req/sec per OSM
       policy; used *only* for records whose ``STATE_CD`` is a US territory
       code that Census does not cover (``VI``, ``GU``, ``MP``, ``AS``) or
       that still have no match after Census.  A hard cap of
       ``max_nominatim_requests`` prevents accidental bulk abuse.

    Both stages write atomically after completing so that a crash mid-run
    preserves Census progress.

    Can be used standalone (without having run ``geocode_cms_providers()``
    first) — if ``geocode_status`` is missing or all-null the entire filtered
    record set is treated as pending.

    Parameters
    ----------
    parquet_path:
        Path to the CMS bronze parquet.
    statuses_to_retry:
        Disposition codes eligible for fallback.  Defaults to
        ``("no_match",)``.  ``None`` values (never geocoded) are always
        included.  You may add ``"score_reject"`` to re-attempt
        low-confidence Overture hits.
    provider_categories:
        Optional list of ``PRVDR_CTGRY_CD`` values to restrict geocoding to.
        ``None`` geocodes all records; pass ``["01"]`` for hospitals only.
    use_nominatim:
        If ``True`` (default), run Nominatim for territory records that
        Census cannot handle.  Set to ``False`` to skip Nominatim entirely.
    nominatim_user_agent:
        HTTP ``User-Agent`` string sent to the Nominatim API.  OSM policy
        requires a descriptive app name and contact.
    max_nominatim_requests:
        Hard cap on the number of Nominatim requests per run.  Prevents
        accidental long-running jobs.  Defaults to 500.

    Returns
    -------
    dict[str, int]
        Mapping of disposition code → count for this run, summed across both
        Census and Nominatim stages.  Keys: ``ok_census``, ``ok_nominatim``,
        ``no_match``, ``error``.
    """
    import csv as _csv
    import io as _io
    import time as _time
    import httpx as _httpx
    import pandas as _pd
    from lib.geocoder import normalize_zip

    # Territory codes that Overture lacks data for; Census may partially cover
    # PR but not VI/GU/MP/AS — all are sent through Census first, then
    # remaining no-matches fall through to Nominatim.
    _TERRITORY_CODES = {"PR", "VI", "GU", "MP", "AS"}
    _CENSUS_BATCH_URL = "https://geocoding.geo.census.gov/geocoder/locations/addressbatch"
    _CENSUS_CHUNK = 9_999

    if statuses_to_retry is None:
        statuses_to_retry = ("no_match",)

    parquet_path = Path(parquet_path)
    if not parquet_path.exists():
        raise FileNotFoundError(f"CMS parquet not found: {parquet_path}")

    df = _pd.read_parquet(parquet_path)

    # Initialise geocode columns if they don't exist yet (standalone use)
    if "geocoded_lon" not in df.columns:
        df["geocoded_lon"] = float("nan")
    if "geocoded_lat" not in df.columns:
        df["geocoded_lat"] = float("nan")
    if "geocode_score" not in df.columns:
        df["geocode_score"] = float("nan")
    if "geocode_status" not in df.columns:
        df["geocode_status"] = _pd.NA
    if "geocode_source" not in df.columns:
        df["geocode_source"] = _pd.NA

    # Records eligible: explicit retry statuses OR never attempted (null status)
    retry_mask = df["geocode_status"].isin(statuses_to_retry) | df["geocode_status"].isna()

    # Apply provider category filter
    if provider_categories is not None and "PRVDR_CTGRY_CD" in df.columns:
        category_mask = df["PRVDR_CTGRY_CD"].astype(str).isin(provider_categories)
        retry_mask = retry_mask & category_mask

    n_retry = int(retry_mask.sum())
    if n_retry == 0:
        print("[CMS fallback geocode] No records to retry — skipping.")
        return {}

    print(f"[CMS fallback geocode] {n_retry:,} records eligible for fallback geocoding.")
    counts: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Stage 1: Census Bureau Batch Geocoder
    # ------------------------------------------------------------------
    pending = df[retry_mask][["ST_ADR", "CITY_NAME", "STATE_CD", "ZIP_CD"]].copy()

    # Build batches of up to _CENSUS_CHUNK rows (no header row in request CSV)
    census_matches: dict[int, tuple[float, float]] = {}  # idx → (lon, lat)
    all_indices = list(pending.index)

    for chunk_start in range(0, len(all_indices), _CENSUS_CHUNK):
        chunk_indices = all_indices[chunk_start: chunk_start + _CENSUS_CHUNK]
        chunk = pending.loc[chunk_indices]

        buf = _io.StringIO()
        writer = _csv.writer(buf)
        for idx, row in chunk.iterrows():
            zip5 = normalize_zip(str(row.get("ZIP_CD") or "").strip())
            writer.writerow([
                idx,
                str(row.get("ST_ADR") or "").strip(),
                str(row.get("CITY_NAME") or "").strip(),
                str(row.get("STATE_CD") or "").strip().upper(),
                zip5 or str(row.get("ZIP_CD") or "").strip(),
            ])

        print(f"  Census batch: submitting {len(chunk_indices):,} records…")
        try:
            resp = _httpx.post(
                _CENSUS_BATCH_URL,
                data={"benchmark": "Public_AR_Current"},
                files={"addressFile": ("addresses.csv", buf.getvalue(), "text/csv")},
                timeout=300,
            )
            resp.raise_for_status()
        except Exception as exc:
            print(f"  Census batch request failed: {exc}")
            counts["error"] = counts.get("error", 0) + len(chunk_indices)
            continue

        # Response CSV has no header. Columns:
        #   Match rows (8 cols): ID, Input Addr, Match, Match Type, Output Addr, Coordinates, TIGER ID, Side
        #   No_Match rows (3+ cols): ID, Input Addr, No_Match
        for row in _csv.reader(_io.StringIO(resp.text)):
            if len(row) < 3:
                continue
            try:
                row_idx = int(row[0])
            except ValueError:
                continue
            match_flag = row[2].strip()
            if match_flag == "Match" and len(row) >= 6:
                coords_str = row[5].strip()
                if not coords_str:
                    continue
                try:
                    # Census returns "lon,lat"
                    lon_str, lat_str = coords_str.split(",", 1)
                    lon, lat = float(lon_str.strip()), float(lat_str.strip())
                except ValueError:
                    continue

                # City plausibility check
                cms_city = str(df.at[row_idx, "CITY_NAME"] if "CITY_NAME" in df.columns else "").strip().upper()
                # Census Output Address format: "STREET, CITY, ST, ZIP"
                census_city = ""
                if len(row) >= 5 and row[4].strip():
                    parts = [p.strip() for p in row[4].split(",")]
                    if len(parts) >= 2:
                        census_city = parts[-3].strip().upper() if len(parts) >= 3 else parts[0].upper()
                if cms_city and census_city and not _cities_plausible(cms_city, census_city):
                    continue

                census_matches[row_idx] = (lon, lat)

    for row_idx, (lon, lat) in census_matches.items():
        df.at[row_idx, "geocoded_lon"] = lon
        df.at[row_idx, "geocoded_lat"] = lat
        df.at[row_idx, "geocode_score"] = float("nan")  # Census doesn't return a score
        df.at[row_idx, "geocode_status"] = "ok"
        df.at[row_idx, "geocode_source"] = "census"
        counts["ok_census"] = counts.get("ok_census", 0) + 1

    # Atomic write after Census stage to preserve progress
    _atomic_write(df, parquet_path)

    census_ok = counts.get("ok_census", 0)
    print(f"  Census stage: {census_ok:,}/{n_retry:,} matched.")

    # ------------------------------------------------------------------
    # Stage 2: Nominatim — territory codes only, with hard cap
    # ------------------------------------------------------------------
    if not use_nominatim:
        _print_fallback_summary(counts)
        return counts

    # Remaining no-match records in territory state codes
    still_unmatched_mask = (
        df["geocode_status"].isin(statuses_to_retry)
        & df["STATE_CD"].astype(str).str.upper().str.strip().isin(_TERRITORY_CODES)
    )
    n_nominatim = int(still_unmatched_mask.sum())
    if n_nominatim == 0:
        _print_fallback_summary(counts)
        return counts

    n_nominatim_capped = min(n_nominatim, max_nominatim_requests)
    if n_nominatim > max_nominatim_requests:
        print(f"  Nominatim: {n_nominatim:,} territory records, capped at {max_nominatim_requests:,}.")
    else:
        print(f"  Nominatim: {n_nominatim:,} territory records (rate-limited to 1 req/sec)…")

    nominatim_indices = list(df[still_unmatched_mask].index[:n_nominatim_capped])
    _NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
    headers = {"User-Agent": nominatim_user_agent}

    for row_idx in nominatim_indices:
        row = df.loc[row_idx]
        zip5 = normalize_zip(str(row.get("ZIP_CD") or "").strip())
        street = str(row.get("ST_ADR") or "").strip()
        city = str(row.get("CITY_NAME") or "").strip()
        state = str(row.get("STATE_CD") or "").strip().upper()

        if not street:
            counts["error"] = counts.get("error", 0) + 1
            continue

        _time.sleep(1.1)  # OSM policy: max 1 req/sec
        try:
            resp = _httpx.get(
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
            print(f"    Nominatim error for row {row_idx}: {exc}")
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

        # City plausibility check
        cms_city = city.upper()
        nom_city = str(hit.get("display_name") or "").split(",")[0].strip().upper()
        if cms_city and nom_city and not _cities_plausible(cms_city, nom_city):
            counts["no_match"] = counts.get("no_match", 0) + 1
            continue

        df.at[row_idx, "geocoded_lon"] = lon
        df.at[row_idx, "geocoded_lat"] = lat
        df.at[row_idx, "geocode_score"] = float("nan")
        df.at[row_idx, "geocode_status"] = "ok"
        df.at[row_idx, "geocode_source"] = "nominatim"
        counts["ok_nominatim"] = counts.get("ok_nominatim", 0) + 1

    # Final atomic write after Nominatim stage
    _atomic_write(df, parquet_path)

    _print_fallback_summary(counts)
    return counts
