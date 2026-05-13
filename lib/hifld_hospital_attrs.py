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

import numpy as np
import pandas as pd


def _normalize_name(name: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace for fuzzy comparison."""
    if not name or not isinstance(name, str):
        return ""
    name = name.lower()
    name = re.sub(r"[^a-z0-9 ]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
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

    Parameters
    ----------
    silver_path:
        Path to the silver data directory (contains ``lifeline_points.parquet``).
    bronze_path:
        Path to the bronze data directory (contains
        ``hifld/hospitals.parquet``).
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

    # Normalise silver address fields
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

    # Load HIFLD hospitals bronze
    hifld = load_hifld_hospitals(bronze_path)
    if len(hifld) == 0:
        return _empty

    # Drop HIFLD records with no trauma info to keep (we still match all, but
    # only keep results where at least one useful attribute is non-null / non-empty)
    SKIP_TRAUMA = {"NOT AVAILABLE", "N/A", "NONE", "", "NAN", "NONE LISTED"}

    results: list[dict] = []

    for row in health.itertuples(index=False):
        lid = row.lifeline_id
        state = row._state   # noqa: SLF001
        zip5 = row._zip5     # noqa: SLF001
        city = row._city     # noqa: SLF001
        name_norm = row._name_norm  # noqa: SLF001

        if not name_norm:
            continue

        candidates = hifld[hifld["_state"] == state] if state else hifld

        if zip5 and len(zip5) == 5:
            zip_cands = candidates[candidates["_zip5"] == zip5]
            if len(zip_cands) > 0:
                candidates = zip_cands
            elif city:
                city_cands = candidates[candidates["_city_norm"] == city]
                candidates = city_cands if len(city_cands) > 0 else candidates

        if len(candidates) == 0:
            continue

        scores = np.array([
            fuzz.token_sort_ratio(name_norm, cn) / 100.0
            for cn in candidates["_name_norm"]
        ])
        best_idx = int(np.argmax(scores))
        best_score = float(scores[best_idx])

        if best_score < threshold:
            continue

        best = candidates.iloc[best_idx]

        trauma = str(best.get("TRAUMA", "") or "").strip()
        helipad = str(best.get("HELIPAD", "") or "").strip()
        owner = str(best.get("OWNER", "") or "").strip()
        htype = str(best.get("TYPE", "") or "").strip()

        # Skip records that bring no useful data
        trauma_clean = trauma.upper() if trauma else ""
        if trauma_clean in SKIP_TRAUMA and not helipad and not owner and not htype:
            continue

        results.append({
            "lifeline_id": lid,
            "hifld_trauma": trauma if trauma_clean not in SKIP_TRAUMA else None,
            "hifld_helipad": helipad if helipad not in ("", "N", "nan") else None,
            "hifld_owner": owner or None,
            "hifld_hospital_type": htype or None,
            "hifld_match_distance_m": round(best_score, 4),  # repurpose field as match score
        })

    if not results:
        return _empty

    attr = pd.DataFrame(results)
    attr = attr.sort_values("hifld_match_distance_m", ascending=False).drop_duplicates("lifeline_id")
    return attr.reset_index(drop=True)
