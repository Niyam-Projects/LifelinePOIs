"""
HIFLD Validation utilities for LifelinePOI.

Downloads/loads archived HIFLD layers from the seerai-hifld-archive GCS bucket
and uses them to boost confidence in OSM-sourced points and refine telecom tower
type classifications.

Philosophy:
  - HIFLD data is stale (archived). We NEVER create new POIs from HIFLD alone.
  - A spatial match within `max_distance_m` boosts the source_score from 0.25
    (OSM-only) to 1.0 (OSM + authoritative).
  - For telecom: matching against cellular/microwave/lm_private/lm_commercial
    layers adds a `tower_type_hifld` attribute for type refinement.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional

import geopandas as gpd
import pandas as pd
import numpy as np

from lib.scoring import compute_confidence, ConfidenceTier


# Telecom HIFLD layer priority order for type classification (most to least specific)
TELECOM_TYPE_PRIORITY = ["cellular", "microwave", "lm_commercial", "lm_private"]


def download_hifld_layer(gcs_path: str, dest_path: Path) -> bool:
    """
    Download a HIFLD parquet file from public GCS to dest_path.
    Returns True if downloaded, False if already exists (cached).
    Skips download if dest_path already exists.
    """
    if dest_path.exists():
        return False

    from google.cloud import storage as gcs

    dest_path.parent.mkdir(parents=True, exist_ok=True)

    # Parse gs://bucket/blob
    if not gcs_path.startswith("gs://"):
        raise ValueError(f"Invalid GCS URL: {gcs_path}")
    parts = gcs_path[5:].split("/", 1)
    bucket_name, blob_path = parts[0], parts[1]

    client = gcs.Client.create_anonymous_client()
    bucket = client.bucket(bucket_name)

    tmp_dir = tempfile.mkdtemp(prefix="hifld_dl_")
    try:
        blob = bucket.blob(blob_path)
        blob.reload()
        tmp_file = os.path.join(tmp_dir, os.path.basename(blob_path))
        blob.download_to_filename(tmp_file)
        shutil.move(tmp_file, str(dest_path))
    except Exception:
        # Try as folder (partition)
        prefix = blob_path if blob_path.endswith("/") else blob_path + "/"
        blobs = [b for b in bucket.list_blobs(prefix=prefix) if b.name.endswith(".parquet")]
        if not blobs:
            raise ValueError(f"No parquet files found at {gcs_path}")
        # Merge multiple partition files into one GeoDataFrame and save
        frames = []
        for b in blobs:
            tmp_file = os.path.join(tmp_dir, os.path.basename(b.name))
            b.download_to_filename(tmp_file)
            frames.append(pd.read_parquet(tmp_file))
        combined = pd.concat(frames, ignore_index=True)
        combined.to_parquet(str(dest_path), index=False)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return True


def load_hifld_layer(
    bronze_path: Path,
    layer_name: str,
    lon_field: str,
    lat_field: str,
    bbox: Optional[list[float]] = None,
) -> Optional[gpd.GeoDataFrame]:
    """
    Load a HIFLD bronze parquet as a GeoDataFrame.
    Handles dotted field names like 'properties.londec' by looking up
    nested dict columns or flattened column names.
    Returns None if file doesn't exist.
    """
    parquet_path = Path(bronze_path) / "hifld" / f"{layer_name}.parquet"
    if not parquet_path.exists():
        print(f"  WARNING: HIFLD bronze not found: {parquet_path} — skipping")
        return None

    df = pd.read_parquet(parquet_path)

    # Resolve potentially dotted field names (e.g. "properties.londec")
    def _resolve_field(df: pd.DataFrame, field: str) -> pd.Series:
        if field in df.columns:
            return pd.to_numeric(df[field], errors="coerce")
        # Try flattened version (remove dot prefix)
        flat = field.split(".")[-1]
        if flat in df.columns:
            return pd.to_numeric(df[flat], errors="coerce")
        # Try "properties" nested struct
        if "." in field and "properties" in df.columns:
            sub_key = field.split(".", 1)[1]
            try:
                return pd.to_numeric(df["properties"].apply(
                    lambda p: p.get(sub_key) if isinstance(p, dict) else None
                ), errors="coerce")
            except Exception:
                pass
        return pd.Series([None] * len(df), dtype=float)

    lons = _resolve_field(df, lon_field)
    lats = _resolve_field(df, lat_field)

    valid = lons.notna() & lats.notna()
    df = df[valid].copy()
    lons = lons[valid]
    lats = lats[valid]

    from shapely.geometry import Point
    geometries = [Point(lon, lat) for lon, lat in zip(lons, lats)]
    gdf = gpd.GeoDataFrame(df, geometry=geometries, crs="EPSG:4326")

    if bbox is not None:
        from lib.spatial import clip_to_bbox
        gdf = clip_to_bbox(gdf, bbox)

    return gdf.reset_index(drop=True)


def match_hifld(
    osm_gdf: gpd.GeoDataFrame,
    hifld_gdf: gpd.GeoDataFrame,
    max_distance_m: float = 50.0,
    projected_crs: str = "EPSG:3857",
) -> np.ndarray:
    """
    Spatial nearest-neighbor match between OSM points and HIFLD points.
    Returns a boolean array (len = len(osm_gdf)) — True where a HIFLD point
    was found within max_distance_m.
    """
    from sklearn.neighbors import BallTree

    if len(hifld_gdf) == 0:
        return np.zeros(len(osm_gdf), dtype=bool)

    osm_proj = osm_gdf.to_crs(projected_crs)
    hifld_proj = hifld_gdf.to_crs(projected_crs)

    def _coords(gdf):
        return np.array([
            [g.centroid.x if g.geom_type != "Point" else g.x,
             g.centroid.y if g.geom_type != "Point" else g.y]
            for g in gdf.geometry
        ])

    tree = BallTree(_coords(hifld_proj), metric="euclidean")
    distances, _ = tree.query(_coords(osm_proj), k=1)
    distances = distances.flatten()
    return distances <= max_distance_m


def apply_hifld_boost(
    core_gdf: gpd.GeoDataFrame,
    layer_filter: str,
    matched_mask: np.ndarray,
    weights: dict,
) -> gpd.GeoDataFrame:
    """
    Update confidence_score, confidence_tier, and source_provenance for
    OSM points that matched a HIFLD layer.

    Only rows where tmp_osm_layer == layer_filter are candidates.
    matched_mask must be aligned to the rows of core_gdf[layer_mask].
    """
    core_gdf = core_gdf.copy()
    layer_mask = core_gdf["tmp_osm_layer"] == layer_filter
    layer_indices = core_gdf.index[layer_mask]

    for i, idx in enumerate(layer_indices):
        if matched_mask[i]:
            # Re-score with both OSM and authoritative present, full distance score
            new_score = compute_confidence(
                distance_m=0,
                osm_attrs={"_dummy": 1},
                auth_attrs={"_dummy": 1},
                key_fields=["_dummy"],
                has_osm=True,
                has_authoritative=True,
                weights=weights,
            )
            core_gdf.at[idx, "confidence_score"] = new_score.composite
            tier = (
                ConfidenceTier.HIGH if new_score.composite >= 0.75
                else ConfidenceTier.MEDIUM if new_score.composite >= 0.40
                else ConfidenceTier.LOW
            )
            core_gdf.at[idx, "confidence_tier"] = tier.value
            current_prov = core_gdf.at[idx, "source_provenance"]
            if "hifld" not in str(current_prov):
                core_gdf.at[idx, "source_provenance"] = f"{current_prov}+hifld"

    return core_gdf


def classify_telecom_tower_type(
    telecom_gdf: gpd.GeoDataFrame,
    hifld_layers: dict[str, gpd.GeoDataFrame],
    max_distance_m: float = 50.0,
) -> pd.Series:
    """
    For each OSM telecom point, determine HIFLD tower type by checking
    all 4 telecom HIFLD layers in priority order.

    Priority: cellular > microwave > lm_commercial > lm_private

    Returns a pd.Series of tower_type_hifld values (None if no match).
    """
    from sklearn.neighbors import BallTree

    result = pd.Series([None] * len(telecom_gdf), dtype=object, index=telecom_gdf.index)

    projected_crs = "EPSG:3857"
    osm_proj = telecom_gdf.to_crs(projected_crs)

    def _coords(gdf):
        return np.array([
            [g.centroid.x if g.geom_type != "Point" else g.x,
             g.centroid.y if g.geom_type != "Point" else g.y]
            for g in gdf.geometry
        ])

    osm_coords = _coords(osm_proj)

    # Process in reverse priority so higher-priority layers overwrite
    for layer_name in reversed(TELECOM_TYPE_PRIORITY):
        hifld_gdf = hifld_layers.get(layer_name)
        if hifld_gdf is None or len(hifld_gdf) == 0:
            continue

        hifld_proj = hifld_gdf.to_crs(projected_crs)
        tree = BallTree(_coords(hifld_proj), metric="euclidean")
        distances, _ = tree.query(osm_coords, k=1)
        distances = distances.flatten()

        matched = distances <= max_distance_m
        result[telecom_gdf.index[matched]] = layer_name

    return result
