"""
Deduplication of hospital/clinic POIs produced by the building-centroid
extraction step in health.sql.

When both an ``amenity=hospital`` node *and* a ``building=hospital`` centroid
(converted from a closed-way polygon) represent the same facility — same name
and within ``proximity_m`` metres — the two records are merged:

- Geometry: the building centroid is kept (more accurate than a hand-placed node).
- Attributes: the amenity node's attribute values fill any gaps on the building row.
- The amenity node duplicate is removed from the silver layer.

This runs as a post-processing step inside Flow 02 (silver conflation), after all
OSM health features have been loaded and assigned UUIDs, but before
``lifeline_points.parquet`` is written.

Candidate identification strategy
----------------------------------
The silver ``lifeline_points`` GeoDataFrame only carries core columns (``lifeline_id``,
``display_name``, ``osm_category``, ``geometry``, etc.).  The extended OSM tags
(``building``, ``amenity``, ``healthcare``) live in ``silver/attr_health.parquet``.

When ``attr_gdf`` is supplied the function joins on ``lifeline_id`` to get the raw
tag columns and identifies candidates precisely.  When it is omitted the function
falls back to a geometry + ``osm_category`` heuristic that works for the typical
OSM health data shape:

- **Building centroids**: ``osm_category`` is NULL (amenity tag absent) AND
  geometry is a Point (centroid derived from a closed-way polygon).
- **Amenity nodes**: ``osm_category IN ('hospital', 'clinic')`` AND geometry is
  a Point (not the campus-boundary Polygon).
"""
from __future__ import annotations

from typing import Optional

import geopandas as gpd
import numpy as np
import pandas as pd


