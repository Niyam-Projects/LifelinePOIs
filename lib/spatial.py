"""
Spatial utilities for LifelinePOI: H3 indexing and AOI clipping.
"""
from __future__ import annotations

from typing import Optional

import geopandas as gpd
import h3
import pandas as pd
from shapely.geometry import box, mapping


# Default H3 resolution for point indexing (≈ 1.2km edge length)
DEFAULT_H3_RESOLUTION = 8


def add_h3_index(
    gdf: gpd.GeoDataFrame,
    resolution: int = DEFAULT_H3_RESOLUTION,
    column: str = "h3_index",
) -> gpd.GeoDataFrame:
    """
    Add an H3 cell index column to a GeoDataFrame.
    Points are indexed directly; polygons use the centroid.
    """
    from shapely.geometry import Point

    def _h3_for_geom(geom) -> Optional[str]:
        if geom is None or geom.is_empty:
            return None
        if hasattr(geom, "centroid"):
            pt = geom.centroid
        else:
            pt = geom
        try:
            return h3.latlng_to_cell(pt.y, pt.x, resolution)
        except Exception:
            return None

    gdf = gdf.copy()
    gdf[column] = gdf.geometry.apply(_h3_for_geom)
    return gdf


def clip_to_bbox(
    gdf: gpd.GeoDataFrame,
    bbox: list[float] | None,
) -> gpd.GeoDataFrame:
    """
    Clip a GeoDataFrame to a bounding box [min_lon, min_lat, max_lon, max_lat].
    Returns the input unchanged if bbox is None.
    """
    if bbox is None:
        return gdf
    min_lon, min_lat, max_lon, max_lat = bbox
    clip_geom = box(min_lon, min_lat, max_lon, max_lat)
    return gdf.clip(clip_geom)


def nearest_neighbor_join(
    left: gpd.GeoDataFrame,
    right: gpd.GeoDataFrame,
    max_distance_m: float = 500.0,
    left_crs: str = "EPSG:4326",
    projected_crs: str = "EPSG:3857",
) -> gpd.GeoDataFrame:
    """
    Spatial nearest-neighbor join using sklearn BallTree.

    Returns left GDF with columns from right appended (suffixed _auth),
    plus 'match_distance_m' column. Unmatched rows get NaN for right columns.

    Uses Web Mercator (EPSG:3857) for meter-accurate distance calculation.
    """
    import numpy as np
    from sklearn.neighbors import BallTree

    if left.crs is None:
        left = left.set_crs(left_crs)
    if right.crs is None:
        right = right.set_crs(left_crs)

    left_proj = left.to_crs(projected_crs)
    right_proj = right.to_crs(projected_crs)

    # Build BallTree on right centroids
    right_coords = np.array([
        [geom.centroid.x if geom.geom_type != "Point" else geom.x,
         geom.centroid.y if geom.geom_type != "Point" else geom.y]
        for geom in right_proj.geometry
    ])
    tree = BallTree(right_coords, metric="euclidean")

    left_coords = np.array([
        [geom.centroid.x if geom.geom_type != "Point" else geom.x,
         geom.centroid.y if geom.geom_type != "Point" else geom.y]
        for geom in left_proj.geometry
    ])

    distances, indices = tree.query(left_coords, k=1)
    distances = distances.flatten()
    indices = indices.flatten()

    result = left.copy()
    right_reset = right.reset_index(drop=True)

    # Only attach match if within max_distance_m
    matched_mask = distances <= max_distance_m
    result["match_distance_m"] = np.where(matched_mask, distances, float("nan"))
    result["matched_right_idx"] = np.where(matched_mask, indices, -1)

    # Merge right attributes for matched rows
    right_cols = [c for c in right_reset.columns if c != "geometry"]
    for col in right_cols:
        result[f"{col}_auth"] = result["matched_right_idx"].apply(
            lambda idx: right_reset.at[idx, col] if idx >= 0 else None
        )

    return result.drop(columns=["matched_right_idx"])
