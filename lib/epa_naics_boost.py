"""
EPA FRS NAICS/SIC → FEMA Lifeline Confidence Boost + New POI Minting.

Two-pass processing pipeline:

Pass 1 — Tight spatial match (50m):
    Filter FRS to lifeline-coded records → BallTree match against silver
    lifeline_points → apply additive confidence boost to matched records.

Pass 2 — Re-geocode + displacement check → match or mint:
    For unmatched FRS records:
      1. Re-geocode via Overture address parquet (if geocode_address_path configured).
      2. Compute displacement between original FRS coords and geocoded coords.
         - Displacement ≤ max_displacement_m → use geocoded point (building-level);
           try a second 100m BallTree match against silver.
         - Displacement > max_displacement_m → billing/HQ address detected; fall
           back to original FRS coords.
      3. Records still unmatched after Pass 2 → mint as new silver POIs.

New POI source_provenance values:
    "epa_frs"                  — minted from FRS coords (geocode failed/unavailable
                                 or large displacement)
    "epa_frs+overture_geocode" — minted using re-geocoded building-level point
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Optional

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point

from lib.naics_lifeline_map import lookup_code, boost_for_tier, NAICS_LIFELINE_MAP, SIC_LIFELINE_MAP, make_fema_lifeline_struct
from lib.scoring import ConfidenceTier, compute_confidence


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _split_codes(code_str: str) -> list[str]:
    import re
    return [c.strip() for c in re.split(r"[|,]", code_str) if c.strip()]


def _is_lifeline_naics(val: object) -> bool:
    if pd.isna(val):
        return False
    for code in _split_codes(str(val)):
        if code in NAICS_LIFELINE_MAP:
            return True
    return False


def _is_lifeline_sic(val: object) -> bool:
    if pd.isna(val):
        return False
    for code in _split_codes(str(val)):
        if code in SIC_LIFELINE_MAP:
            return True
    return False


def _coords_array(gdf: gpd.GeoDataFrame) -> np.ndarray:
    """Return (N, 2) float array of [x, y] in the GDF's current CRS."""
    return np.array([
        [g.centroid.x if g.geom_type != "Point" else g.x,
         g.centroid.y if g.geom_type != "Point" else g.y]
        for g in gdf.geometry
    ])


