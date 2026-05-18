"""
HIFLD Hospital Attribute extraction for LifelinePOI.

Matches HIFLD bronze hospital records to silver health POIs to carry
authoritative hospital attributes (trauma level, helipad, owner type, etc.)
into a silver attribute table: ``silver/attr_health_hifld_attrs.parquet``.

This uses the same state+ZIP+name fuzzy-match pattern as CMS enrichment
so the two attr tables are independent and can be merged separately.

HIFLD TRAUMA field value examples:
  LEVEL I, LEVEL II, LEVEL III, LEVEL IV, LEVEL V,
  LEVEL I PEDIATRIC, LEVEL I ADULT, TRH, TRF, CTH, ATH, RTC,
  NOT AVAILABLE (omitted from output)
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd


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


def load_hifld_hospitals(bronze_path: Path) -> pd.DataFrame:
    """
    Load HIFLD hospitals bronze parquet and return a DataFrame with
    normalised key fields ready for matching.

    Returns an empty DataFrame if the file does not exist.
    """
    hifld_file = Path(bronze_path) / "hifld" / "hospitals.parquet"
    if not hifld_file.exists():
        return pd.DataFrame()

    df = pd.read_parquet(hifld_file)

    # Keep relevant columns only
    keep = [c for c in ["NAME", "ADDRESS", "CITY", "STATE", "ZIP",
                         "TRAUMA", "HELIPAD", "OWNER", "TYPE",
                         "BEDS", "LATITUDE", "LONGITUDE", "OBJECTID"] if c in df.columns]
    df = df[keep].copy()

    for col in ("NAME", "ADDRESS", "CITY", "STATE", "ZIP"):
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip()

    if "ZIP" in df.columns:
        df["_zip5"] = df["ZIP"].str[:5]

    if "NAME" in df.columns:
        df["_name_norm"] = df["NAME"].apply(_normalize_name)

    if "STATE" in df.columns:
        df["_state"] = df["STATE"].str.upper().str[:2]

    if "CITY" in df.columns:
        df["_city_norm"] = df["CITY"].str.upper()

    return df.reset_index(drop=True)


def build_attr_health_hifld(
    silver_path: Path,
    bronze_path: Path,
    threshold: float = 0.80,
) -> pd.DataFrame:
    """
    Match HIFLD hospital records to silver health POIs and return a
    ``lifeline_id``-keyed attribute DataFrame ready to write as
    ``silver/attr_health_hifld_attrs.parquet``.

    Matching uses a vectorized tiered-merge strategy instead of row iteration:

    * **Tier 1** — merge on ``(state, zip)``   → tightest candidates
    * **Tier 2** — merge on ``(state, city)``  → city fallback for zip-miss POIs
    * **Tier 3** — merge on ``state`` only     → last resort for the remainder

    Fuzzy name scoring runs over all candidate pairs in one vectorized pass
    using rapidfuzz; the best score wins per health POI.

    Parameters
    ----------
    silver_path:
        Path to the silver data directory (contains ``lifeline_points.parquet``).
    bronze_path:
        Path to the bronze data directory (contains ``hifld/hospitals.parquet``).
    threshold:
        Minimum rapidfuzz ``token_sort_ratio`` score (0–1) to accept a match.

    Returns
    -------
    DataFrame with columns:
        lifeline_id, hifld_trauma, hifld_helipad, hifld_owner,
        hifld_hospital_type, hifld_match_distance_m
    """
    try:
        from rapidfuzz import fuzz
    except ImportError as exc:
        raise ImportError("rapidfuzz is required: pip install rapidfuzz") from exc

    silver_path = Path(silver_path)
    bronze_path = Path(bronze_path)

    _empty = pd.DataFrame(columns=[
        "lifeline_id", "hifld_trauma", "hifld_helipad",
        "hifld_owner", "hifld_hospital_type", "hifld_match_distance_m",
    ])

    master_file = silver_path / "lifeline_points.parquet"
    if not master_file.exists():
        return _empty

    master = pd.read_parquet(master_file)
    if "tmp_osm_layer" in master.columns:
        health = master[master["tmp_osm_layer"] == "health"].copy()
    else:
        attr_file = silver_path / "attr_health.parquet"
        if attr_file.exists():
            attr_ids = pd.read_parquet(attr_file, columns=["lifeline_id"])["lifeline_id"]
            health = master[master["lifeline_id"].isin(attr_ids)].copy()
        else:
            health = master.copy()

    if len(health) == 0:
        return _empty

    # ── Prepare health lookup columns ─────────────────────────────────────
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
    health.loc[_empty_name, "_name_norm"] = (
        health.loc[_empty_name, "display_name"].apply(_normalize_name)
    )

    # ── Load HIFLD bronze ─────────────────────────────────────────────────
    hifld = load_hifld_hospitals(bronze_path)
    if len(hifld) == 0:
        return _empty

    # Rename HIFLD's normalised name to avoid collision with health's column.
    hifld = hifld.rename(columns={"_name_norm": "_hifld_name"})

    # Only bring the columns needed for merging and result assembly.
    _attr_cols = [c for c in ("TRAUMA", "HELIPAD", "OWNER", "TYPE") if c in hifld.columns]
    _merge_cols = ["_state", "_zip5", "_city_norm", "_hifld_name"] + _attr_cols
    _merge_cols = [c for c in _merge_cols if c in hifld.columns]
    hifld_m = hifld[_merge_cols]

    # Narrow health to the columns used in merges.
    health_m = health[["lifeline_id", "_name_norm", "_state", "_zip5", "_city"]].copy()
    # Drop health POIs with no name (nothing to fuzzy-match against).
    health_m = health_m[health_m["_name_norm"] != ""].copy()

    if len(health_m) == 0:
        return _empty

    SKIP_TRAUMA = {"NOT AVAILABLE", "N/A", "NONE", "", "NAN", "NONE LISTED"}

    # ── Tier 1: state + ZIP ───────────────────────────────────────────────
    h_zip = health_m[health_m["_zip5"].str.len() == 5]
    tier1 = h_zip.merge(hifld_m, on=["_state", "_zip5"], how="inner")

    # ── Tier 2: state + city (zip had no match) ───────────────────────────
    zip_matched = set(tier1["lifeline_id"])
    h_city = h_zip[
        ~h_zip["lifeline_id"].isin(zip_matched) & (h_zip["_city"] != "")
    ]
    if "_city_norm" in hifld_m.columns and len(h_city):
        tier2 = h_city.merge(
            hifld_m, left_on=["_state", "_city"], right_on=["_state", "_city_norm"], how="inner"
        )
    else:
        tier2 = pd.DataFrame(columns=tier1.columns)

    # ── Tier 3: state only (no zip or zip+city both missed) ───────────────
    city_matched = zip_matched | set(tier2["lifeline_id"])
    h_state = health_m[
        ~health_m["lifeline_id"].isin(city_matched) & (health_m["_state"] != "")
    ]
    tier3 = h_state.merge(hifld_m, on="_state", how="inner") if len(h_state) else pd.DataFrame(columns=tier1.columns)

    # ── Score all candidate pairs at once ─────────────────────────────────
    candidates = pd.concat([tier1, tier2, tier3], ignore_index=True)
    if len(candidates) == 0:
        return _empty

    hifld_names = candidates["_hifld_name"].fillna("").tolist()
    health_names = candidates["_name_norm"].tolist()
    candidates["_score"] = [
        fuzz.token_sort_ratio(a, b) / 100.0
        for a, b in zip(health_names, hifld_names)
    ]

    # Filter by threshold and keep best match per POI.
    candidates = candidates[candidates["_score"] >= threshold]
    if len(candidates) == 0:
        return _empty

    best = (
        candidates
        .sort_values("_score", ascending=False)
        .drop_duplicates("lifeline_id")
        .reset_index(drop=True)
    )

    # ── Build result with vectorized string ops ───────────────────────────
    def _strcol(df: pd.DataFrame, name: str) -> pd.Series:
        if name in df.columns:
            return df[name].fillna("").astype(str).str.strip()
        return pd.Series("", index=df.index)

    trauma = _strcol(best, "TRAUMA")
    helipad = _strcol(best, "HELIPAD")
    owner = _strcol(best, "OWNER")
    htype = _strcol(best, "TYPE")
    trauma_upper = trauma.str.upper()

    # Drop rows that carry no useful data.
    has_useful = ~(
        trauma_upper.isin(SKIP_TRAUMA)
        & helipad.isin(("", "N", "nan"))
        & (owner == "")
        & (htype == "")
    )
    best = best[has_useful].reset_index(drop=True)
    if len(best) == 0:
        return _empty

    trauma = trauma[has_useful].reset_index(drop=True)
    helipad = helipad[has_useful].reset_index(drop=True)
    owner = owner[has_useful].reset_index(drop=True)
    htype = htype[has_useful].reset_index(drop=True)
    trauma_upper = trauma_upper[has_useful].reset_index(drop=True)

    result = pd.DataFrame({
        "lifeline_id": best["lifeline_id"].values,
        "hifld_trauma": trauma.where(~trauma_upper.isin(SKIP_TRAUMA)).values,
        "hifld_helipad": helipad.where(~helipad.isin(("", "N", "nan"))).values,
        "hifld_owner": owner.where(owner != "").values,
        "hifld_hospital_type": htype.where(htype != "").values,
        "hifld_match_distance_m": best["_score"].values,
    })
    return result.reset_index(drop=True)
