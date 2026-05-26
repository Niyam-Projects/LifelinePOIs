"""
CMS Hospital & Non-Hospital Provider Info enrichment for LifelinePOI.

Matches CMS provider records (bronze) against silver health POIs to produce
``silver/attr_health_cms.parquet``, a supplemental attribute table containing
authoritative staffing/capacity counts and related provider metrics.

Matching strategy (four tiers):

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
    Silver health POIs are first enriched with OSM address fields (postcode,
    state, city, housenumber) from ``silver/attr_health.parquet`` via a
    ``lifeline_id`` join.  ZIP codes absent after that join are extracted from
    the free-text ``display_name`` field (5-digit pattern).  Then a vectorized
    tiered-merge is run:
    * state + ZIP  → tightest candidates
    * ZIP only     → for POIs where state is absent but ZIP is known
    * state + city → city fallback for zip-miss POIs
    * state only   → last resort
    Fuzzy ``token_sort_ratio`` scoring over all candidate pairs; best-score-
    wins per POI; minimum ``name_similarity_threshold`` (default 0.80).

  **Tier 3 — Address number + ZIP**
    For POIs not matched in Tier 1 or 2 that have both a numeric house-number
    and a ZIP code: inner-join on (``_zip5``, ``_addr_num``) against CMS
    records.  House numbers are extracted from ``addr:housenumber`` first, then
    fall back to parsing ``display_name`` (e.g. ",\\s*(917) Ave …") and PR
    highway references (``PR-135`` → ``135``).  CMS highway addresses
    (``CARRETERA 135``, ``CARR. 2``) are similarly parsed.  Pairs are scored by
    ``token_sort_ratio``; best-score-wins per POI; minimum
    ``addr_name_threshold`` (default 0.50).  Method recorded as ``"addr_zip"``.

  **Tier 4 — ZIP + set-fuzzy name**
    Final catch-all for POIs still unmatched after Tiers 1–3 that have a
    known ZIP code.  Uses ``token_set_ratio`` (superior for subset/superset
    name relationships — e.g. "Hospital General Menonita de Caguas" vs
    "HOSPITAL MENONITA CAGUAS INC") with a ZIP-only geographic filter.
    Minimum ``name_zip_threshold`` (default 0.75).  Method recorded as
    ``"name_zip"``.

Name normalisation
------------------
``_normalize_name`` strips accented characters (NFKD → ASCII), lowercases,
removes punctuation, expands common abbreviations, strips Spanish filler
prepositions/articles (de, del, la, el, los, las), removes trailing "inc",
and strips "hospital" from the start or end of the normalised token sequence.

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
import unicodedata
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd


# Regex to extract a 2-letter US state abbreviation from a Nominatim display_name.
# Matches patterns like "San Juan, PR 00926" or "Ponce, PR 00733".
_STATE_IN_DISPLAY_RE = re.compile(r"\b([A-Z]{2})\s+\d{5}\b")

# Minimum token_set_ratio for the state-from-display_name Tier 2d sub-tier.
# High threshold (0.90) required because state-only is a broad geographic filter.
_TIER2D_SET_THRESHOLD = 0.90

# Columns that receive special backward-compat names in the output
_COMPAT_RENAMES: dict[str, str] = {
    "BED_CNT": "cms_bed_cnt",
    "CRTFD_BED_CNT": "cms_certified_bed_cnt",
    "OPRTG_ROOM_CNT": "cms_operating_rooms",
}


def _normalize_name(name: str) -> str:
    """Normalize hospital names for fuzzy matching.
    - Strip accented characters to ASCII (NFKD decomposition)
    - Lowercase, strip punctuation, collapse whitespace
    - Remove Spanish filler prepositions/articles (de, del, la, el, los, las)
    - Remove trailing 'INC'
    - Expand abbreviations (e.g., 'CTR' → 'Center')
    - Strip 'Hospital' from start/end
    """
    if not name or not isinstance(name, str):
        return ""
    # Decompose accented characters to ASCII (e.g. "Bayamón" → "Bayamon")
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    name = name.lower()
    name = re.sub(r"[^a-z0-9 ]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    # Remove trailing 'inc'
    name = re.sub(r" inc$", "", name)
    # Expand abbreviations — "st" → "saint" standardises "ST LUCIE" ↔ "Saint Lucie",
    # "St. Mary's" ↔ "Saint Marys", etc., where "st" is always a saint prefix in
    # US hospital names.
    abbr = {"ctr": "center", "med": "medical", "univ": "university", "dept": "department",
            "st": "saint", "hosp": "hospital", "mun": "municipal", "munic": "municipal"}
    # Remove Spanish filler prepositions/articles common in hospital names
    _stopwords = {"de", "del", "la", "el", "los", "las"}
    words = [abbr.get(w, w) for w in name.split() if w not in _stopwords]
    name = " ".join(words)
    # Remove/reorder 'hospital' at start/end
    if name.startswith("hospital "):
        name = name[len("hospital "):]
    # Strip combined suffix " hospital and medical center" before individual terms.
    # Keep standalone " medical center" intact — it's often a unique identifier.
    if name.endswith(" hospital and medical center"):
        name = name[: -len(" hospital and medical center")]
    elif name.endswith(" hospital and medical centre"):
        name = name[: -len(" hospital and medical centre")]
    elif name.endswith(" hospital"):
        name = name[: -len(" hospital")]
    # Strip common health-system suffixes so "Tallahassee Memorial HealthCare"
    # normalizes to the same root as "Tallahassee Memorial Hospital".
    for _suffix in (" healthcare", " health care", " health system", " health sciences", " health science"):
        if name.endswith(_suffix):
            name = name[: -len(_suffix)]
            break
    name = name.strip()
    return name


# Organisation-prefix patterns stripped when rescoring ZIP-constrained candidates.
# Many US hospital chains (HCA Florida, AdventHealth / former Florida Hospital)
# rebrand facilities without OSM being updated, leaving name mismatches that
# standard fuzzy scoring can't bridge.  Stripping these prefixes is safe only
# within a single ZIP code where the remaining location token is unique.
_BRAND_PREFIXES: tuple[str, ...] = (
    "hca florida ",
    "hca healthcare ",
    "hca ",
    "adventhealth by florida hospital ",
    "adventhealth ",
    "florida hospital ",
    "orlando health ",
    "baycare ",
    "uf health ",
    "university of florida health ",
)


def _strip_brand(n: str) -> str:
    """Strip a known org/chain prefix from a normalised hospital name.

    Returns the original string unchanged if no prefix matches or if stripping
    would leave an empty string (e.g. the name *is* the brand alone).
    """
    for p in _BRAND_PREFIXES:
        if n.startswith(p):
            result = n[len(p):]
            return result if result else n
    return n


def _apply_adj_penalty(raw_scores: list[float], df: "pd.DataFrame", penalty: float = 0.75) -> list[float]:
    """Apply 0-bed category-01 penalty to raw fuzzy scores for pre-filter use.

    Category-01 (short-term general hospital) records with zero beds are
    typically historical/closed records that should lose to active beds>0
    records.  Penalising them here prevents them from blocking fallthrough to
    broader tiers (e.g. tier2c state-level) when a better match exists there.
    """
    is_hosp = (
        df["PRVDR_CTGRY_CD"].fillna("").astype(str).eq("01").tolist()
        if "PRVDR_CTGRY_CD" in df.columns
        else [False] * len(df)
    )
    beds = (
        pd.to_numeric(df["BED_CNT"], errors="coerce").fillna(0).tolist()
        if "BED_CNT" in df.columns
        else [0.0] * len(df)
    )
    return [
        s * (penalty if h and b == 0 else 1.0)
        for s, h, b in zip(raw_scores, is_hosp, beds)
    ]


def _build_prvdr_to_row(cms: "pd.DataFrame") -> dict:
    """Build a PRVDR_NUM → full CMS row lookup.

    When the same provider number appears in multiple rows (e.g. a current and a
    historical record), keep the row with the *highest* BED_CNT so that a
    correctly-matched provider is not assigned zero beds just because an
    older zero-bed record happened to appear first in the file.
    """
    prvdr_to_row: dict = {}
    if "PRVDR_NUM" not in cms.columns:
        return prvdr_to_row
    for _, row in cms.iterrows():
        pn = str(row.get("PRVDR_NUM", "") or "")
        if not pn:
            continue
        existing = prvdr_to_row.get(pn)
        if existing is None:
            prvdr_to_row[pn] = row
        else:
            new_beds = pd.to_numeric(row.get("BED_CNT"), errors="coerce")
            old_beds = pd.to_numeric(existing.get("BED_CNT"), errors="coerce")
            if pd.notna(new_beds) and (pd.isna(old_beds) or new_beds > old_beds):
                prvdr_to_row[pn] = row
    return prvdr_to_row


def _detect_cnt_columns(df: pd.DataFrame) -> list[str]:
    """Return all columns in *df* whose upper-case name ends with ``_CNT``."""
    return [c for c in df.columns if c.upper().endswith("_CNT")]


def load_cms_providers(bronze_path: Path) -> pd.DataFrame:
    """
    Load CMS provider parquet from bronze and return a filtered DataFrame
    containing all provider records (no category filter applied — all providers
    with bed counts are eligible for matching).

    Includes ``geocoded_lat`` / ``geocoded_lon`` when present and coerces
    all ``_CNT`` columns to nullable ``Int64`` to preserve zeros while
    allowing missing values.

    Returns an empty DataFrame if the file does not exist.
    """
    cms_file = Path(bronze_path) / "cms" / "cms_hospital_providers.parquet"
    if not cms_file.exists():
        return pd.DataFrame()

    df = pd.read_parquet(cms_file)

    # Normalise key string fields for matching
    for col in ("PRVDR_NUM", "FAC_NAME", "ST_ADR", "CITY_NAME", "STATE_CD", "ZIP_CD"):
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip()

    if "ZIP_CD" in df.columns:
        df["_zip5"] = df["ZIP_CD"].str[:5]

    if "FAC_NAME" in df.columns:
        df["_name_norm"] = df["FAC_NAME"].apply(_normalize_name)

    # Extract leading numeric house number from street address for Tier 3
    if "ST_ADR" in df.columns:
        df["_addr_num"] = df["ST_ADR"].str.extract(r"^(\d+)")[0].fillna("")
        # Fallback: highway-style addresses — CARRETERA 135, CARR. 2, PR-135
        _carr_mask = df["_addr_num"] == ""
        if _carr_mask.any():
            _carr_nums = df.loc[_carr_mask, "ST_ADR"].str.extract(
                r"(?i)(?:CARR\.?\s+|CARRETERA\s+|PR-)(\d{2,4})"
            )[0].fillna("")
            df.loc[_carr_mask, "_addr_num"] = _carr_nums

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
        "cms_provider_category": str(best_row.get("PRVDR_CTGRY_CD", "") or ""),
        "cms_provider_subtype": str(best_row.get("PRVDR_CTGRY_SBTYP_CD", "") or ""),
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
    already_matched: "set[str] | frozenset[str]" = frozenset(),
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

    k = min(10, len(cms_geo))
    tree = BallTree(cms_coords, metric="euclidean")
    distances, indices = tree.query(health_coords, k=k)
    # distances/indices shape: (n_health, k)

    results: list[dict] = []
    matched_ids: set[str] = set()

    cms_geo_reset = cms_geo.reset_index(drop=True)
    lids = health_with_geom["lifeline_id"].tolist()
    names = health_with_geom["_name_norm"].tolist()

    for i, (lid, h_name) in enumerate(zip(lids, names)):
        if lid in already_matched:
            continue
        if not h_name:
            continue
        best_score: float = -1.0
        best_candidate = None
        best_dist: float = 0.0

        for j in range(k):
            dist = float(distances[i, j])
            if dist > spatial_distance_m:
                break  # BallTree results are sorted by distance; no closer ones remain
            candidate = cms_geo_reset.iloc[int(indices[i, j])]
            cms_name = str(candidate.get("_name_norm", "") or "")
            # Use max of sort and set ratios so spatially-close pairs where the
            # OSM name has extra words (e.g. "aventura hospital and medical center"
            # vs "hca florida aventura") still pass the name-similarity gate.
            name_score = max(
                fuzz.token_sort_ratio(h_name, cms_name),
                fuzz.token_set_ratio(h_name, cms_name),
            ) / 100.0
            if name_score < spatial_name_threshold:
                continue
            # Tie-breaker: prefer higher name score, then general hospital category, then bed count
            is_hosp = int(str(candidate.get("PRVDR_CTGRY_CD", "") or "") == "01")
            beds = int(candidate.get("BED_CNT") or 0) if pd.notna(candidate.get("BED_CNT")) else 0
            if best_candidate is None or (
                name_score > best_score
                or (name_score == best_score and is_hosp > int(
                    str(best_candidate.get("PRVDR_CTGRY_CD", "") or "") == "01"
                ))
                or (name_score == best_score
                    and str(candidate.get("PRVDR_CTGRY_CD", "") or "") == str(best_candidate.get("PRVDR_CTGRY_CD", "") or "")
                    and beds > (int(best_candidate.get("BED_CNT") or 0) if pd.notna(best_candidate.get("BED_CNT")) else 0))
            ):
                best_score = name_score
                best_candidate = candidate
                best_dist = dist

        if best_candidate is not None:
            results.append(_build_result_row(lid, best_candidate, best_score, "spatial", best_dist, cnt_cols))
            matched_ids.add(lid)

    return results, matched_ids


# ---------------------------------------------------------------------------
# DuckDB-backed state-only join helper
# ---------------------------------------------------------------------------

def _duckdb_fuzzy_state_join(
    left: "pd.DataFrame",
    right: "pd.DataFrame",
    left_state_col: str,
    right_state_col: str,
    left_name_col: str,
    right_name_col: str,
    threshold: float = 0.80,
    scorer: str = "token_sort_ratio",
) -> "pd.DataFrame":
    """Inner join on a state column with a rapidfuzz name-similarity filter
    executed entirely inside DuckDB to avoid materialising a huge Cartesian
    product in pandas memory.

    Semantically equivalent to::

        left.merge(right, left_on=left_state_col, right_on=right_state_col,
                   how="inner")
        # followed by filtering on fuzz.<scorer>(name_col) >= threshold

    DuckDB applies the Python UDF as part of the join evaluation so that
    only candidate pairs that pass ``threshold`` are ever returned to Python.
    This keeps peak memory proportional to the *match count* rather than the
    full Cartesian product (which can reach 80 M+ rows for Florida/PR data).

    ``scorer`` must be one of ``"token_sort_ratio"`` or ``"token_set_ratio"``.
    The caller may still re-score the returned DataFrame for tiebreaking —
    the pre-filter is exact so no valid matches are dropped.

    Column-collision handling mirrors ``pd.DataFrame.merge``:

    * When ``left_state_col == right_state_col`` the join key is kept once
      (from the left side) and dropped from the right.
    * Any other columns present in both DataFrames are suffixed ``_x``
      (left) and ``_y`` (right).
    """
    import duckdb
    from rapidfuzz import fuzz as _fuzz

    if scorer == "max_sort_set":
        def _scorer_fn(a: str, b: str) -> float:
            return max(_fuzz.token_sort_ratio(a, b), _fuzz.token_set_ratio(a, b))
    else:
        _scorer_fn = getattr(_fuzz, scorer)

    # Tiny sentinel tables — DuckDB only needs the name + state + row index.
    left_mini = pd.DataFrame({
        "__lidx": range(len(left)),
        "__lname": left[left_name_col].fillna("").astype(str).values,
        "__lstate": left[left_state_col].fillna("").astype(str).values,
    })
    right_mini = pd.DataFrame({
        "__ridx": range(len(right)),
        "__rname": right[right_name_col].fillna("").astype(str).values,
        "__rstate": right[right_state_col].fillna("").astype(str).values,
    })

    con = duckdb.connect()
    con.register("__lmini", left_mini)
    con.register("__rmini", right_mini)
    # Register the rapidfuzz scorer as a DuckDB Python UDF so the filter is
    # evaluated inside DuckDB's join loop — no 80 M-row intermediate table.
    con.create_function(
        "__fuzzy_score",
        lambda a, b: _scorer_fn(str(a), str(b)) / 100.0,
        [str, str],
        float,
    )
    try:
        pairs = con.execute(
            f"""
            SELECT l.__lidx AS _l, r.__ridx AS _r
            FROM __lmini l
            INNER JOIN __rmini r ON l.__lstate = r.__rstate
            WHERE __fuzzy_score(l.__lname, r.__rname) >= {threshold:.6f}
            """
        ).fetchdf()
    finally:
        con.close()

    if len(pairs) == 0:
        return pd.DataFrame()

    left_sel = left.iloc[pairs["_l"].values].reset_index(drop=True)
    right_sel = right.iloc[pairs["_r"].values].reset_index(drop=True)

    # Drop the join key from right when both sides share the same column name.
    if left_state_col == right_state_col:
        right_sel = right_sel.drop(columns=[right_state_col], errors="ignore")

    # Rename colliding columns (_x / _y) to match pandas merge behaviour.
    collision_cols = sorted(set(left_sel.columns) & set(right_sel.columns))
    if collision_cols:
        left_sel = left_sel.rename(columns={c: f"{c}_x" for c in collision_cols})
        right_sel = right_sel.rename(columns={c: f"{c}_y" for c in collision_cols})

    return pd.concat([left_sel, right_sel], axis=1).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Tier 2: ZIP + fuzzy name (vectorized tiered merge, existing logic)
# ---------------------------------------------------------------------------

def _tier2_zip_fuzzy(
    health: pd.DataFrame,
    cms: pd.DataFrame,
    threshold: float,
    cnt_cols: list[str],
    already_matched: set[str],
    cms_already_matched: "frozenset[str] | set[str]" = frozenset(),
) -> list[dict]:
    """
    Match unmatched silver health POIs to CMS records using state+ZIP+name.

    Returns a results list.
    """
    try:
        from rapidfuzz import fuzz
    except ImportError as exc:
        raise ImportError("rapidfuzz is required: pip install rapidfuzz") from exc

    # Exclude CMS providers already matched in an earlier tier
    if cms_already_matched and "PRVDR_NUM" in cms.columns:
        cms = cms[~cms["PRVDR_NUM"].isin(cms_already_matched)].copy()

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
        c for c in cnt_cols + ["PRVDR_NUM", "PRVDR_CTGRY_CD"] if c in cms_m.columns
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

    # Pre-score Tier 2a to identify hospitals that ACTUALLY matched above threshold.
    # Hospitals that had ZIP candidates but no match above threshold should still
    # fall through to city/state-only tiers (e.g. Halifax Health: zip 32117 vs CMS 32114).
    if len(tier2a) > 0:
        _t2a_scores = [
            max(fuzz.token_sort_ratio(str(a), str(b)), fuzz.token_set_ratio(str(a), str(b))) / 100.0
            for a, b in zip(tier2a["_name_norm"].tolist(), tier2a["_cms_name"].fillna("").tolist())
        ]
        _t2a_adj = _apply_adj_penalty(_t2a_scores, tier2a)
        zip_matched_ids = set(tier2a.loc[[s >= threshold for s in _t2a_adj], "lifeline_id"])
    else:
        zip_matched_ids = set()
    # zip_ids tracks all hospitals that had ZIP+state candidates (for dedup only)
    zip_ids = set(tier2a["lifeline_id"]) if len(tier2a) else set()

    # ── Tier 2a': ZIP only (for POIs where _state is empty) ──────────────
    h_zip_nostate = h_zip[~h_zip["lifeline_id"].isin(zip_ids) & (h_zip["_state"] == "")]
    zip_only_cols = [c for c in ["_zip5", "_cms_name", "PRVDR_NUM"] + [
        c for c in cms_m.columns if c not in ("_state", "_city_norm")
    ] if c in cms_m.columns]
    zip_only_cols = list(dict.fromkeys(zip_only_cols))
    tier2a_nostate = (
        h_zip_nostate.merge(cms_m[zip_only_cols], on="_zip5", how="inner")
        if len(h_zip_nostate)
        else pd.DataFrame()
    )
    if len(tier2a_nostate) > 0:
        _t2a_ns_scores = [
            max(fuzz.token_sort_ratio(str(a), str(b)), fuzz.token_set_ratio(str(a), str(b))) / 100.0
            for a, b in zip(tier2a_nostate["_name_norm"].tolist(), tier2a_nostate["_cms_name"].fillna("").tolist())
        ]
        _t2a_ns_adj = _apply_adj_penalty(_t2a_ns_scores, tier2a_nostate)
        zip_matched_ids |= set(tier2a_nostate.loc[[s >= threshold for s in _t2a_ns_adj], "lifeline_id"])
    zip_ids = zip_ids | (set(tier2a_nostate["lifeline_id"]) if len(tier2a_nostate) else set())

    # ── Tier 2b: state + city ─────────────────────────────────────────────
    # Run for hospitals that either had no ZIP candidates OR had ZIP candidates
    # but none scored above threshold (zip_ids minus zip_matched_ids).
    h_city_excl = zip_matched_ids  # only exclude hospitals that actually matched in 2a
    h_city = h_zip[~h_zip["lifeline_id"].isin(h_city_excl) & (h_zip["_city"] != "")]
    if "_city_norm" in cms_m.columns and len(h_city):
        tier2b = h_city.merge(
            cms_m, left_on=["_state", "_city"], right_on=["_state", "_city_norm"], how="inner"
        )
    else:
        tier2b = pd.DataFrame()

    # Pre-score Tier 2b to identify hospitals that ACTUALLY matched above threshold.
    # Like the ZIP fallthrough fix, hospitals with city candidates that don't score
    # above threshold (e.g. Morton Plant in "LUTZ" matching only irrelevant facilities)
    # must still fall through to the state-only tier.
    if len(tier2b) > 0:
        _t2b_scores = [
            max(fuzz.token_sort_ratio(str(a), str(b)), fuzz.token_set_ratio(str(a), str(b))) / 100.0
            for a, b in zip(tier2b["_name_norm"].tolist(), tier2b["_cms_name"].fillna("").tolist())
        ]
        _t2b_adj = _apply_adj_penalty(_t2b_scores, tier2b)
        city_matched_ids = zip_matched_ids | set(tier2b.loc[[s >= threshold for s in _t2b_adj], "lifeline_id"])
    else:
        city_matched_ids = zip_matched_ids

    # ── Tier 2c: state only ───────────────────────────────────────────────
    # Use DuckDB with a rapidfuzz UDF to avoid materialising the full
    # state-level Cartesian product in pandas memory (can be 80 M+ rows).
    # Include all unmatched hospitals with a known state — not just those
    # without ZIP candidates — so that ZIP-miss cases (hospital in CMS at a
    # different ZIP) still get a chance at the state-level name search.
    # Use max(token_sort_ratio, token_set_ratio) so subset-name pairs pass.
    h_state = health_m[~health_m["lifeline_id"].isin(city_matched_ids) & (health_m["_state"] != "")]
    tier2c = (
        _duckdb_fuzzy_state_join(
            h_state, cms_m,
            left_state_col="_state", right_state_col="_state",
            left_name_col="_name_norm", right_name_col="_cms_name",
            threshold=threshold, scorer="max_sort_set",
        )
        if len(h_state) else pd.DataFrame()
    )

    # Tag ZIP-constrained rows so the brand-rescore pass below can identify them.
    if len(tier2a) > 0:
        tier2a = tier2a.copy()
        tier2a["_from_zip"] = True
    if len(tier2a_nostate) > 0:
        tier2a_nostate = tier2a_nostate.copy()
        tier2a_nostate["_from_zip"] = True

    candidates = pd.concat([tier2a, tier2a_nostate, tier2b, tier2c], ignore_index=True)

    # Build prvdr_to_row lookup unconditionally (needed by both 2a–2c and Tier 2d)
    prvdr_to_row = _build_prvdr_to_row(cms)

    results: list[dict] = []

    if len(candidates) > 0:
        cms_names = candidates["_cms_name"].fillna("").tolist()
        health_names = candidates["_name_norm"].tolist()
        # Use max(token_sort_ratio, token_set_ratio) so subset-name cases like
        # "Tallahassee Memorial HealthCare" vs "Tallahassee Memorial" still score
        # highly even when the shorter string is a complete subset of the longer.
        candidates["_score"] = [
            max(fuzz.token_sort_ratio(a, b), fuzz.token_set_ratio(a, b)) / 100.0
            for a, b in zip(health_names, cms_names)
        ]

        # Brand-prefix-aware rescoring for ZIP-constrained candidates only.
        # When an OSM hospital name and its CMS counterpart differ only by a
        # chain-prefix rebranding (e.g. "JFK Medical Center" vs
        # "HCA FLORIDA JFK HOSPITAL", or "Florida Hospital DeLand" vs
        # "ADVENTHEALTH DELAND"), stripping the prefix before scoring can bridge
        # the gap.  We restrict this to ZIP-constrained rows (_from_zip=True)
        # because the stripped short names (e.g. "jfk", "deland") are only
        # unique within a single ZIP — applying brand-stripping at state level
        # would cause false positives across distant same-chain facilities.
        _from_zip_mask = candidates.get("_from_zip", pd.Series(False, index=candidates.index)).fillna(False).astype(bool)
        _below_mask = candidates["_score"] < threshold
        _rescore_mask = _from_zip_mask & _below_mask
        if _rescore_mask.any():
            _rh = candidates.loc[_rescore_mask, "_name_norm"].fillna("").apply(_strip_brand)
            _rc = candidates.loc[_rescore_mask, "_cms_name"].fillna("").apply(_strip_brand)
            _changed = (_rh != candidates.loc[_rescore_mask, "_name_norm"].fillna("")) | \
                       (_rc != candidates.loc[_rescore_mask, "_cms_name"].fillna(""))
            if _changed.any():
                _ci = _rh[_changed].index
                _brand_scores = pd.Series(
                    [max(fuzz.token_sort_ratio(a, b), fuzz.token_set_ratio(a, b)) / 100.0
                     for a, b in zip(_rh[_changed].tolist(), _rc[_changed].tolist())],
                    index=_ci,
                )
                candidates.loc[_ci, "_score"] = candidates.loc[_ci, "_score"].combine(_brand_scores, max)

        candidates = candidates[candidates["_score"] >= threshold]
        if len(candidates) > 0:
            candidates = candidates.copy()
            _cn = candidates["_cms_name"].fillna("").tolist()
            _hn = candidates["_name_norm"].tolist()
            candidates["_sort_score"] = [fuzz.token_set_ratio(a, b) / 100.0 for a, b in zip(_hn, _cn)]
            candidates["_is_hosp"] = (
                candidates["PRVDR_CTGRY_CD"].fillna("").astype(str).eq("01").astype(int)
                if "PRVDR_CTGRY_CD" in candidates.columns else 0
            )
            candidates["_beds"] = (
                pd.to_numeric(candidates["BED_CNT"], errors="coerce").fillna(0)
                if "BED_CNT" in candidates.columns else 0
            )
            # Penalise 0-bed category-01 (hospital) records so that when an old
            # zero-bed historical record and an active beds>0 record are both
            # candidates, the active record wins even at a slightly lower name score.
            # (e.g. "SACRED HEART HOSPITAL OF PENSACOLA, FLORIDA" (0 beds) vs
            # "ASCENSION SACRED HEART PENSACOLA" (547 beds) for the same facility)
            candidates["_score_adj"] = [
                score * (0.75 if is_hosp == 1 and beds == 0 else 1.0)
                for score, is_hosp, beds in zip(
                    candidates["_score"].tolist(),
                    candidates["_is_hosp"].tolist(),
                    candidates["_beds"].tolist(),
                )
            ]
            candidates = candidates[candidates["_score_adj"] >= threshold]
        if len(candidates) > 0:
            best = (
                candidates
                .sort_values(["_score_adj", "_is_hosp", "_sort_score", "_beds"],
                             ascending=[False, False, False, False])
                .drop_duplicates("lifeline_id")
                .reset_index(drop=True)
            )
            for i, brow in best.iterrows():
                lid = brow["lifeline_id"]
                score = float(brow["_score"])
                prvdr_num = str(brow.get("PRVDR_NUM", "") or "")
                full_row = prvdr_to_row.get(prvdr_num, brow)
                results.append(_build_result_row(lid, full_row, score, "zip_fuzzy", None, cnt_cols))

    # ── Tier 2d: state extracted from display_name ────────────────────────
    # Fallback for POIs with _state="" that were missed by all earlier sub-tiers
    # because the OSM record lacks addr:state (common in Puerto Rico).
    # Uses token_set_ratio >= 0.90 for high precision — state-only is a broad filter.
    if "display_name" in health_m.columns:
        tier2d_matched = already_matched | {r["lifeline_id"] for r in results}
        h_2d = health_m[
            ~health_m["lifeline_id"].isin(tier2d_matched)
            & (health_m["_state"] == "")
        ].copy()
        if len(h_2d) > 0:
            h_2d["_extracted_state"] = (
                h_2d["display_name"].fillna("").astype(str).apply(
                    lambda dn: (m.group(1) if (m := _STATE_IN_DISPLAY_RE.search(dn)) else "")
                )
            )
            h_2d = h_2d[h_2d["_extracted_state"] != ""]
        if len(h_2d) > 0:
            tier2d_candidates = _duckdb_fuzzy_state_join(
                    h_2d, cms_m,
                    left_state_col="_extracted_state", right_state_col="_state",
                    left_name_col="_name_norm", right_name_col="_cms_name",
                    threshold=_TIER2D_SET_THRESHOLD, scorer="token_set_ratio",
                )
            if len(tier2d_candidates) > 0:
                tier2d_candidates = tier2d_candidates.copy()
                _t2d_hn = tier2d_candidates["_name_norm"].fillna("").tolist()
                _t2d_cn = tier2d_candidates["_cms_name"].fillna("").tolist()
                tier2d_candidates["_score"] = [
                    fuzz.token_set_ratio(str(a), str(b)) / 100.0
                    for a, b in zip(_t2d_hn, _t2d_cn)
                ]
                tier2d_candidates = tier2d_candidates[
                    tier2d_candidates["_score"] >= _TIER2D_SET_THRESHOLD
                ]
            if len(tier2d_candidates) > 0:
                tier2d_candidates = tier2d_candidates.copy()
                _t2d_hn2 = tier2d_candidates["_name_norm"].fillna("").tolist()
                _t2d_cn2 = tier2d_candidates["_cms_name"].fillna("").tolist()
                tier2d_candidates["_sort_score"] = [
                    fuzz.token_sort_ratio(str(a), str(b)) / 100.0
                    for a, b in zip(_t2d_hn2, _t2d_cn2)
                ]
                tier2d_candidates["_is_hosp"] = (
                    tier2d_candidates["PRVDR_CTGRY_CD"].fillna("").astype(str).eq("01").astype(int)
                    if "PRVDR_CTGRY_CD" in tier2d_candidates.columns else 0
                )
                tier2d_candidates["_beds"] = (
                    pd.to_numeric(tier2d_candidates["BED_CNT"], errors="coerce").fillna(0)
                    if "BED_CNT" in tier2d_candidates.columns else 0
                )
                tier2d_best = (
                    tier2d_candidates
                    .sort_values(["_score", "_sort_score", "_is_hosp", "_beds"],
                                 ascending=[False, False, False, False])
                    .drop_duplicates("lifeline_id")
                    .reset_index(drop=True)
                )
                for _, brow in tier2d_best.iterrows():
                    lid = brow["lifeline_id"]
                    score = float(brow["_score"])
                    prvdr_num = str(brow.get("PRVDR_NUM", "") or "")
                    full_row = prvdr_to_row.get(prvdr_num, brow)
                    results.append(
                        _build_result_row(lid, full_row, score, "zip_fuzzy", None, cnt_cols)
                    )

    # ── Tier 2e: state + city with token_set_ratio ────────────────────────
    # Fallback for POIs whose name is a token-subset of the CMS name (e.g.
    # "auxilio mutuo" ⊂ "auxilio mutuo hosp transplant").  token_sort_ratio
    # fails because extra CMS tokens inflate edit distance; token_set_ratio
    # scores the intersection correctly.  State + city is a strong enough
    # geographic anchor to justify this scorer.
    if "_city_norm" in cms_m.columns:
        tier2e_matched = already_matched | {r["lifeline_id"] for r in results}
        h_2e = health_m[
            ~health_m["lifeline_id"].isin(tier2e_matched)
            & (health_m["_state"] != "")
            & (health_m["_city"] != "")
        ].copy()
        if len(h_2e) > 0:
            tier2e_candidates = h_2e.merge(
                cms_m, left_on=["_state", "_city"], right_on=["_state", "_city_norm"], how="inner"
            )
            if len(tier2e_candidates) > 0:
                tier2e_candidates = tier2e_candidates.copy()
                tier2e_candidates["_score"] = [
                    fuzz.token_set_ratio(str(a), str(b)) / 100.0
                    for a, b in zip(
                        tier2e_candidates["_name_norm"].fillna("").tolist(),
                        tier2e_candidates["_cms_name"].fillna("").tolist(),
                    )
                ]
                tier2e_candidates = tier2e_candidates[
                    tier2e_candidates["_score"] >= threshold
                ]
            if len(tier2e_candidates) > 0:
                tier2e_candidates = tier2e_candidates.copy()
                _t2e_hn = tier2e_candidates["_name_norm"].fillna("").tolist()
                _t2e_cn = tier2e_candidates["_cms_name"].fillna("").tolist()
                tier2e_candidates["_sort_score"] = [
                    fuzz.token_sort_ratio(str(a), str(b)) / 100.0
                    for a, b in zip(_t2e_hn, _t2e_cn)
                ]
                tier2e_candidates["_is_hosp"] = (
                    tier2e_candidates["PRVDR_CTGRY_CD"].fillna("").astype(str).eq("01").astype(int)
                    if "PRVDR_CTGRY_CD" in tier2e_candidates.columns else 0
                )
                tier2e_candidates["_beds"] = (
                    pd.to_numeric(tier2e_candidates["BED_CNT"], errors="coerce").fillna(0)
                    if "BED_CNT" in tier2e_candidates.columns else 0
                )
                tier2e_best = (
                    tier2e_candidates
                    .sort_values(["_score", "_sort_score", "_is_hosp", "_beds"],
                                 ascending=[False, False, False, False])
                    .drop_duplicates("lifeline_id")
                    .reset_index(drop=True)
                )
                for _, brow in tier2e_best.iterrows():
                    lid = brow["lifeline_id"]
                    score = float(brow["_score"])
                    prvdr_num = str(brow.get("PRVDR_NUM", "") or "")
                    full_row = prvdr_to_row.get(prvdr_num, brow)
                    results.append(
                        _build_result_row(lid, full_row, score, "zip_fuzzy", None, cnt_cols)
                    )

    return results


# ---------------------------------------------------------------------------
# Address enrichment: recover OSM addr fields via lifeline_id join
# ---------------------------------------------------------------------------

def _enrich_health_addr(health: "pd.DataFrame", silver_path: Path) -> "pd.DataFrame":
    """
    Re-attach OSM address fields to silver health POIs by joining
    ``silver/attr_health.parquet`` on ``lifeline_id``.

    The silver conflation pipeline strips raw OSM tags from the master
    ``lifeline_points.parquet`` but preserves them in ``attr_health.parquet``,
    keyed by ``lifeline_id``.  This function merges those addr columns back so
    that Tier 2 (zip+fuzzy) and Tier 3 (addr_num+zip) can fire.

    Only fills fields that are currently empty; existing values are preserved.
    """
    attr_file = Path(silver_path) / "attr_health.parquet"
    if not attr_file.exists():
        return health

    addr_cols = ["addr:postcode", "addr:state", "addr:city", "addr:housenumber"]
    try:
        attr = pd.read_parquet(attr_file, columns=["lifeline_id"] + addr_cols)
    except Exception:
        return health

    for col in addr_cols:
        if col not in attr.columns:
            attr[col] = ""
        attr[col] = attr[col].fillna("").astype(str).str.strip()

    # Deduplicate attr by lifeline_id: prefer rows with more address fields filled in.
    # attr_health.parquet can have multiple rows per lifeline_id (e.g. campus sub-features
    # that were collapsed out of lifeline_points.parquet but kept in the attr table).
    # Without this, the left-merge below produces more rows than health, causing a
    # Boolean index length mismatch on the .loc[] assignment.
    _completeness = attr[addr_cols].apply(lambda s: s.str.len() > 0).sum(axis=1)
    attr = (
        attr.assign(_completeness=_completeness)
        .sort_values("_completeness", ascending=False)
        .drop_duplicates(subset="lifeline_id", keep="first")
        .drop(columns=["_completeness"])
        .reset_index(drop=True)
    )

    # Left-join: only update rows where current value is empty
    merged = health.merge(
        attr.rename(columns={c: f"_osm_{c}" for c in addr_cols}),
        on="lifeline_id",
        how="left",
    )

    for col in addr_cols:
        osm_col = f"_osm_{col}"
        if osm_col not in merged.columns:
            continue
        osm_vals = merged[osm_col].fillna("")
        current_empty = merged[col] == ""
        health.loc[current_empty.values, col] = osm_vals[current_empty].values

    return health


# ---------------------------------------------------------------------------
# Tier 3: address number + ZIP match
# ---------------------------------------------------------------------------

def _tier3_addr_zip(
    health: "pd.DataFrame",
    cms: "pd.DataFrame",
    cnt_cols: list[str],
    already_matched: set[str],
    addr_name_threshold: float = 0.50,
    cms_already_matched: "frozenset[str] | set[str]" = frozenset(),
) -> list[dict]:
    """
    Match unmatched silver health POIs to CMS records using ``addr:housenumber``
    (leading digits) + ZIP code, with a fuzzy name sanity check.

    Only fires for POIs that have both ``_addr_num`` and ``_zip5`` populated and
    for CMS records that have a numeric ``_addr_num`` extracted from ``ST_ADR``.

    Returns a results list in the same format as the other tier functions.
    """
    try:
        from rapidfuzz import fuzz
    except ImportError as exc:
        raise ImportError("rapidfuzz is required: pip install rapidfuzz") from exc

    # Exclude CMS providers already matched in an earlier tier
    if cms_already_matched and "PRVDR_NUM" in cms.columns:
        cms = cms[~cms["PRVDR_NUM"].isin(cms_already_matched)].copy()

    health_m = health[
        ~health["lifeline_id"].isin(already_matched)
        & (health["_addr_num"] != "")
        & (health["_zip5"].str.len() == 5)
    ].copy()

    cms_m = cms[cms.get("_addr_num", pd.Series("", index=cms.index)).astype(str) != ""].copy()
    if "_addr_num" not in cms_m.columns:
        return []

    if len(health_m) == 0 or len(cms_m) == 0:
        return []

    cms_renamed = cms_m.rename(columns={"_name_norm": "_cms_name"})
    keep_cols = [c for c in ["_zip5", "_addr_num", "_cms_name", "PRVDR_NUM", "PRVDR_CTGRY_CD"] + cnt_cols if c in cms_renamed.columns]
    cms_sub = cms_renamed[keep_cols].copy()

    # Primary join: addr_num + ZIP
    candidates_zip = health_m.merge(cms_sub, on=["_zip5", "_addr_num"], how="inner")
    if len(candidates_zip) > 0:
        candidates_zip = candidates_zip.copy()
        candidates_zip["_method"] = "addr_zip"

    # ── Tier 3b: addr_num + city (zip-tolerant) ───────────────────────────
    # For POIs with _city populated that were NOT matched by the primary join.
    primary_matched_lids: set[str] = (
        set(candidates_zip["lifeline_id"]) if len(candidates_zip) else set()
    )
    has_city = "_city" in health_m.columns and "CITY_NAME" in cms_renamed.columns
    if has_city:
        h_city_m = health_m[
            ~health_m["lifeline_id"].isin(primary_matched_lids)
            & (health_m["_city"].str.strip() != "")
        ].copy()
        if len(h_city_m) > 0:
            h_city_m["_city_upper"] = h_city_m["_city"].str.upper().str.strip()
            cms_city = cms_renamed[["_addr_num", "_cms_name", "PRVDR_NUM"]].copy()
            cms_city["_city_upper"] = cms_renamed["CITY_NAME"].str.upper().str.strip()
            candidates_city = h_city_m.merge(
                cms_city, on=["_addr_num", "_city_upper"], how="inner"
            )
            if len(candidates_city) > 0:
                candidates_city = candidates_city.copy()
                candidates_city["_method"] = "addr_city"
        else:
            candidates_city = pd.DataFrame()
    else:
        candidates_city = pd.DataFrame()

    candidates = pd.concat([candidates_zip, candidates_city], ignore_index=True)
    if len(candidates) == 0:
        return []

    candidates["_score"] = [
        fuzz.token_sort_ratio(str(a), str(b)) / 100.0
        for a, b in zip(
            candidates["_name_norm"].fillna("").tolist(),
            candidates["_cms_name"].fillna("").tolist(),
        )
    ]
    candidates = candidates[candidates["_score"] >= addr_name_threshold]
    if len(candidates) == 0:
        return []

    candidates = candidates.copy()
    _t3_hn = candidates["_name_norm"].fillna("").tolist()
    _t3_cn = candidates["_cms_name"].fillna("").tolist()
    candidates["_sort_score"] = [fuzz.token_set_ratio(str(a), str(b)) / 100.0 for a, b in zip(_t3_hn, _t3_cn)]
    candidates["_is_hosp"] = (
        candidates["PRVDR_CTGRY_CD"].fillna("").astype(str).eq("01").astype(int)
        if "PRVDR_CTGRY_CD" in candidates.columns else 0
    )
    candidates["_beds"] = (
        pd.to_numeric(candidates["BED_CNT"], errors="coerce").fillna(0)
        if "BED_CNT" in candidates.columns else 0
    )
    best = (
        candidates
        .sort_values(["_score", "_sort_score", "_is_hosp", "_beds"],
                     ascending=[False, False, False, False])
        .drop_duplicates("lifeline_id")
        .reset_index(drop=True)
    )

    prvdr_to_row = _build_prvdr_to_row(cms)

    results: list[dict] = []
    for _, brow in best.iterrows():
        lid = brow["lifeline_id"]
        score = float(brow["_score"])
        prvdr_num = str(brow.get("PRVDR_NUM", "") or "")
        full_row = prvdr_to_row.get(prvdr_num, brow)
        method = str(brow.get("_method", "addr_zip"))
        results.append(_build_result_row(lid, full_row, score, method, None, cnt_cols))

    return results


# ---------------------------------------------------------------------------
# Tier 4: ZIP + token_set_ratio name (permissive catch-all)
# ---------------------------------------------------------------------------

def _tier4_name_zip(
    health: "pd.DataFrame",
    cms: "pd.DataFrame",
    cnt_cols: list[str],
    already_matched: set[str],
    name_zip_threshold: float = 0.75,
    cms_already_matched: "frozenset[str] | set[str]" = frozenset(),
) -> list[dict]:
    """
    Tier 4 catch-all: ZIP + ``token_set_ratio`` name match for POIs not matched
    by Tiers 1–3.

    Uses ``token_set_ratio`` (better than ``token_sort_ratio`` for subset/
    superset name relationships such as "Hospital General Menonita de Caguas"
    vs "HOSPITAL MENONITA CAGUAS INC") with a ZIP-only geographic filter.
    Requires ``_zip5`` to be populated on both sides.

    Returns a results list with ``cms_match_method = "name_zip"``.
    """
    try:
        from rapidfuzz import fuzz
    except ImportError as exc:
        raise ImportError("rapidfuzz is required: pip install rapidfuzz") from exc

    # Exclude CMS providers already matched in an earlier tier
    if cms_already_matched and "PRVDR_NUM" in cms.columns:
        cms = cms[~cms["PRVDR_NUM"].isin(cms_already_matched)].copy()

    health_m = health[
        ~health["lifeline_id"].isin(already_matched)
        & (health["_name_norm"] != "")
        & (health["_zip5"].str.len() == 5)
    ].copy()

    if len(health_m) == 0 or len(cms) == 0:
        return []

    cms_m = cms[cms["_zip5"].str.len() == 5].copy()
    if len(cms_m) == 0:
        return []

    cms_m = cms_m.rename(columns={"_name_norm": "_cms_name"})
    keep_cols = [c for c in ["_zip5", "_cms_name", "PRVDR_NUM", "PRVDR_CTGRY_CD"] + cnt_cols if c in cms_m.columns]
    cms_sub = cms_m[list(dict.fromkeys(keep_cols))].copy()

    candidates = health_m.merge(cms_sub, on="_zip5", how="inner")
    if len(candidates) == 0:
        return []

    candidates["_score"] = [
        fuzz.token_set_ratio(str(a), str(b)) / 100.0
        for a, b in zip(
            candidates["_name_norm"].fillna("").tolist(),
            candidates["_cms_name"].fillna("").tolist(),
        )
    ]
    candidates = candidates[candidates["_score"] >= name_zip_threshold]
    if len(candidates) == 0:
        return []

    candidates = candidates.copy()
    _t4_hn = candidates["_name_norm"].fillna("").tolist()
    _t4_cn = candidates["_cms_name"].fillna("").tolist()
    candidates["_sort_score"] = [fuzz.token_sort_ratio(str(a), str(b)) / 100.0 for a, b in zip(_t4_hn, _t4_cn)]
    candidates["_is_hosp"] = (
        candidates["PRVDR_CTGRY_CD"].fillna("").astype(str).eq("01").astype(int)
        if "PRVDR_CTGRY_CD" in candidates.columns else 0
    )
    candidates["_beds"] = (
        pd.to_numeric(candidates["BED_CNT"], errors="coerce").fillna(0)
        if "BED_CNT" in candidates.columns else 0
    )
    best = (
        candidates
        .sort_values(["_score", "_sort_score", "_is_hosp", "_beds"],
                     ascending=[False, False, False, False])
        .drop_duplicates("lifeline_id")
        .reset_index(drop=True)
    )

    prvdr_to_row = _build_prvdr_to_row(cms)

    results: list[dict] = []
    for _, brow in best.iterrows():
        lid = brow["lifeline_id"]
        score = float(brow["_score"])
        prvdr_num = str(brow.get("PRVDR_NUM", "") or "")
        full_row = prvdr_to_row.get(prvdr_num, brow)
        results.append(_build_result_row(lid, full_row, score, "name_zip", None, cnt_cols))

    return results


# ---------------------------------------------------------------------------
# State resolution: fill missing _state via pygris Census boundaries
# ---------------------------------------------------------------------------

def _resolve_missing_states(health: "pd.DataFrame") -> "pd.DataFrame":
    """
    Dynamically resolve the US state/territory code for health POI rows where
    ``_state`` is currently empty.

    Uses ``pygris.states(cb=True)`` to fetch simplified Census state boundaries
    (cached after first call) and performs an ``intersects`` spatial join against
    the POI geometry to populate ``_state`` from the ``STUSPS`` column.

    Only rows with both ``_state == ""`` and a valid geometry are processed.
    Rows that still cannot be resolved are left as ``""``.

    Returns the mutated DataFrame in-place (also returned for convenience).
    """
    missing_mask = health["_state"] == ""
    if not missing_mask.any():
        return health

    try:
        import pygris  # noqa: PLC0415
        import geopandas as gpd  # noqa: PLC0415
    except ImportError:
        return health

    has_geom = missing_mask & health.geometry.notna()
    if not has_geom.any():
        return health

    try:
        states_gdf = pygris.states(cb=True, cache=True).to_crs("EPSG:4326")
    except Exception as exc:
        print(f"    [pygris] Could not fetch state boundaries: {exc}")
        return health

    missing_gdf = gpd.GeoDataFrame(
        health.loc[has_geom, ["lifeline_id"]].copy(),
        geometry=health.loc[has_geom, "geometry"].values,
        crs="EPSG:4326",
    )
    try:
        joined = gpd.sjoin(missing_gdf, states_gdf[["STUSPS", "geometry"]], how="left", predicate="intersects")
    except Exception as exc:
        print(f"    [pygris] Spatial join failed: {exc}")
        return health

    lid_to_state = (
        joined.dropna(subset=["STUSPS"])
        .drop_duplicates("lifeline_id")
        .set_index("lifeline_id")["STUSPS"]
        .to_dict()
    )
    if lid_to_state:
        health.loc[has_geom, "_state"] = (
            health.loc[has_geom, "lifeline_id"].map(lid_to_state).fillna("").values
        )
        resolved = sum(1 for v in lid_to_state.values() if v)
        print(f"    [pygris] Resolved {resolved:,} missing state values via Census boundaries")

    return health


# ---------------------------------------------------------------------------
# Tier 5: Census geocode unmatched CMS + spatial + token_set_ratio name
# ---------------------------------------------------------------------------

_CENSUS_BATCH_URL = "https://geocoding.geo.census.gov/geocoder/locations/addressbatch"
_CENSUS_CHUNK_SIZE = 9_999


def _geocode_cms_census(cms_ungeocoded: "pd.DataFrame") -> "dict[int, tuple[float, float]]":
    """
    Submit CMS rows to the US Census Bureau Batch Geocoding API.

    Returns a mapping of DataFrame integer index → (lon, lat) for matched rows.
    Only rows where ``STATE_CD`` is a US state (not US territories the Census API
    cannot handle) are submitted; the rest are silently skipped.

    If the API is unreachable or returns an error, logs a warning and returns an
    empty dict — Tier 5 degrades gracefully.
    """
    try:
        import httpx  # noqa: PLC0415
        import csv as _csv  # noqa: PLC0415
        import io as _io  # noqa: PLC0415
    except ImportError:
        return {}

    _TERRITORY_CODES = frozenset({"VI", "GU", "MP", "AS", "PR"})
    eligible_mask = ~cms_ungeocoded.get(
        "STATE_CD", pd.Series("", index=cms_ungeocoded.index)
    ).astype(str).str.strip().str.upper().isin(_TERRITORY_CODES)
    eligible = cms_ungeocoded[eligible_mask]

    if len(eligible) == 0:
        return {}

    all_indices = list(eligible.index)
    results: dict[int, tuple[float, float]] = {}

    for chunk_start in range(0, len(all_indices), _CENSUS_CHUNK_SIZE):
        chunk_indices = all_indices[chunk_start: chunk_start + _CENSUS_CHUNK_SIZE]
        chunk = eligible.loc[chunk_indices]

        buf = _io.StringIO()
        writer = _csv.writer(buf)
        for idx, row in chunk.iterrows():
            zip5 = str(row.get("ZIP_CD") or "").strip()[:5].zfill(5) if str(row.get("ZIP_CD") or "").strip() else ""
            writer.writerow([
                idx,
                str(row.get("ST_ADR") or "").strip(),
                str(row.get("CITY_NAME") or "").strip(),
                str(row.get("STATE_CD") or "").strip().upper(),
                zip5,
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
            print(f"    [Tier5/Census] Batch geocode request failed: {exc}")
            continue

        for row in _csv.reader(_io.StringIO(resp.text)):
            if len(row) < 3:
                continue
            try:
                row_idx = int(row[0])
            except ValueError:
                continue
            if row[2].strip() != "Match" or len(row) < 6:
                continue
            coords_str = row[5].strip()
            if not coords_str:
                continue
            try:
                lon_str, lat_str = coords_str.split(",", 1)
                results[row_idx] = (float(lon_str.strip()), float(lat_str.strip()))
            except ValueError:
                continue

    return results


def _tier5_census_spatial(
    health: "pd.DataFrame",
    cms: "pd.DataFrame",
    cnt_cols: list[str],
    already_matched: "set[str]",
    census_distance_m: float = 500.0,
    census_name_threshold: float = 0.70,
    cms_already_matched: "frozenset[str] | set[str]" = frozenset(),
) -> list[dict]:
    """
    Tier 5 catch-all: geocode unmatched CMS providers via Census Batch API,
    then spatial BallTree + ``token_set_ratio`` name match.

    Targets CMS records that lack valid ``geocoded_lat/lon`` (those skipped by
    Tier 1) and health POIs still unmatched after Tiers 1–4.  Uses a wider
    search radius (default 500 m) and a higher ``token_set_ratio`` threshold
    (default 0.70) to recover genuine matches that earlier tiers missed due to
    geocoding gaps.

    If the Census API is unreachable, returns an empty list (degrades gracefully).

    Returns a results list with ``cms_match_method = "census_spatial"``.
    """
    try:
        from rapidfuzz import fuzz  # noqa: PLC0415
        from sklearn.neighbors import BallTree  # noqa: PLC0415
        import geopandas as gpd  # noqa: PLC0415
    except ImportError as exc:
        raise ImportError("rapidfuzz, scikit-learn and geopandas are required") from exc

    # Exclude already-matched CMS providers
    if cms_already_matched and "PRVDR_NUM" in cms.columns:
        cms = cms[~cms["PRVDR_NUM"].isin(cms_already_matched)].copy()

    # Target: CMS providers WITHOUT valid geocoded coordinates
    geocoded_mask = (
        cms["geocoded_lat"].notna()
        & cms["geocoded_lon"].notna()
        & (cms.get("geocode_status", pd.Series("ok", index=cms.index)) == "ok")
    )
    cms_ungeocoded = cms[~geocoded_mask].copy()
    if len(cms_ungeocoded) == 0:
        return []

    # Geocode via Census API
    geocode_hits = _geocode_cms_census(cms_ungeocoded)
    if not geocode_hits:
        return []

    # Build GeoDataFrame from newly geocoded CMS points
    geocoded_rows = []
    for idx, (lon, lat) in geocode_hits.items():
        row = cms_ungeocoded.loc[idx].copy()
        row["_census_lon"] = lon
        row["_census_lat"] = lat
        geocoded_rows.append(row)

    cms_newly = pd.DataFrame(geocoded_rows).reset_index(drop=True)
    cms_newly_gdf = gpd.GeoDataFrame(
        cms_newly,
        geometry=gpd.points_from_xy(cms_newly["_census_lon"], cms_newly["_census_lat"]),
        crs="EPSG:4326",
    ).to_crs("EPSG:3857")

    # Health subset: unmatched POIs with valid geometry
    health_m = health[
        ~health["lifeline_id"].isin(already_matched)
        & health.geometry.notna()
        & (health["_name_norm"] != "")
    ].copy()
    if len(health_m) == 0:
        return []

    health_proj = health_m.copy()
    if hasattr(health_proj, "crs") and health_proj.crs is not None:
        health_proj = health_proj.to_crs("EPSG:3857")
    else:
        health_proj = gpd.GeoDataFrame(health_proj, geometry="geometry").set_crs("EPSG:4326").to_crs("EPSG:3857")

    def _xy(geom):
        if geom.geom_type == "Point":
            return geom.x, geom.y
        return geom.centroid.x, geom.centroid.y

    cms_coords = np.array([_xy(g) for g in cms_newly_gdf.geometry])
    health_coords = np.array([_xy(g) for g in health_proj.geometry])

    k = min(10, len(cms_newly_gdf))
    tree = BallTree(cms_coords, metric="euclidean")
    distances, indices = tree.query(health_coords, k=k)

    results: list[dict] = []
    matched_ids: set[str] = set()
    lids = health_m["lifeline_id"].tolist()
    names = health_m["_name_norm"].tolist()

    for i, (lid, h_name) in enumerate(zip(lids, names)):
        best_score: float = -1.0
        best_candidate = None
        best_dist: float = 0.0

        for j in range(k):
            dist = float(distances[i, j])
            if dist > census_distance_m:
                break
            candidate = cms_newly_gdf.iloc[int(indices[i, j])]
            cms_name = str(candidate.get("_name_norm", "") or "")
            name_score = fuzz.token_set_ratio(h_name, cms_name) / 100.0
            if name_score < census_name_threshold:
                continue
            is_hosp = int(str(candidate.get("PRVDR_CTGRY_CD", "") or "") == "01")
            beds = int(candidate.get("BED_CNT") or 0) if pd.notna(candidate.get("BED_CNT")) else 0
            best_is_hosp = int(str(best_candidate.get("PRVDR_CTGRY_CD", "") or "") == "01") if best_candidate is not None else 0
            best_beds = int(best_candidate.get("BED_CNT") or 0) if best_candidate is not None and pd.notna(best_candidate.get("BED_CNT")) else 0
            if best_candidate is None or name_score > best_score or (
                name_score == best_score and (is_hosp > best_is_hosp or (is_hosp == best_is_hosp and beds > best_beds))
            ):
                best_score = name_score
                best_candidate = candidate
                best_dist = dist

        if best_candidate is not None and lid not in matched_ids:
            results.append(_build_result_row(lid, best_candidate, best_score, "census_spatial", best_dist, cnt_cols))
            matched_ids.add(lid)

    return results


def _write_cms_match_state(
    cms: pd.DataFrame,
    matched: pd.DataFrame,
    silver_path: Path,
) -> None:
    """
    Write ``silver/cms_match_state.parquet`` recording which CMS providers were
    matched, at which tier, to which ``lifeline_id``.

    Unmatched providers have ``match_status = "unmatched"`` and empty match
    columns, enabling QAQC of remaining coverage gaps.

    Parameters
    ----------
    cms:
        Full CMS provider DataFrame from ``load_cms_providers()``.
    matched:
        Deduplicated match results DataFrame (output of ``build_attr_health_cms()``).
    silver_path:
        Path to the silver data directory.
    """
    state_cols = [
        "PRVDR_NUM", "FAC_NAME", "ST_ADR", "ZIP_CD", "CITY_NAME",
        "STATE_CD", "PRVDR_CTGRY_CD", "PRVDR_CTGRY_SBTYP_CD",
        "geocoded_lat", "geocoded_lon",
    ]
    keep = [c for c in state_cols if c in cms.columns]
    state = cms[keep].copy()

    if len(matched):
        # Dedupe by PRVDR_NUM: keep best-score match per provider
        match_df = (
            matched[["cms_provider_num", "lifeline_id", "cms_match_method", "cms_match_score"]]
            .copy()
            .sort_values("cms_match_score", ascending=False)
            .drop_duplicates("cms_provider_num")
            .rename(columns={
                "cms_provider_num": "PRVDR_NUM",
                "lifeline_id": "match_lifeline_id",
                "cms_match_method": "match_tier",
                "cms_match_score": "match_score",
            })
        )
        match_df["match_status"] = "matched"
        state = state.merge(match_df, on="PRVDR_NUM", how="left")
    else:
        state["match_status"] = "unmatched"
        state["match_tier"] = ""
        state["match_lifeline_id"] = ""
        state["match_score"] = float("nan")

    state["match_status"] = state["match_status"].fillna("unmatched")
    state["match_tier"] = state["match_tier"].fillna("")
    state["match_lifeline_id"] = state["match_lifeline_id"].fillna("")

    out_file = Path(silver_path) / "cms_match_state.parquet"
    state.to_parquet(out_file, index=False)
    print(f"    CMS match state: {(state['match_status'] == 'matched').sum():,} matched, "
          f"{(state['match_status'] == 'unmatched').sum():,} unmatched -> {out_file}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_attr_health_cms(
    silver_path: Path,
    bronze_path: Path,
    threshold: float = 0.80,
    spatial_distance_m: float = 200.0,
    spatial_name_threshold: float = 0.55,
    addr_name_threshold: float = 0.50,
    name_zip_threshold: float = 0.75,
    census_distance_m: float = 500.0,
    census_name_threshold: float = 0.70,
) -> pd.DataFrame:
    """
    Match CMS hospital providers to silver health POIs and return a
    ``lifeline_id``-keyed attribute DataFrame ready to write as
    ``silver/attr_health_cms.parquet``.

    Uses a five-tier strategy:
    * **Tier 1** — BallTree spatial match on CMS ``geocoded_lat/lon`` within
      ``spatial_distance_m`` metres, guarded by ``spatial_name_threshold``.
      Queries up to k=10 nearest providers; tie-breaks by hospital category
      (``PRVDR_CTGRY_CD == "01"``) then bed count.
    * **Tier 2** — state + ZIP (or ZIP-only) + rapidfuzz name match for
      unmatched POIs, minimum score ``threshold``.  Requires addr fields from
      ``silver/attr_health.parquet``; these are merged in automatically.
    * **Tier 3** — addr:housenumber + ZIP exact match with fuzzy name sanity
      check (``addr_name_threshold``).  Recorded as method ``"addr_zip"``.
      House numbers are extracted from ``addr:housenumber`` (OSM tag), falling
      back to parsing the ``display_name`` field for POIs where that tag is
      absent (common in OSM data for PR).
    * **Tier 4** — ZIP + ``token_set_ratio`` name catch-all (``name_zip_threshold``).
    * **Tier 5** — Census Batch API geocodes unmatched CMS providers; spatial
      BallTree (``census_distance_m``) + ``token_set_ratio`` (``census_name_threshold``).
      Degrades gracefully if the Census API is unreachable.

    Missing US state codes (``_state == ""``) are resolved via pygris Census
    state boundaries before tier matching begins.

    As a side-effect, writes ``silver/cms_match_state.parquet`` — a table of
    all CMS providers tagged as ``"matched"`` or ``"unmatched"`` with the tier,
    ``lifeline_id``, and score for matched providers.  This file supports QAQC
    and ensures the same CMS provider is not claimed by multiple tiers.

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
    addr_name_threshold:
        Minimum fuzzy name score (0–1) for Tier 3 addr_num + ZIP matches.
    name_zip_threshold:
        Minimum ``token_set_ratio`` score (0–1) for Tier 4 ZIP + set-fuzzy
        name matches (default 0.75).
    census_distance_m:
        Spatial search radius in metres for Tier 5 Census-geocoded matches
        (default 500 m — wider than Tier 1 to account for geocoding error).
    census_name_threshold:
        Minimum ``token_set_ratio`` score (0–1) for Tier 5 (default 0.70).

    Returns
    -------
    DataFrame with columns:
        lifeline_id, cms_provider_num, cms_provider_category,
        cms_provider_subtype, cms_match_score, cms_match_method,
        cms_match_distance_m, cms_bed_cnt, cms_certified_bed_cnt,
        cms_operating_rooms, cms_<all_other_cnt_fields>...
    """
    silver_path = Path(silver_path)
    bronze_path = Path(bronze_path)

    _empty_cols = [
        "lifeline_id", "cms_provider_num", "cms_provider_category",
        "cms_provider_subtype", "cms_match_score",
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
    for col in ("addr:state", "addr:postcode", "addr:city", "addr:housenumber", "name", "display_name"):
        if col not in health.columns:
            health[col] = ""
        else:
            health[col] = health[col].fillna("").astype(str).str.strip()

    # Enrich addr fields from silver/attr_health.parquet (recovers OSM tags stripped in conflation)
    health = _enrich_health_addr(health, silver_path)

    health["_zip5"] = health["addr:postcode"].str[:5]
    health["_state"] = health["addr:state"].str.upper().str[:2]
    health["_city"] = health["addr:city"].str.upper()
    health["_addr_num"] = health["addr:housenumber"].str.extract(r"^(\d+)")[0].fillna("")
    # Fallback: extract addr_num from display_name when addr:housenumber is empty.
    # OSM display_name typically looks like "Name, 917 Avenida X, City, Region ZIP"
    # — the street number appears after a comma (or at start), 2–4 digits, then a space.
    _no_num = health["_addr_num"] == ""
    if _no_num.any():
        _dn_num = health.loc[_no_num, "display_name"].str.extract(
            r"(?:,\s*|^)(\d{2,4})\s"
        )[0].fillna("")
        health.loc[_no_num, "_addr_num"] = _dn_num
    # Additional fallback: PR highway references in display_name (e.g. "PR-135")
    _still_no_num = health["_addr_num"] == ""
    if _still_no_num.any():
        _pr_num = health.loc[_still_no_num, "display_name"].str.extract(
            r"\bPR-(\d{2,4})\b"
        )[0].fillna("")
        health.loc[_still_no_num, "_addr_num"] = _pr_num
    # ZIP fallback: extract 5-digit ZIP from display_name when still unknown
    _no_zip = health["_zip5"].str.len() < 5
    if _no_zip.any():
        _dn_zip = health.loc[_no_zip, "display_name"].str.extract(r"\b(\d{5})\b")[0].fillna("")
        health.loc[_no_zip, "_zip5"] = _dn_zip
    health["_name_norm"] = health["name"].apply(_normalize_name)
    _empty_name = health["_name_norm"] == ""
    health.loc[_empty_name, "_name_norm"] = health.loc[_empty_name, "display_name"].apply(_normalize_name)

    # Resolve missing state codes via pygris Census boundaries
    health = _resolve_missing_states(health)

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
    cms_matched_ids: set[str] = {r["cms_provider_num"] for r in tier1_results if r["cms_provider_num"]}

    # --- Tier 2: ZIP + fuzzy name ---
    tier2_results = _tier2_zip_fuzzy(health, cms, threshold, cnt_cols, matched_ids, cms_already_matched=cms_matched_ids)
    print(f"    CMS Tier 2 (zip+fuzzy): {len(tier2_results):,} matches")
    cms_matched_ids |= {r["cms_provider_num"] for r in tier2_results if r["cms_provider_num"]}

    # --- Tier 3: address number + ZIP ---
    matched_ids_t2 = matched_ids | {r["lifeline_id"] for r in tier2_results}
    tier3_results = _tier3_addr_zip(health, cms, cnt_cols, matched_ids_t2, addr_name_threshold, cms_already_matched=cms_matched_ids)
    print(f"    CMS Tier 3 (addr+zip):  {len(tier3_results):,} matches")
    cms_matched_ids |= {r["cms_provider_num"] for r in tier3_results if r["cms_provider_num"]}

    # --- Tier 4: ZIP + token_set_ratio name (permissive catch-all) ---
    matched_ids_t3 = matched_ids_t2 | {r["lifeline_id"] for r in tier3_results}
    tier4_results = _tier4_name_zip(health, cms, cnt_cols, matched_ids_t3, name_zip_threshold, cms_already_matched=cms_matched_ids)
    print(f"    CMS Tier 4 (name+zip):  {len(tier4_results):,} matches")
    cms_matched_ids |= {r["cms_provider_num"] for r in tier4_results if r["cms_provider_num"]}

    # --- Tier 5: Census geocode unmatched CMS + spatial ---
    matched_ids_t4 = matched_ids_t3 | {r["lifeline_id"] for r in tier4_results}
    tier5_results = _tier5_census_spatial(
        health, cms, cnt_cols, matched_ids_t4,
        census_distance_m=census_distance_m,
        census_name_threshold=census_name_threshold,
        cms_already_matched=cms_matched_ids,
    )
    print(f"    CMS Tier 5 (census+spatial): {len(tier5_results):,} matches")

    all_results = tier1_results + tier2_results + tier3_results + tier4_results + tier5_results
    if not all_results:
        _write_cms_match_state(cms, pd.DataFrame(), silver_path)
        return pd.DataFrame(columns=_empty_cols)

    attr = pd.DataFrame(all_results)
    # Guard against duplicate lifeline_ids (shouldn't normally occur)
    attr = (
        attr.sort_values("cms_match_score", ascending=False)
        .drop_duplicates("lifeline_id")
        .reset_index(drop=True)
    )

    # Write silver CMS match state for QAQC
    _write_cms_match_state(cms, attr, silver_path)

    return attr