def _haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Approximate surface distance in meters between two WGS-84 points."""
    R = 6_371_000.0
    phi1, phi2 = np.radians(lat1), np.radians(lat2)
    dphi = np.radians(lat2 - lat1)
    dlambda = np.radians(lon2 - lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(phi1) * np.cos(phi2) * np.sin(dlambda / 2) ** 2
    return R * 2 * np.arcsin(np.sqrt(a))


# ---------------------------------------------------------------------------
# Step 1: Load + filter FRS
# ---------------------------------------------------------------------------

def load_epa_frs_filtered(
    bronze_path: Path | str,
    bbox: Optional[list[float]] = None,
) -> gpd.GeoDataFrame:
    """
    Load the EPA FRS national parquet, keep only rows whose NAICS or SIC codes
    match the FEMA lifeline map (~500K of 10.4M rows), and return as a
    GeoDataFrame in EPSG:4326.

    Args:
        bronze_path: root bronze storage directory (contains epa/frs_national.parquet)
        bbox:        optional [min_lon, min_lat, max_lon, max_lat] filter

    Returns:
        GeoDataFrame with columns: REGISTRY_ID, PRIMARY_NAME, LOCATION_ADDRESS,
        CITY_NAME, STATE_CODE, POSTAL_CODE, NAICS_CODES, SIC_CODES, geometry
    """
    parquet_path = Path(bronze_path) / "epa" / "frs_national.parquet"
    if not parquet_path.exists():
        raise FileNotFoundError(f"EPA FRS parquet not found: {parquet_path}")

    cols = [
        "REGISTRY_ID", "PRIMARY_NAME", "LOCATION_ADDRESS",
        "CITY_NAME", "STATE_CODE", "POSTAL_CODE",
        "NAICS_CODES", "SIC_CODES", "geometry",
    ]
    df = pd.read_parquet(parquet_path, columns=cols)

    # Filter to lifeline-relevant NAICS or SIC codes
    mask = df["NAICS_CODES"].apply(_is_lifeline_naics) | df["SIC_CODES"].apply(_is_lifeline_sic)
    df = df[mask].copy()
    print(f"  EPA FRS: {len(df):,} lifeline-coded records (of {mask.shape[0]:,} total)")

    # Build GeoDataFrame
    if "geometry" in df.columns and df["geometry"].notna().any():
        from shapely import wkb, wkt
        geoms = []
        for g in df["geometry"]:
            if g is None or (isinstance(g, float) and pd.isna(g)):
                geoms.append(None)
            elif isinstance(g, str):
                try:
                    geoms.append(wkt.loads(g))
                except Exception:
                    geoms.append(None)
            elif isinstance(g, (bytes, bytearray)):
                try:
                    geoms.append(wkb.loads(g))
                except Exception:
                    geoms.append(None)
            else:
                geoms.append(g)
        gdf = gpd.GeoDataFrame(df, geometry=geoms, crs="EPSG:4326")
    else:
        raise ValueError("FRS parquet has no geometry column — run ingestion first.")

    gdf = gdf[gdf.geometry.notna()].copy()

    if bbox is not None:
        from lib.spatial import clip_to_bbox
        gdf = clip_to_bbox(gdf, bbox)

    return gdf.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Step 2a: Pass 1 — tight spatial match (50m)
# ---------------------------------------------------------------------------

def match_epa_to_silver(
    silver_gdf: gpd.GeoDataFrame,
    frs_gdf: gpd.GeoDataFrame,
    max_distance_m: float = 50.0,
    projected_crs: str = "EPSG:3857",
) -> tuple[pd.DataFrame, gpd.GeoDataFrame]:
    """
    Spatial nearest-neighbor match between silver lifeline_points and FRS points.

    Returns:
        match_df:       DataFrame with columns silver_idx, frs_idx (both integer
                        positional indices into the input GDFs), distance_m
        unmatched_frs:  GeoDataFrame of FRS rows that had no silver match within
                        max_distance_m (reset index)
    """
    from sklearn.neighbors import BallTree

    if len(silver_gdf) == 0 or len(frs_gdf) == 0:
        return pd.DataFrame(columns=["silver_idx", "frs_idx", "distance_m"]), frs_gdf.reset_index(drop=True)

    silver_proj = silver_gdf.to_crs(projected_crs)
    frs_proj = frs_gdf.to_crs(projected_crs)

    tree = BallTree(_coords_array(silver_proj), metric="euclidean")
    distances, indices = tree.query(_coords_array(frs_proj), k=1)
    distances = distances.flatten()
    indices = indices.flatten()

    matched_mask = distances <= max_distance_m

    match_df = pd.DataFrame({
        "silver_idx": indices[matched_mask],
        "frs_idx": np.where(matched_mask)[0],
        "distance_m": distances[matched_mask],
    })

    unmatched_frs = frs_gdf.iloc[~matched_mask].copy().reset_index(drop=True)
    return match_df, unmatched_frs


# ---------------------------------------------------------------------------
# Step 2b: Apply boost to matched silver records
# ---------------------------------------------------------------------------

def apply_epa_naics_boost(
    core_gdf: gpd.GeoDataFrame,
    frs_gdf: gpd.GeoDataFrame,
    match_df: pd.DataFrame,
    naics_cfg: object,
) -> gpd.GeoDataFrame:
    """
    Apply additive confidence boost to silver records matched in Pass 1 or Pass 2.

    Adds columns (if not already present):
        epa_registry_id, naics_codes, sic_codes, naics_lifeline_tier, naics_sector

    Updates: confidence_score (capped at 1.0), confidence_tier, source_provenance

    Args:
        core_gdf:   silver lifeline_points GeoDataFrame (modified in place on copy)
        frs_gdf:    FRS GeoDataFrame (aligned with frs_idx in match_df)
        match_df:   output of match_epa_to_silver()
        naics_cfg:  EpaNaicsConfig instance
    """
    core_gdf = core_gdf.copy()

    # Ensure new columns exist
    for col in ("epa_registry_id", "naics_codes", "sic_codes", "naics_lifeline_tier", "naics_sector"):
        if col not in core_gdf.columns:
            core_gdf[col] = None

    cfg_overrides = {
        "boost_tier1": naics_cfg.boost_tier1,
        "boost_tier2": naics_cfg.boost_tier2,
        "boost_tier3": naics_cfg.boost_tier3,
    }

    for _, row in match_df.iterrows():
        s_idx = int(row["silver_idx"])
        f_idx = int(row["frs_idx"])
        frs_row = frs_gdf.iloc[f_idx]

        naics_str = frs_row.get("NAICS_CODES", None)
        sic_str = frs_row.get("SIC_CODES", None)

        entry = lookup_code(
            naics_str if pd.notna(naics_str) else None,
            sic_str if pd.notna(sic_str) else None,
        )
        if entry is None:
            continue

        boost = boost_for_tier(entry["tier"], cfg_overrides)
        old_score = core_gdf.at[s_idx, "confidence_score"]
        new_score = min(1.0, float(old_score) + boost)
        core_gdf.at[s_idx, "confidence_score"] = new_score

        tier = (
            ConfidenceTier.HIGH if new_score >= 0.75
            else ConfidenceTier.MEDIUM if new_score >= 0.40
            else ConfidenceTier.LOW
        )
        core_gdf.at[s_idx, "confidence_tier"] = tier.value

        prov = core_gdf.at[s_idx, "source_provenance"]
        if "epa_naics" not in str(prov):
            core_gdf.at[s_idx, "source_provenance"] = f"{prov}+epa_naics"

        # Tag with FRS / NAICS metadata (don't overwrite if already set)
        if pd.isna(core_gdf.at[s_idx, "epa_registry_id"]):
            core_gdf.at[s_idx, "epa_registry_id"] = str(frs_row.get("REGISTRY_ID", ""))
        if pd.isna(core_gdf.at[s_idx, "naics_codes"]):
            core_gdf.at[s_idx, "naics_codes"] = str(naics_str) if pd.notna(naics_str) else None
        if pd.isna(core_gdf.at[s_idx, "sic_codes"]):
            core_gdf.at[s_idx, "sic_codes"] = str(sic_str) if pd.notna(sic_str) else None
        core_gdf.at[s_idx, "naics_lifeline_tier"] = entry["tier"]
        core_gdf.at[s_idx, "naics_sector"] = entry["naics_sector"]

    return core_gdf


# ---------------------------------------------------------------------------
# Step 3a: Re-geocode unmatched FRS records
# ---------------------------------------------------------------------------

def regeocode_frs_batch(
    unmatched_gdf: gpd.GeoDataFrame,
    overture_address_path: str | Path,
) -> gpd.GeoDataFrame:
    """
    Attempt to re-geocode each unmatched FRS record via the Overture address
    parquet geocoder. Adds columns:
        geocoded_lon, geocoded_lat, geocode_score, geocoded_geometry

    Records that fail geocoding get NaN for all geocode columns.
    """
    from lib.geocoder import geocode

    geocoded_lons = []
    geocoded_lats = []
    geocode_scores = []

    for _, row in unmatched_gdf.iterrows():
        addr = row.get("LOCATION_ADDRESS", "")
        city = row.get("CITY_NAME", "")
        state = row.get("STATE_CODE", "")
        postcode = str(row.get("POSTAL_CODE", "") or "")
        # Normalize 9-digit ZIP to 5-digit
        if len(postcode) > 5:
            postcode = postcode[:5]

        lon, lat, score = None, None, None
        try:
            hits = geocode(
                street=str(addr) if pd.notna(addr) else "",
                housenumber="",
                postcode=postcode,
                state=str(state) if pd.notna(state) else "",
                country="US",
                city=str(city) if pd.notna(city) else "",
                base_path=str(overture_address_path),
            )
            if hits:
                best = hits[0]
                lon = best.get("lon") or best.get("longitude")
                lat = best.get("lat") or best.get("latitude")
                score = best.get("score", 0.0)
        except Exception:
            pass

        geocoded_lons.append(lon)
        geocoded_lats.append(lat)
        geocode_scores.append(score)

    result = unmatched_gdf.copy()
    result["geocoded_lon"] = geocoded_lons
    result["geocoded_lat"] = geocoded_lats
    result["geocode_score"] = geocode_scores

    # Build geocoded_geometry column
    def _make_geom(row: pd.Series) -> Point | None:
        if pd.notna(row["geocoded_lon"]) and pd.notna(row["geocoded_lat"]):
            return Point(float(row["geocoded_lon"]), float(row["geocoded_lat"]))
        return None

    result["geocoded_geometry"] = result.apply(_make_geom, axis=1)
    return result


# ---------------------------------------------------------------------------
# Step 3b: Compute displacement and split
# ---------------------------------------------------------------------------

def compute_displacement_m(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Add `displacement_m` column: Haversine distance between original FRS geometry
    and geocoded_geometry. NaN if geocoded_geometry is None.
    """
    gdf = gdf.copy()

    def _disp(row: pd.Series) -> float:
        geocoded = row.get("geocoded_geometry")
        if geocoded is None:
            return float("nan")
        frs_geom = row.geometry
        if frs_geom is None or frs_geom.is_empty:
            return float("nan")
        return _haversine_m(frs_geom.x, frs_geom.y, geocoded.x, geocoded.y)

    gdf["displacement_m"] = gdf.apply(_disp, axis=1)
    return gdf


