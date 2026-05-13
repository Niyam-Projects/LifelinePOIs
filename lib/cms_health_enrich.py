"""
CMS Hospital & Non-Hospital Provider Info enrichment for LifelinePOI.

Matches CMS provider records (bronze) against silver health POIs to produce
``silver/attr_health_cms.parquet``, a supplemental attribute table containing
authoritative bed counts and related provider metrics.

Matching strategy:
  1. Pre-filter CMS to hospital categories (PRVDR_CTGRY_CD == "01").
  2. Exact pre-filter on state abbreviation + ZIP first-5 to narrow candidates.
  3. rapidfuzz ``token_sort_ratio`` on normalized names (threshold configurable).
  4. Fallback: state + city name match if ZIP is absent on the silver side.
  5. Best-score-wins when multiple CMS records match the same POI.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd


# CMS category code for general/acute care hospitals
_HOSPITAL_CTGRY = "01"


def _normalize_name(name: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace for fuzzy comparison."""
    if not name or not isinstance(name, str):
        return ""
    name = name.lower()
    name = re.sub(r"[^a-z0-9 ]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def load_cms_providers(bronze_path: Path) -> pd.DataFrame:
    """
    Load CMS provider parquet from bronze and return a filtered DataFrame
    containing only hospital records (PRVDR_CTGRY_CD == "01").

    Returns an empty DataFrame if the file does not exist.
    """
    cms_file = Path(bronze_path) / "cms" / "cms_hospital_providers.parquet"
    if not cms_file.exists():
        return pd.DataFrame()

    df = pd.read_parquet(cms_file)

    # Filter to hospitals only
    if "PRVDR_CTGRY_CD" in df.columns:
        df = df[df["PRVDR_CTGRY_CD"].astype(str) == _HOSPITAL_CTGRY].copy()

    # Normalize key fields for matching
    for col in ("FAC_NAME", "ST_ADR", "CITY_NAME", "STATE_CD", "ZIP_CD"):
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip()

    if "ZIP_CD" in df.columns:
        df["_zip5"] = df["ZIP_CD"].str[:5]

    if "FAC_NAME" in df.columns:
        df["_name_norm"] = df["FAC_NAME"].apply(_normalize_name)

    # Numeric bed columns — coerce to int (NaN → 0)
    for col in ("BED_CNT", "CRTFD_BED_CNT", "OPRTG_ROOM_CNT"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)

    return df.reset_index(drop=True)


def build_attr_health_cms(
    silver_path: Path,
    bronze_path: Path,
    threshold: float = 0.80,
) -> pd.DataFrame:
    """
    Match CMS hospital providers to silver health POIs and return a
    ``lifeline_id``-keyed attribute DataFrame ready to write as
    ``silver/attr_health_cms.parquet``.

    Parameters
    ----------
    silver_path:
        Path to the silver data directory (contains ``lifeline_points.parquet``
        and optionally ``attr_health.parquet``).
    bronze_path:
        Path to the bronze data directory (contains
        ``cms/cms_hospital_providers.parquet``).
    threshold:
        Minimum rapidfuzz ``token_sort_ratio`` score (0–1) to accept a match.

    Returns
    -------
    DataFrame with columns:
        lifeline_id, cms_provider_num, cms_bed_cnt, cms_certified_bed_cnt,
        cms_operating_rooms, cms_match_score
    """
    try:
        from rapidfuzz import fuzz
    except ImportError as exc:
        raise ImportError("rapidfuzz is required: pip install rapidfuzz") from exc

    silver_path = Path(silver_path)
    bronze_path = Path(bronze_path)

    # --- Load silver health points ---
    master_file = silver_path / "lifeline_points.parquet"
    if not master_file.exists():
        return pd.DataFrame(columns=[
            "lifeline_id", "cms_provider_num", "cms_bed_cnt",
            "cms_certified_bed_cnt", "cms_operating_rooms", "cms_match_score",
        ])

    master = pd.read_parquet(master_file)
    health = master[master.get("tmp_osm_layer", master.get("lifeline_category", pd.Series())).astype(str) == "health"].copy()
    if "tmp_osm_layer" in master.columns:
        health = master[master["tmp_osm_layer"] == "health"].copy()
    else:
        # Fallback: merge attr_health to identify health points
        attr_file = silver_path / "attr_health.parquet"
        if attr_file.exists():
            attr = pd.read_parquet(attr_file, columns=["lifeline_id"])
            health = master[master["lifeline_id"].isin(attr["lifeline_id"])].copy()
        else:
            health = master.copy()

    if len(health) == 0:
        return pd.DataFrame(columns=[
            "lifeline_id", "cms_provider_num", "cms_bed_cnt",
            "cms_certified_bed_cnt", "cms_operating_rooms", "cms_match_score",
        ])

    # Build lookup columns on health points
    health = health.copy()
    for col in ("addr:state", "addr:postcode", "addr:city", "name", "display_name"):
        if col not in health.columns:
            health[col] = ""
        else:
            health[col] = health[col].fillna("").astype(str).str.strip()

    health["_zip5"] = health["addr:postcode"].str[:5]
    health["_state"] = health["addr:state"].str.upper().str[:2]
    health["_city"] = health["addr:city"].str.upper()
    health["_name_norm"] = health["name"].apply(_normalize_name)
    # Fallback name from display_name when OSM name is empty
    _empty_name = health["_name_norm"] == ""
    health.loc[_empty_name, "_name_norm"] = health.loc[_empty_name, "display_name"].apply(_normalize_name)

    # --- Load CMS providers ---
    cms = load_cms_providers(bronze_path)
    if len(cms) == 0:
        return pd.DataFrame(columns=[
            "lifeline_id", "cms_provider_num", "cms_bed_cnt",
            "cms_certified_bed_cnt", "cms_operating_rooms", "cms_match_score",
        ])

    cms = cms.copy()
    cms["_state"] = cms["STATE_CD"].str.upper().str[:2] if "STATE_CD" in cms.columns else ""
    cms["_city_norm"] = cms["CITY_NAME"].str.upper() if "CITY_NAME" in cms.columns else ""

    # --- Match ---
    results: list[dict] = []

    for row in health.itertuples(index=False):
        lid = row.lifeline_id
        state = row._state  # noqa: SLF001
        zip5 = row._zip5    # noqa: SLF001
        city = row._city    # noqa: SLF001
        name_norm = row._name_norm  # noqa: SLF001

        if not name_norm:
            continue

        # Candidate filtering: state match is required; then try ZIP, then city fallback
        candidates = cms[cms["_state"] == state] if state else cms

        if zip5 and len(zip5) == 5:
            zip_candidates = candidates[candidates["_zip5"] == zip5]
            if len(zip_candidates) > 0:
                candidates = zip_candidates
            elif city:
                # ZIP not matched — fall back to city
                city_candidates = candidates[candidates["_city_norm"] == city]
                candidates = city_candidates if len(city_candidates) > 0 else candidates

        if len(candidates) == 0:
            continue

        # Vectorized fuzzy match
        scores = np.array([
            fuzz.token_sort_ratio(name_norm, cn) / 100.0
            for cn in candidates["_name_norm"]
        ])
        best_idx = int(np.argmax(scores))
        best_score = float(scores[best_idx])

        if best_score < threshold:
            continue

        best = candidates.iloc[best_idx]
        results.append({
            "lifeline_id": lid,
            "cms_provider_num": best.get("PRVDR_NUM", ""),
            "cms_bed_cnt": int(best.get("BED_CNT", 0)),
            "cms_certified_bed_cnt": int(best.get("CRTFD_BED_CNT", 0)),
            "cms_operating_rooms": int(best.get("OPRTG_ROOM_CNT", 0)),
            "cms_match_score": round(best_score, 4),
        })

    if not results:
        return pd.DataFrame(columns=[
            "lifeline_id", "cms_provider_num", "cms_bed_cnt",
            "cms_certified_bed_cnt", "cms_operating_rooms", "cms_match_score",
        ])

    attr = pd.DataFrame(results)
    # Keep only the best match per lifeline_id (shouldn't normally occur but guard anyway)
    attr = attr.sort_values("cms_match_score", ascending=False).drop_duplicates("lifeline_id")
    return attr.reset_index(drop=True)
