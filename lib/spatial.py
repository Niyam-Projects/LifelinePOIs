"""
Spatial utilities for LifelinePOI: H3 indexing, AOI clipping, and state boundary filtering.
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


def load_state_boundary(
    state_codes: list[str],
    bbox: list[float] | None = None,
    cache_dir: str | None = None,
) -> gpd.GeoDataFrame | None:
    """
    Load high-resolution state/territory boundaries using pygris.

    Strategy: bbox pre-filter first (fast), then return the dissolved union
    polygon for precise intersection filtering of national datasets.

    Args:
        state_codes: List of FIPS-compatible state abbreviations (e.g. ["PR"]).
        bbox:        Optional [min_lon, min_lat, max_lon, max_lat] for a fast
                     pre-filter before fetching the boundary.
        cache_dir:   Directory to cache downloaded TIGER shapefiles.
                     Defaults to pygris default (~/.cache/pygris).

    Returns:
        GeoDataFrame with a single-row dissolved boundary in EPSG:4326,
        or None if pygris is unavailable or the state cannot be found.
    """
    try:
        import pygris
    except ImportError:
        return None

    if not state_codes:
        return None

    kwargs: dict = {"year": 2023, "cb": False}  # cb=False = full-resolution TIGER boundary
    if cache_dir:
        kwargs["cache"] = True

    try:
        parts = []
        for code in state_codes:
            try:
                boundary = pygris.states(state=code, **kwargs)
                parts.append(boundary)
            except Exception:
                # Fallback: cartographic boundary if full-res unavailable
                try:
                    boundary = pygris.states(state=code, year=2023, cb=True)
                    parts.append(boundary)
                except Exception:
                    pass

        if not parts:
            return None

        import pandas as _pd
        combined = gpd.GeoDataFrame(
            _pd.concat(parts, ignore_index=True), crs=parts[0].crs
        ).to_crs("EPSG:4326")

        # Dissolve to a single polygon union
        dissolved = combined.dissolve().reset_index(drop=True)[["geometry"]]
        return dissolved

    except Exception:
        return None


def clip_to_state_boundary(
    gdf: gpd.GeoDataFrame,
    state_codes: list[str],
    bbox: list[float] | None = None,
) -> gpd.GeoDataFrame:
    """
    Filter a GeoDataFrame to records that intersect the actual state boundary.

    Two-step approach for performance:
      1. bbox pre-filter (fast index scan, eliminates most out-of-area records)
      2. precise polygon intersection with high-res TIGER state boundary

    Falls back to bbox-only if pygris is unavailable or the boundary cannot
    be loaded.

    Args:
        gdf:         Input GeoDataFrame in EPSG:4326.
        state_codes: State/territory abbreviations (e.g. ["PR"]).
        bbox:        Bounding box [min_lon, min_lat, max_lon, max_lat].

    Returns:
        Filtered GeoDataFrame (same CRS as input).
    """
    if gdf is None or len(gdf) == 0:
        return gdf

    # Step 1: fast bbox pre-filter
    if bbox is not None:
        gdf = clip_to_bbox(gdf, bbox)
        if len(gdf) == 0:
            return gdf

    # Step 2: precise state boundary intersection
    boundary = load_state_boundary(state_codes, bbox=bbox)
    if boundary is None or len(boundary) == 0:
        print(f"  [spatial] pygris boundary unavailable for {state_codes} — using bbox only")
        return gdf

    try:
        state_union = boundary.geometry.unary_union
        mask = gdf.geometry.intersects(state_union)
        filtered = gdf[mask].copy()
        n_removed = len(gdf) - len(filtered)
        if n_removed > 0:
            print(f"  [spatial] State boundary filter removed {n_removed:,} out-of-boundary records")
        return filtered
    except Exception as e:
        print(f"  [spatial] State boundary intersection failed ({e}) — using bbox only")
        return gdf


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