def split_by_displacement(
    gdf: gpd.GeoDataFrame,
    max_displacement_m: float = 500.0,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """
    Split re-geocoded FRS records into two groups:

    plausible:         displacement ≤ max_displacement_m (or no geocode attempted)
                       → use geocoded_geometry as the authoritative point
    large_displacement: displacement > max_displacement_m
                       → billing/HQ address; fall back to original FRS geometry

    For large_displacement records the geometry column is left as-is (FRS coords).
    """
    has_geocode = gdf["geocoded_geometry"].notna()
    within = has_geocode & (gdf["displacement_m"] <= max_displacement_m)
    beyond = has_geocode & (gdf["displacement_m"] > max_displacement_m)
    no_geocode = ~has_geocode

    plausible = gdf[within].copy()
    # Replace geometry with re-geocoded point for plausible group
    plausible["geometry"] = plausible["geocoded_geometry"].apply(lambda g: g)
    plausible = gpd.GeoDataFrame(plausible, geometry="geometry", crs="EPSG:4326")

    # Large displacement + no-geocode groups both keep original FRS geometry
    large_disp = gdf[beyond | no_geocode].copy()

    return plausible.reset_index(drop=True), large_disp.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Step 4: Build new silver POIs from unmatched FRS records
# ---------------------------------------------------------------------------

_COMPONENT_LAYER_MAP: dict[tuple[str, str], str] = {
    ("Energy", "Power Grid"):               "power",
    ("Energy", "Fuel"):                     "fuel",
    ("Communications", "Infrastructure"):   "telecom",
    ("Communications", "911 and Dispatch"): "safety",
    ("Health and Medical", "Medical Care"): "health",
    ("Health and Medical", "Medical Supply Chain"): "health",
    ("Health and Medical", "Public Health"): "health",
    ("Safety and Security", "Fire Service"): "safety",
    ("Safety and Security", "Law Enforcement/Security"): "safety",
    ("Safety and Security", "Community Safety"): "safety",
    ("Safety and Security", "Government Service"): "safety",
    ("Transportation", "Aviation"):          "transportation",
    ("Transportation", "Railway"):           "transportation",
    ("Transportation", "Maritime"):          "transportation",
    ("Transportation", "Mass Transit"):      "transportation",
    ("Transportation", "Highway Roadway Motor Vehicle"): "transportation",
    ("Water Systems", "Potable Water Infrastructure"): "water_infrastructure",
    ("Water Systems", "Wastewater Management"): "water_infrastructure",
    ("Hazardous Materials", "Facilities"):   "fuel",
}


def build_frs_new_pois(
    unmatched_gdf: gpd.GeoDataFrame,
    source_provenance: str,
    weights: dict,
) -> gpd.GeoDataFrame:
    """
    Convert unmatched (and re-geocoded/displaced) FRS records into new silver
    lifeline_point rows.

    Each new POI:
    - Gets a UUID5 lifeline_id derived from "epa_frs/{REGISTRY_ID}"
    - Inherits geometry from the (possibly updated) geometry column
    - Confidence score uses source_score=0.5 (authoritative-only, no OSM match)
    - Copies NAICS/SIC metadata into the new NAICS columns

    Args:
        unmatched_gdf:    FRS GeoDataFrame (may have geocoded columns present)
        source_provenance: "epa_frs" or "epa_frs+overture_geocode"
        weights:          confidence_weights dict from ConflationConfig

    Returns:
        GeoDataFrame of new silver rows (same schema as silver lifeline_points)
    """
    rows = []
    for _, row in unmatched_gdf.iterrows():
        naics_str = row.get("NAICS_CODES", None)
        sic_str = row.get("SIC_CODES", None)
        entry = lookup_code(
            naics_str if pd.notna(naics_str) else None,
            sic_str if pd.notna(sic_str) else None,
        )
        if entry is None:
            continue

        registry_id = str(row.get("REGISTRY_ID", ""))
        lifeline_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"epa_frs/{registry_id}"))

        score_obj = compute_confidence(
            distance_m=0,
            osm_attrs={},
            auth_attrs={"_dummy": 1},
            key_fields=["_dummy"],
            has_osm=False,
            has_authoritative=True,
            weights=weights,
        )
        cs = score_obj.composite
        tier = (
            ConfidenceTier.HIGH if cs >= 0.75
            else ConfidenceTier.MEDIUM if cs >= 0.40
            else ConfidenceTier.LOW
        )

        rows.append({
            "lifeline_id": lifeline_id,
            "display_name": str(row.get("PRIMARY_NAME", "")) or registry_id,
            "osm_category": None,
            "h3_index": None,          # will be filled by add_h3_index if called
            "confidence_score": cs,
            "confidence_tier": tier.value,
            "tmp_osm_layer": _COMPONENT_LAYER_MAP.get((entry["lifeline"], entry["lifeline_component"]), "other"),
            "fema_lifeline": make_fema_lifeline_struct(entry["lifeline_key"]),
            "source_provenance": source_provenance,
            "epa_registry_id": registry_id,
            "naics_codes": str(naics_str) if pd.notna(naics_str) else None,
            "sic_codes": str(sic_str) if pd.notna(sic_str) else None,
            "naics_lifeline_tier": entry["tier"],
            "naics_sector": entry["naics_sector"],
            "geometry": row.geometry,
        })

    if not rows:
        return gpd.GeoDataFrame(columns=[
            "lifeline_id", "display_name", "osm_category", "h3_index",
            "confidence_score", "confidence_tier",
            "tmp_osm_layer", "fema_lifeline",
            "source_provenance",
            "epa_registry_id", "naics_codes", "sic_codes",
            "naics_lifeline_tier", "naics_sector", "geometry",
        ], geometry="geometry", crs="EPSG:4326")

    new_gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")
    return new_gdf.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Step 5: Append minted POIs to silver