def dedup_hospital_building_centroids(
    health_gdf: gpd.GeoDataFrame,
    attr_gdf: Optional[pd.DataFrame] = None,
    name_threshold: float = 0.85,
    proximity_m: float = 50.0,
    projected_crs: str = "EPSG:3857",
) -> tuple[gpd.GeoDataFrame, int]:
    """Merge amenity=hospital/clinic nodes with nearby building centroid duplicates.

    For each ``building=hospital/clinic`` centroid that has a matching
    ``amenity=hospital/clinic`` node within ``proximity_m`` metres and whose
    name similarity meets ``name_threshold``, this function:

    1. Coalesces attributes: building-centroid values take priority; the node's
       non-null values fill any gaps.
    2. Keeps the building-centroid geometry (more accurate footprint-derived location).
    3. Removes the amenity-node duplicate.

    Args:
        health_gdf: Silver health GeoDataFrame (``tmp_osm_layer == 'health'`` rows).
        attr_gdf: Optional ``attr_health.parquet`` DataFrame.  When provided,
            ``building``, ``amenity``, and ``healthcare`` columns are used for
            precise candidate identification.  When ``None``, a heuristic based
            on ``osm_category`` and geometry type is used instead.
        name_threshold: Minimum rapidfuzz ``token_sort_ratio`` score (0–1) for a
            name match.  Pairs where both names are NULL are also merged.
        proximity_m: Maximum distance in metres for a candidate pair.
        projected_crs: CRS used for metre-accurate BallTree distances.

    Returns:
        ``(deduplicated_gdf, n_merged)`` where ``n_merged`` is the number of
        amenity-node rows removed by merging.
    """
    try:
        from rapidfuzz import fuzz as _fuzz
    except ImportError:  # pragma: no cover
        raise ImportError("rapidfuzz is required for health_dedup; run: pip install rapidfuzz")

    if health_gdf.empty:
        return health_gdf.copy(), 0

    gdf = health_gdf.copy().reset_index(drop=True)

    # ── Identify the two candidate populations ────────────────────────────────

    if attr_gdf is not None and "lifeline_id" in attr_gdf.columns:
        # Precise: join attr table tags onto the silver rows
        _tag_cols = [c for c in ("building", "amenity", "healthcare") if c in attr_gdf.columns]
        _attr_slim = attr_gdf[["lifeline_id"] + _tag_cols].copy()
        gdf = gdf.merge(_attr_slim, on="lifeline_id", how="left", suffixes=("", "_attr"))

        _building = gdf.get("building", pd.Series(dtype=object, index=gdf.index))
        _amenity  = gdf.get("amenity",  pd.Series(dtype=object, index=gdf.index))
        _hlthcare = gdf.get("healthcare", pd.Series(dtype=object, index=gdf.index))

        bldg_mask = (
            _building.isin(["hospital", "clinic"])
            & _amenity.isna()
            & _hlthcare.isna()
        )
        node_mask = (
            (
                _amenity.isin(["hospital", "clinic"])
                | _hlthcare.isin(["hospital", "clinic"])
            )
            & (gdf.geometry.geom_type == "Point")
        )
    else:
        # Heuristic: building centroids have no amenity tag → osm_category is NULL
        _geom_type = gdf.geometry.geom_type
        _osm_cat   = gdf.get("osm_category", pd.Series(dtype=object, index=gdf.index))

        bldg_mask = _osm_cat.isna() & (_geom_type == "Point")
        node_mask = (
            _osm_cat.isin(["hospital", "clinic"])
            & (_geom_type == "Point")
        )

    bldg_df = gdf[bldg_mask].copy()
    node_df  = gdf[node_mask].copy()

    if bldg_df.empty or node_df.empty:
        return health_gdf.copy(), 0

    # ── Spatial proximity filter (BallTree) ───────────────────────────────────

    bldg_proj = bldg_df.to_crs(projected_crs)
    node_proj  = node_df.to_crs(projected_crs)

    from sklearn.neighbors import BallTree as _BallTree

    node_coords = np.array([[g.x, g.y] for g in node_proj.geometry])
    bldg_coords = np.array([[g.x, g.y] for g in bldg_proj.geometry])

    tree = _BallTree(node_coords, metric="euclidean")
    distances, indices = tree.query(bldg_coords, k=1)
    distances = distances.flatten()
    indices   = indices.flatten()

    bldg_reset = bldg_df.reset_index(drop=True)
    node_reset = node_df.reset_index(drop=True)

    # ── Name-similarity filter ────────────────────────────────────────────────

    node_rows_to_drop: set[int] = set()  # original index values in gdf to remove
    merge_updates: dict[int, dict] = {}   # original index → coalesced attr updates

    for bldg_pos, (dist, node_pos) in enumerate(zip(distances, indices)):
        if dist > proximity_m:
            continue

        bldg_row = bldg_reset.iloc[bldg_pos]
        node_row = node_reset.iloc[int(node_pos)]

        bldg_name = bldg_row.get("display_name") or bldg_row.get("name")
        node_name = node_row.get("display_name") or node_row.get("name")

        both_unnamed = _is_empty(bldg_name) and _is_empty(node_name)
        if not both_unnamed and not _is_empty(bldg_name) and not _is_empty(node_name):
            score = _fuzz.token_sort_ratio(str(bldg_name), str(node_name)) / 100.0
            if score < name_threshold:
                continue

        merged_attrs = _coalesce_attrs(bldg_row, node_row)

        bldg_orig_idx = bldg_df.index[bldg_pos]
        node_orig_idx = node_df.index[int(node_pos)]

        merge_updates[bldg_orig_idx] = merged_attrs
        node_rows_to_drop.add(node_orig_idx)

    if not node_rows_to_drop:
        return health_gdf.copy(), 0

    # ── Apply updates + drop node duplicates ─────────────────────────────────

    result = gdf.copy()
    for orig_idx, attrs in merge_updates.items():
        for col, val in attrs.items():
            if col in result.columns:
                result.at[orig_idx, col] = val

    result = result.drop(index=list(node_rows_to_drop)).reset_index(drop=True)

    # Drop any temp join columns added from attr_gdf
    extra_cols = [c for c in ("building", "amenity", "healthcare") if c not in health_gdf.columns and c in result.columns]
    if extra_cols:
        result = result.drop(columns=extra_cols)

    return gpd.GeoDataFrame(result, geometry="geometry", crs=health_gdf.crs), len(node_rows_to_drop)


def _is_empty(val) -> bool:
    """Return True if val is None, NaN, or a blank/sentinel string."""
    if val is None:
        return True
    s = str(val).strip().lower()
    return s in ("", "nan", "none", "null")


def _coalesce_attrs(primary: pd.Series, secondary: pd.Series) -> dict:
    """Return attribute updates: secondary fills gaps in primary.

    Primary values win; secondary values are used only when the primary is
    null/empty.  Geometry and key identity columns are always skipped.
    """
    skip_cols = {"geometry", "lifeline_id", "type", "id", "bbox", "names"}
    updates: dict = {}
    for col in secondary.index:
        if col in skip_cols:
            continue
        sec_val = secondary.get(col)
        if _is_empty(sec_val):
            continue
        if _is_empty(primary.get(col)):
            updates[col] = sec_val
    return updates
