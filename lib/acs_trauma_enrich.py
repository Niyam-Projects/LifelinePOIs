"""
ACS (American College of Surgeons) Trauma Center enrichment for LifelinePOI.

Reads the committed seed file ``data/seed/acs_trauma_level.parquet`` — an
export of ACS-verified trauma centers — and matches records to silver health
POIs to produce ``silver/attr_health_acs_trauma.parquet``.

Matching strategy:
  1. BallTree spatial match (default 200 m) — ACS records include lat/lon.
  2. rapidfuzz token_sort_ratio name confirmation (default threshold 0.70) to
     reject geometrically-close but wrong hospitals.
  3. Best-distance-wins when multiple ACS records fall within the radius.

The ACS trauma level is the most authoritative source for Level I–V
designations and takes priority over the HIFLD TRAUMA field in gold output.
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd


_SEED_RELATIVE = Path("data") / "seed" / "acs_trauma_level.parquet"


def _normalize_name(name: str) -> str:
    if not name or not isinstance(name, str):
        return ""
    name = name.lower()
    name = re.sub(r"[^a-z0-9 ]", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def load_acs_trauma(seed_path: Path | None = None) -> pd.DataFrame:
    """
    Load the ACS trauma seed parquet.

    Parameters
    ----------
    seed_path:
        Explicit path to the parquet file.  If None, looks for
        ``data/seed/acs_trauma_level.parquet`` relative to the current
        working directory (i.e., the repo root).

    Returns an empty DataFrame if the file does not exist.
    """
    if seed_path is None:
        seed_path = Path.cwd() / _SEED_RELATIVE
    seed_path = Path(seed_path)
    if not seed_path.exists():
        return pd.DataFrame()

    df = pd.read_parquet(seed_path)

    # Normalise for matching
    for col in ("institution_name", "program_name", "city", "state", "zip_code"):
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip()

    if "state" in df.columns:
        df["_state"] = df["state"].str.upper().str[:2]

    if "zip_code" in df.columns:
        df["_zip5"] = df["zip_code"].str[:5]

    if "institution_name" in df.columns:
        df["_name_norm"] = df["institution_name"].apply(_normalize_name)
    elif "program_name" in df.columns:
        df["_name_norm"] = df["program_name"].apply(_normalize_name)
    else:
        df["_name_norm"] = ""

    # Coerce lat/lon to float
    for col in ("latitude", "longitude"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df.reset_index(drop=True)


def build_attr_health_acs_trauma(
    silver_path: Path,
    seed_path: Path | None = None,
    max_distance_m: float = 200.0,
    name_threshold: float = 0.70,
) -> pd.DataFrame:
    """
    Match ACS trauma centers to silver health POIs and return a
    ``lifeline_id``-keyed attribute DataFrame.

    Parameters
    ----------
    silver_path:
        Path to silver data directory (contains ``lifeline_points.parquet``).
    seed_path:
        Override path to ``acs_trauma_level.parquet``.  Defaults to
        ``data/seed/acs_trauma_level.parquet`` relative to cwd.
    max_distance_m:
        Maximum spatial distance in metres for a valid match.
    name_threshold:
        Minimum rapidfuzz token_sort_ratio score (0–1) to confirm match.

    Returns
    -------
    DataFrame with columns:
        lifeline_id, acs_institution_id, acs_trauma_level,
        acs_program_type, acs_match_distance_m
    """
    try:
        from rapidfuzz import fuzz
    except ImportError as exc:
        raise ImportError("rapidfuzz is required: pip install rapidfuzz") from exc

    try:
        from sklearn.neighbors import BallTree
    except ImportError as exc:
        raise ImportError("scikit-learn is required: pip install scikit-learn") from exc

    _empty = pd.DataFrame(columns=[
        "lifeline_id", "acs_institution_id", "acs_trauma_level",
        "acs_program_type", "acs_match_distance_m",
    ])

    silver_path = Path(silver_path)
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

    # Load ACS seed
    acs = load_acs_trauma(seed_path)
    if len(acs) == 0:
        return _empty

    # Drop ACS records without coordinates
    acs_geo = acs.dropna(subset=["latitude", "longitude"]).copy().reset_index(drop=True)
    if len(acs_geo) == 0:
        return _empty

    # Build BallTree on ACS coordinates (radians for haversine)
    acs_coords_rad = np.radians(acs_geo[["latitude", "longitude"]].values)
    tree = BallTree(acs_coords_rad, metric="haversine")

    # Extract health POI coordinates
    try:
        import geopandas as gpd
        if not hasattr(health, "geometry"):
            raise AttributeError
        health_proj = health.copy()
        poi_lons = health_proj.geometry.x.values
        poi_lats = health_proj.geometry.y.values
    except Exception:
        poi_lons = pd.to_numeric(health.get("longitude", pd.Series(dtype=float)), errors="coerce").values
        poi_lats = pd.to_numeric(health.get("latitude", pd.Series(dtype=float)), errors="coerce").values

    EARTH_RADIUS_M = 6_371_000.0
    radius_rad = max_distance_m / EARTH_RADIUS_M

    poi_coords_rad = np.column_stack([np.radians(poi_lats), np.radians(poi_lons)])

    # Query: indices and distances within radius
    indices, distances = tree.query_radius(
        poi_coords_rad, r=radius_rad, return_distance=True, sort_results=True
    )

    # Build name lookup for POIs
    for col in ("name", "display_name"):
        if col not in health.columns:
            health[col] = ""
        else:
            health[col] = health[col].fillna("").astype(str)
    health["_name_norm"] = health["name"].apply(_normalize_name)
    _empty_nm = health["_name_norm"] == ""
    health.loc[_empty_nm, "_name_norm"] = health.loc[_empty_nm, "display_name"].apply(_normalize_name)

    results: list[dict] = []
    health_vals = health[["lifeline_id", "_name_norm"]].values

    for i, (cand_indices, cand_dists) in enumerate(zip(indices, distances)):
        if len(cand_indices) == 0:
            continue
        lid = health_vals[i, 0]
        poi_name = health_vals[i, 1]

        # Among candidates in radius, pick best name match
        best_score = -1.0
        best_acs_idx = -1
        best_dist_m = float("inf")

        for acs_idx, dist_rad in zip(cand_indices, cand_dists):
            dist_m = float(dist_rad * EARTH_RADIUS_M)
            acs_name = acs_geo.at[acs_idx, "_name_norm"]
            score = fuzz.token_sort_ratio(poi_name, acs_name) / 100.0 if poi_name and acs_name else 0.0
            if score > best_score:
                best_score = score
                best_acs_idx = acs_idx
                best_dist_m = dist_m

        if best_score < name_threshold or best_acs_idx < 0:
            continue

        best = acs_geo.iloc[best_acs_idx]
        results.append({
            "lifeline_id": lid,
            "acs_institution_id": int(best.get("institution_id", 0) or 0),
            "acs_trauma_level": str(best.get("trauma_level", "") or "").strip() or None,
            "acs_program_type": str(best.get("program_type", "") or "").strip() or None,
            "acs_match_distance_m": round(best_dist_m, 1),
        })

    if not results:
        return _empty

    attr = pd.DataFrame(results)
    # If a POI somehow matched multiple ACS records, keep closest
    attr = attr.sort_values("acs_match_distance_m").drop_duplicates("lifeline_id")
    return attr.reset_index(drop=True)