# ---------------------------------------------------------------------------

def append_frs_pois_to_silver(
    silver_gdf: gpd.GeoDataFrame,
    new_pois_gdf: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """
    Concatenate minted FRS POIs into the silver GeoDataFrame.
    New POIs are appended; existing records are unchanged.
    Missing columns in either frame are filled with None.
    """
    if len(new_pois_gdf) == 0:
        return silver_gdf

    combined = pd.concat([silver_gdf, new_pois_gdf], ignore_index=True)
    return gpd.GeoDataFrame(combined, geometry="geometry", crs="EPSG:4326")


# ---------------------------------------------------------------------------
# High-level orchestrator
# ---------------------------------------------------------------------------

def run_epa_naics_pipeline(
    silver_gdf: gpd.GeoDataFrame,
    bronze_path: Path | str,
    naics_cfg: object,
    conflation_weights: dict,
    bbox: Optional[list[float]] = None,
) -> tuple[gpd.GeoDataFrame, dict]:
    """
    Full two-pass EPA NAICS pipeline.

    Returns:
        updated silver GeoDataFrame, stats dict with keys:
            pass1_boosted, pass2_boosted, minted_geocoded, minted_frs_only,
            minted_large_displacement, total_minted
    """
    stats: dict = {
        "pass1_boosted": 0,
        "pass2_boosted": 0,
        "minted_geocoded": 0,
        "minted_frs_only": 0,
        "minted_large_displacement": 0,
        "total_minted": 0,
    }

    # Load FRS filtered to lifeline codes
    frs_gdf = load_epa_frs_filtered(bronze_path, bbox)
    if len(frs_gdf) == 0:
        print("  EPA NAICS: no lifeline-coded FRS records found.")
        return silver_gdf, stats

    # -----------------------------------------------------------------------
    # Pass 1: tight 50m spatial match
    # -----------------------------------------------------------------------
    p1_distance = naics_cfg.pass1_match_distance_m
    print(f"  Pass 1: tight {p1_distance}m spatial match...")
    match_df, unmatched_frs = match_epa_to_silver(silver_gdf, frs_gdf, p1_distance)
    stats["pass1_boosted"] = len(match_df)
    print(f"    → {len(match_df):,} boosted, {len(unmatched_frs):,} unmatched")

    if len(match_df) > 0:
        silver_gdf = apply_epa_naics_boost(silver_gdf, frs_gdf, match_df, naics_cfg)

    if not naics_cfg.mint_new_pois or len(unmatched_frs) == 0:
        return silver_gdf, stats

    # -----------------------------------------------------------------------
    # Pass 2: re-geocode + displacement check
    # -----------------------------------------------------------------------
    geocode_path = naics_cfg.geocode_address_path
    if geocode_path:
        print(f"  Pass 2: re-geocoding {len(unmatched_frs):,} unmatched records...")
        unmatched_frs = regeocode_frs_batch(unmatched_frs, geocode_path)
        unmatched_frs = compute_displacement_m(unmatched_frs)

        plausible, large_disp = split_by_displacement(
            unmatched_frs, naics_cfg.max_displacement_m
        )
        print(f"    → {len(plausible):,} plausible geocodes, "
              f"{len(large_disp):,} large-displacement / no-geocode")

        # Pass 2 spatial match on re-geocoded points
        p2_match_df, still_unmatched = match_epa_to_silver(
            silver_gdf, plausible, naics_cfg.pass2_match_distance_m
        )
        stats["pass2_boosted"] = len(p2_match_df)
        print(f"    → Pass 2: {len(p2_match_df):,} additional boosted")

        if len(p2_match_df) > 0:
            silver_gdf = apply_epa_naics_boost(silver_gdf, plausible, p2_match_df, naics_cfg)

        # Mint still-unmatched plausible (re-geocoded point used)
        if len(still_unmatched) > 0:
            new_geocoded = build_frs_new_pois(
                still_unmatched, "epa_frs+overture_geocode", conflation_weights
            )
            stats["minted_geocoded"] = len(new_geocoded)
            silver_gdf = append_frs_pois_to_silver(silver_gdf, new_geocoded)

        # Mint large-displacement group using original FRS coords
        if len(large_disp) > 0:
            new_frs = build_frs_new_pois(
                large_disp, "epa_frs", conflation_weights
            )
            stats["minted_large_displacement"] = len(new_frs)
            silver_gdf = append_frs_pois_to_silver(silver_gdf, new_frs)

    else:
        # No geocoding configured — mint all unmatched from FRS coords
        print(f"  Pass 2: no geocode path configured; minting {len(unmatched_frs):,} from FRS coords")
        new_frs = build_frs_new_pois(unmatched_frs, "epa_frs", conflation_weights)
        stats["minted_frs_only"] = len(new_frs)
        silver_gdf = append_frs_pois_to_silver(silver_gdf, new_frs)

    stats["total_minted"] = (
        stats["minted_geocoded"] + stats["minted_frs_only"] + stats["minted_large_displacement"]
    )
    print(f"  EPA NAICS complete: {stats['pass1_boosted']:,} P1 boosts, "
          f"{stats['pass2_boosted']:,} P2 boosts, {stats['total_minted']:,} new POIs")
    return silver_gdf, stats
