"""
CMS Hospital & Non-Hospital Provider Info enrichment for LifelinePOI.

Matches CMS provider records (bronze) against silver health POIs to produce
``silver/attr_health_cms.parquet``, a supplemental attribute table containing
authoritative staffing/capacity counts and related provider metrics.

Matching strategy (two tiers):

  **Tier 1 — Spatial**
    Build a BallTree over CMS records that have valid ``geocoded_lat`` /
    ``geocoded_lon`` (``geocode_status == "ok"``).  For each silver health
    POI, find the nearest CMS point within ``spatial_match_distance_m``
    (default 200 m — looser than the 50 m HIFLD threshold because address-
    level geocoding lands at the kerb, not the building centroid).  A minimum
    fuzzy name score (``spatial_name_threshold``, default 0.55) is required to
    accept the match, guarding against coincidentally nearby hospitals in dense
    areas.

  **Tier 2 — ZIP + fuzzy name**
    For silver POIs not matched in Tier 1, run a vectorized tiered-merge:
    * state + ZIP  → tightest candidates
    * state + city → city fallback for zip-miss POIs
    * state only   → last resort
    Fuzzy ``token_sort_ratio`` scoring over all candidate pairs; best-score-
    wins per POI; minimum ``name_similarity_threshold`` (default 0.80).

Output columns
--------------
Core:
    lifeline_id, cms_provider_num, cms_match_score, cms_match_method,
    cms_match_distance_m

Backward-compat capacity (always present):
    cms_bed_cnt, cms_certified_bed_cnt, cms_operating_rooms

All other ``_CNT`` columns auto-detected from the CMS bronze, prefixed
``cms_``, lowercased.  Values are nullable integers (0 preserved, NaN for
truly absent).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd


# CMS category code for general/acute care hospitals
_HOSPITAL_CTGRY = "01"

# Columns that receive special backward-compat names in the output
_COMPAT_RENAMES: dict[str, str] = {
    "BED_CNT": "cms_bed_cnt",
    "CRTFD_BED_CNT": "cms_certified_bed_cnt",
    "OPRTG_ROOM_CNT": "cms_operating_rooms",
}


def _normalize_name(name: str) -> str:
    """Normalize hospital names for fuzzy matching.
    - Lowercase, strip punctuation, collapse whitespace
    - Remove/reorder common words (e.g., 'Hospital' at start/end)
    - Remove trailing 'INC'
    - Expand abbreviations (e.g., 'CTR' → 'Center')
    """
    if not name or not isinstance(name, str):
        return ""
    name = name.lower()
    name = re.sub(r"[^a-z0-9 ]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    # Remove trailing 'inc'
    name = re.sub(r" inc$", "", name)
    # Expand abbreviations
    abbr = {"ctr": "center", "med": "medical", "univ": "university", "dept": "department"}
    words = [abbr.get(w, w) for w in name.split()]
    name = " ".join(words)
    # Remove/reorder 'hospital' at start/end
    if name.startswith("hospital "):
        name = name[len("hospital "):]
    if name.endswith(" hospital"):
        name = name[:-len(" hospital")]
    name = name.strip()
    return name


def _detect_cnt_columns(df: pd.DataFrame) -> list[str]:
    """Return all columns in *df* whose upper-case name ends with ``_CNT``."""
    return [c for c in df.columns if c.upper().endswith("_CNT")]


def load_cms_providers(bronze_path: Path) -> pd.DataFrame:
    """
    Load CMS provider parquet from bronze and return a filtered DataFrame
    containing only hospital records (PRVDR_CTGRY_CD == "01").

    Includes ``geocoded_lat`` / ``geocoded_lon`` when present and coerces
    all ``_CNT`` columns to nullable ``Int64`` to preserve zeros while
    allowing missing values.

    Returns an empty DataFrame if the file does not exist.
    """
    cms_file = Path(bronze_path) / "cms" / "cms_hospital_providers.parquet"
    if not cms_file.exists():
        return pd.DataFrame()

    df = pd.read_parquet(cms_file)

    # Filter to hospitals only
    if "PRVDR_CTGRY_CD" in df.columns:
        df = df[df["PRVDR_CTGRY_CD"].astype(str) == _HOSPITAL_CTGRY].copy()

    # Normalise key string fields for matching
    for col in ("FAC_NAME", "ST_ADR", "CITY_NAME", "STATE_CD", "ZIP_CD"):
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip()

    if "ZIP_CD" in df.columns:
        df["_zip5"] = df["ZIP_CD"].str[:5]

    if "FAC_NAME" in df.columns:
        df["_name_norm"] = df["FAC_NAME"].apply(_normalize_name)

    # Coerce all _CNT columns to float first, then to nullable Int64
    # (two-step cast avoids the object→Int64 safe-cast error in some pandas versions)
    for col in _detect_cnt_columns(df):
        numeric = pd.to_numeric(df[col], errors="coerce")
        try:
            df[col] = numeric.astype("Int64")
        except (TypeError, ValueError):
            df[col] = numeric  # keep as float64 if Int64 cast fails

    # Normalise geocode coordinates to float (NaN when missing/invalid)
    for col in ("geocoded_lat", "geocoded_lon"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        else:
            df[col] = float("nan")

    return df.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Internal: build the output row dict for a single match
# ---------------------------------------------------------------------------

def _build_result_row(
    lid: str,
    best_row: "pd.Series",
    score: float,
    method: str,
    distance_m: Optional[float],
    cnt_cols: list[str],
) -> dict:
    """Assemble a result dict for one matched health POI."""
    row: dict = {
        "lifeline_id": lid,
        "cms_provider_num": str(best_row.get("PRVDR_NUM", "") or ""),
        "cms_match_score": round(score, 4),
        "cms_match_method": method,
        "cms_match_distance_m": distance_m,
    }
    # Backward-compat capacity columns
    for src, dst in _COMPAT_RENAMES.items():
        val = best_row.get(src, pd.NA)
        row[dst] = int(val) if pd.notna(val) else 0

    # All other _CNT columns as cms_<lower>
    compat_srcs = set(_COMPAT_RENAMES.keys())
    for col in cnt_cols:
        if col in compat_srcs:
            continue  # already handled above
        dst = "cms_" + col.lower()
        val = best_row.get(col, pd.NA)
        row[dst] = int(val) if pd.notna(val) else pd.NA
    return row


# ---------------------------------------------------------------------------
# Tier 1: spatial BallTree match
# ---------------------------------------------------------------------------

def _tier1_spatial(
    health: pd.DataFrame,
    cms: pd.DataFrame,
    spatial_distance_m: float,
    spatial_name_threshold: float,
    cnt_cols: list[str],
) -> tuple[list[dict], set[str]]:
    """
    Match silver health POIs to CMS records using geocoded coordinates.

    Returns (results_list, matched_lifeline_ids).
    """
    try:
        from rapidfuzz import fuzz
        from sklearn.neighbors import BallTree
    except ImportError as exc:
        raise ImportError("rapidfuzz and scikit-learn are required") from exc

    # CMS subset with valid geocoded coordinates
    geocoded_mask = (
        cms["geocoded_lat"].notna()
        & cms["geocoded_lon"].notna()
        & (cms.get("geocode_status", pd.Series("ok", index=cms.index)) == "ok")
    )
    cms_geo = cms[geocoded_mask].copy()
    if len(cms_geo) == 0:
        return [], set()

    # Silver subset with geometry
    health_with_geom = health[health.geometry.notna()].copy()
    if len(health_with_geom) == 0:
        return [], set()

    # Project to Web Mercator for meter-accurate distances
    import geopandas as gpd
    from shapely.geometry import Point

    cms_gdf = gpd.GeoDataFrame(
        cms_geo,
        geometry=gpd.points_from_xy(cms_geo["geocoded_lon"], cms_geo["geocoded_lat"]),
        crs="EPSG:4326",
    ).to_crs("EPSG:3857")

    health_proj = health_with_geom.copy()
    if hasattr(health_proj, "crs") and health_proj.crs is not None:
        health_proj = health_proj.to_crs("EPSG:3857")
    else:
        health_proj = gpd.GeoDataFrame(health_proj, geometry="geometry").set_crs("EPSG:4326").to_crs("EPSG:3857")

    cms_coords = np.array([
        [geom.centroid.x if geom.geom_type != "Point" else geom.x,
         geom.centroid.y if geom.geom_type != "Point" else geom.y]
        for geom in cms_gdf.geometry
    ])
    health_coords = np.array([
        [geom.centroid.x if geom.geom_type != "Point" else geom.x,
         geom.centroid.y if geom.geom_type != "Point" else geom.y]
        for geom in health_proj.geometry
    ])

    tree = BallTree(cms_coords, metric="euclidean")
    distances, indices = tree.query(health_coords, k=1)
    distances = distances.flatten()
    indices = indices.flatten()

    results: list[dict] = []
    matched_ids: set[str] = set()

    cms_geo_reset = cms_geo.reset_index(drop=True)
    lids = health_with_geom["lifeline_id"].tolist()
    names = health_with_geom["_name_norm"].tolist()

    for i, (lid, h_name, dist, idx) in enumerate(zip(lids, names, distances, indices)):
        if dist > spatial_distance_m:
            continue
        candidate = cms_geo_reset.iloc[int(idx)]
        if not h_name:
            continue
        cms_name = str(candidate.get("_name_norm", "") or "")
        name_score = fuzz.token_sort_ratio(h_name, cms_name) / 100.0
        if name_score < spatial_name_threshold:
            continue
        results.append(_build_result_row(lid, candidate, name_score, "spatial", float(dist), cnt_cols))
        matched_ids.add(lid)

    return results, matched_ids


# ---------------------------------------------------------------------------
# Tier 2: ZIP + fuzzy name (vectorized tiered merge, existing logic)
# ---------------------------------------------------------------------------

def _tier2_zip_fuzzy(
    health: pd.DataFrame,
    cms: pd.DataFrame,
    threshold: float,
    cnt_cols: list[str],
    already_matched: set[str],
) -> list[dict]:
    """
    Match unmatched silver health POIs to CMS records using state+ZIP+name.

    Returns a results list.
    """
    try:
        from rapidfuzz import fuzz
    except ImportError as exc:
        raise ImportError("rapidfuzz is required: pip install rapidfuzz") from exc

    # Prepare CMS lookup columns
    cms = cms.copy()
    cms["_state"] = (
        cms["STATE_CD"].str.upper().str[:2] if "STATE_CD" in cms.columns else pd.Series("", index=cms.index)
    )
    cms["_city_norm"] = (
        cms["CITY_NAME"].str.upper() if "CITY_NAME" in cms.columns else pd.Series("", index=cms.index)
    )

    # Rename CMS normalised name to avoid collision
    cms_m = cms.rename(columns={"_name_norm": "_cms_name"})

    keep = ["_state", "_zip5", "_city_norm", "_cms_name"] + [
        c for c in cnt_cols + ["PRVDR_NUM"] if c in cms_m.columns
    ]
    keep = list(dict.fromkeys(keep))  # deduplicate, preserve order
    cms_m = cms_m[[c for c in keep if c in cms_m.columns]]

    # Health subset not yet matched
    health_m = health[~health["lifeline_id"].isin(already_matched)].copy()
    health_m = health_m[health_m["_name_norm"] != ""]

    if len(health_m) == 0:
        return []

    # ── Tier 2a: state + ZIP ──────────────────────────────────────────────
    h_zip = health_m[health_m["_zip5"].str.len() == 5]
    tier2a = h_zip.merge(cms_m, on=["_state", "_zip5"], how="inner") if len(h_zip) else pd.DataFrame()

    # ── Tier 2b: state + city (zip had no candidates) ─────────────────────
    zip_ids = set(tier2a["lifeline_id"]) if len(tier2a) else set()
    h_city = h_zip[~h_zip["lifeline_id"].isin(zip_ids) & (h_zip["_city"] != "")]
    if "_city_norm" in cms_m.columns and len(h_city):
        tier2b = h_city.merge(
            cms_m, left_on=["_state", "_city"], right_on=["_state", "_city_norm"], how="inner"
        )
    else:
        tier2b = pd.DataFrame()

    # ── Tier 2c: state only ───────────────────────────────────────────────
    city_ids = zip_ids | (set(tier2b["lifeline_id"]) if len(tier2b) else set())
    h_state = health_m[~health_m["lifeline_id"].isin(city_ids) & (health_m["_state"] != "")]
    tier2c = h_state.merge(cms_m, on="_state", how="inner") if len(h_state) else pd.DataFrame()

    candidates = pd.concat([tier2a, tier2b, tier2c], ignore_index=True)
    if len(candidates) == 0:
        return []

    cms_names = candidates["_cms_name"].fillna("").tolist()
    health_names = candidates["_name_norm"].tolist()
    candidates["_score"] = [
        fuzz.token_sort_ratio(a, b) / 100.0
        for a, b in zip(health_names, cms_names)
    ]

    candidates = candidates[candidates["_score"] >= threshold]
    if len(candidates) == 0:
        return []

    best = (
        candidates
        .sort_values("_score", ascending=False)
        .drop_duplicates("lifeline_id")
        .reset_index(drop=True)
    )

    results: list[dict] = []
    # Need full CMS row for _cnt columns — merge best back to full cms
    best_prvdr = best["PRVDR_NUM"].tolist() if "PRVDR_NUM" in best.columns else [""] * len(best)
    prvdr_to_row = {}
    if "PRVDR_NUM" in cms.columns:
        for _, row in cms.iterrows():
            pn = str(row.get("PRVDR_NUM", "") or "")
            if pn and pn not in prvdr_to_row:
                prvdr_to_row[pn] = row

    for i, brow in best.iterrows():
        lid = brow["lifeline_id"]
        score = float(brow["_score"])
        prvdr_num = str(brow.get("PRVDR_NUM", "") or "")
        full_row = prvdr_to_row.get(prvdr_num, brow)
        results.append(_build_result_row(lid, full_row, score, "zip_fuzzy", None, cnt_cols))

    return results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_attr_health_cms(
    silver_path: Path,
    bronze_path: Path,
    threshold: float = 0.80,
    spatial_distance_m: float = 200.0,
    spatial_name_threshold: float = 0.55,
) -> pd.DataFrame:
    """
    Match CMS hospital providers to silver health POIs and return a
    ``lifeline_id``-keyed attribute DataFrame ready to write as
    ``silver/attr_health_cms.parquet``.

    Uses a two-tier strategy:
    * **Tier 1** — BallTree spatial match on CMS ``geocoded_lat/lon`` within
      ``spatial_distance_m`` metres, guarded by ``spatial_name_threshold``.
    * **Tier 2** — state + ZIP + rapidfuzz name match for unmatched POIs,
      minimum score ``threshold``.

    Parameters
    ----------
    silver_path:
        Path to the silver data directory.
    bronze_path:
        Path to the bronze data directory.
    threshold:
        Minimum rapidfuzz ``token_sort_ratio`` score (0–1) for Tier 2.
    spatial_distance_m:
        BallTree search radius in metres for Tier 1.
    spatial_name_threshold:
        Minimum fuzzy name score (0–1) required to accept a Tier 1 spatial match.

    Returns
    -------
    DataFrame with columns:
        lifeline_id, cms_provider_num, cms_match_score, cms_match_method,
        cms_match_distance_m, cms_bed_cnt, cms_certified_bed_cnt,
        cms_operating_rooms, cms_<all_other_cnt_fields>...
    """
    silver_path = Path(silver_path)
    bronze_path = Path(bronze_path)

    _empty_cols = [
        "lifeline_id", "cms_provider_num", "cms_match_score",
        "cms_match_method", "cms_match_distance_m",
        "cms_bed_cnt", "cms_certified_bed_cnt", "cms_operating_rooms",
    ]

    # --- Load silver health points ---
    master_file = silver_path / "lifeline_points.parquet"
    if not master_file.exists():
        return pd.DataFrame(columns=_empty_cols)

    import geopandas as gpd

    master = gpd.read_parquet(master_file)
    if "tmp_osm_layer" in master.columns:
        health = master[master["tmp_osm_layer"] == "health"].copy()
    else:
        attr_file = silver_path / "attr_health.parquet"
        if attr_file.exists():
            attr = pd.read_parquet(attr_file, columns=["lifeline_id"])
            health = master[master["lifeline_id"].isin(attr["lifeline_id"])].copy()
        else:
            health = master.copy()

    if len(health) == 0:
        return pd.DataFrame(columns=_empty_cols)

    # Build lookup columns on health points
    for col in ("addr:state", "addr:postcode", "addr:city", "name", "display_name"):
        if col not in health.columns:
            health[col] = ""
        else:
            health[col] = health[col].fillna("").astype(str).str.strip()

    health["_zip5"] = health["addr:postcode"].str[:5]
    health["_state"] = health["addr:state"].str.upper().str[:2]
    health["_city"] = health["addr:city"].str.upper()
    health["_name_norm"] = health["name"].apply(_normalize_name)
    _empty_name = health["_name_norm"] == ""
    health.loc[_empty_name, "_name_norm"] = health.loc[_empty_name, "display_name"].apply(_normalize_name)

    # --- Load CMS providers ---
    cms = load_cms_providers(bronze_path)
    if len(cms) == 0:
        return pd.DataFrame(columns=_empty_cols)

    cnt_cols = _detect_cnt_columns(cms)

    # --- Tier 1: Spatial ---
    tier1_results, matched_ids = _tier1_spatial(
        health, cms, spatial_distance_m, spatial_name_threshold, cnt_cols
    )
    print(f"    CMS Tier 1 (spatial):   {len(tier1_results):,} matches")

    # --- Tier 2: ZIP + fuzzy name ---
    tier2_results = _tier2_zip_fuzzy(health, cms, threshold, cnt_cols, matched_ids)
    print(f"    CMS Tier 2 (zip+fuzzy): {len(tier2_results):,} matches")

    all_results = tier1_results + tier2_results
    if not all_results:
        return pd.DataFrame(columns=_empty_cols)

    attr = pd.DataFrame(all_results)
    # Guard against duplicate lifeline_ids (shouldn't normally occur)
    attr = (
        attr.sort_values("cms_match_score", ascending=False)
        .drop_duplicates("lifeline_id")
        .reset_index(drop=True)
    )
    return attr

