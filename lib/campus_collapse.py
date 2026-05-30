"""
Campus-style POI collapse for LifelinePOI.

Hospitals, universities, schools, and colleges in OSM often have many features
mapped on a single campus: a campus boundary polygon (amenity=hospital/university/
school/college), individual building nodes or footprint polygons, and ancillary
service points.  This module collapses all sub-features that fall within a campus
boundary into one primary campus POI, preserving the sub-features in a secondary
layer and the polygon boundary for future bridging.

Gold-level output: one point per campus.
"""
from __future__ import annotations

from typing import Optional

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point


# ── ISCED level → FEMA lifeline key ──────────────────────────────────────────

_ISCED_TO_FEMA_KEY: dict[str, str] = {
    "0": "government_schools",
    "1": "government_schools",
    "2": "government_schools",
    "3": "government_schools",
    "4": "government_schools",
    "5": "essential_government_functions",
    "6": "essential_government_functions",
    "7": "essential_government_functions",
    "8": "essential_government_functions",
}

_UNIVERSITY_FEMA_STRUCT = {
    "primary": "essential_government_functions",
    "hierarchy": ["safety_and_security", "government_service", "essential_government_functions"],
    "alternates": [],
}
_SCHOOL_FEMA_STRUCT = {
    "primary": "government_schools",
    "hierarchy": ["safety_and_security", "government_service", "government_schools"],
    "alternates": [],
}

# Default fema_lifeline struct per amenity type (used when no ISCED tag present)
_AMENITY_DEFAULT_FEMA: dict[str, dict] = {
    "hospital": {
        "primary": "hospitals",
        "hierarchy": ["health_and_medical", "medical_care", "hospitals"],
        "alternates": [],
    },
    "university": _UNIVERSITY_FEMA_STRUCT,
    "college": _UNIVERSITY_FEMA_STRUCT,
    "school": _SCHOOL_FEMA_STRUCT,
    "kindergarten": _SCHOOL_FEMA_STRUCT,
}


def isced_to_fema_key(isced_level: Optional[str]) -> Optional[str]:
    """Map an ``isced:level`` OSM tag value to a FEMA lifeline leaf key.

    Handles multi-value tags like ``"1;2"`` by taking the highest level present.
    Returns ``None`` if the input cannot be parsed.
    """
    if not isced_level:
        return None
    parts = [p.strip() for p in str(isced_level).split(";")]
    numeric = [p for p in parts if p.isdigit()]
    if not numeric:
        return None
    max_level = max(numeric, key=int)
    return _ISCED_TO_FEMA_KEY.get(max_level)


# ── Feature separation ────────────────────────────────────────────────────────

def separate_campus_features(
    layer_gdf: gpd.GeoDataFrame,
    campus_amenities: list[str],
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Split a silver layer into campus boundary polygons and campus sub-features.

    Campus polygons: Polygon/MultiPolygon geometry AND ``osm_category`` in
    ``campus_amenities``.  Everything else is a campus sub-feature (nodes,
    building-footprint polygons, ancillary service points).

    Returns ``(polygons_gdf, sub_features_gdf)``.
    """
    is_polygon = layer_gdf.geometry.geom_type.isin(["Polygon", "MultiPolygon"])
    osm_cat = layer_gdf.get("osm_category", pd.Series(dtype=object, index=layer_gdf.index))
    is_campus = osm_cat.isin(campus_amenities)

    poly_mask = is_polygon & is_campus
    return layer_gdf[poly_mask].copy(), layer_gdf[~poly_mask].copy()


# ── Spatial grouping ──────────────────────────────────────────────────────────

def group_points_by_polygon(
    sub_features: gpd.GeoDataFrame,
    campus_polygons: gpd.GeoDataFrame,
    projected_crs: str = "EPSG:3857",
) -> dict[int, list[int]]:
    """For each campus polygon (iloc), find sub-feature iloc indices within it.

    Uses centroid-in-polygon (``within`` predicate via geopandas sjoin).
    Sub-features that fall inside multiple polygons are assigned to the first
    match (index order of ``campus_polygons``).

    Returns ``{polygon_iloc: [sub_feature_iloc, ...]}``.
    """
    if len(campus_polygons) == 0 or len(sub_features) == 0:
        return {}

    poly_proj = campus_polygons.reset_index(drop=True).to_crs(projected_crs)
    pts_proj = sub_features.reset_index(drop=True).to_crs(projected_crs)

    # Use centroids for non-point geometries
    centroids = pts_proj.geometry.apply(
        lambda g: g.centroid if g.geom_type != "Point" else g
    )
    pts_centroid = pts_proj.copy()
    pts_centroid["geometry"] = centroids

    joined = gpd.sjoin(
        pts_centroid[["geometry"]],
        poly_proj[["geometry"]],
        how="left",
        predicate="within",
    )

    # De-duplicate: each sub-feature assigned to at most one polygon (first match)
    joined_dedup = joined.reset_index().drop_duplicates(subset="index", keep="first")

    groups: dict[int, list[int]] = {}
    for _, row in joined_dedup.iterrows():
        poly_iloc = row.get("index_right")
        if pd.notna(poly_iloc):
            groups.setdefault(int(poly_iloc), []).append(int(row["index"]))

    return groups


# ── Attribute conflation ──────────────────────────────────────────────────────

def _first_non_null(series: pd.Series) -> object:
    """Return first non-null, non-empty value from a Series."""
    valid = series.dropna()
    valid = valid[valid.astype(str).str.strip().str.lower().ne("nan").ne("none").ne("")]
    return valid.iloc[0] if len(valid) > 0 else None


def conflate_campus_attributes(
    polygon_row: pd.Series,
    group_rows: pd.DataFrame,
) -> dict:
    """Merge attributes from campus sub-features into the campus primary record.

    Conflation rules:
    - ``emergency``, ``wheelchair``: ``'yes'`` if ANY member has it
    - ``beds``, ``capacity``: numeric sum (null-safe)
    - ``name``, ``operator``, ``operator:wikidata``: polygon value → first non-null
    - ``phone``, ``email``, ``website``, ``opening_hours``: polygon → first non-null
    - ``speciality``, ``healthcare:speciality``: pipe-joined union of distinct values
    - FEMA fields: polygon record if available, else highest-confidence sub-feature
    """
    merged = polygon_row.to_dict()

    all_rows = (
        pd.concat([group_rows, polygon_row.to_frame().T], ignore_index=True)
        if len(group_rows) > 0
        else polygon_row.to_frame().T.copy()
    )

    # Boolean: 'yes' if any member has it
    for col in ("emergency", "wheelchair"):
        if col in all_rows.columns:
            has_yes = (all_rows[col].astype(str).str.strip().str.lower() == "yes").any()
            if has_yes:
                merged[col] = "yes"

    # Numeric: sum
    for col in ("beds", "capacity"):
        if col in all_rows.columns:
            vals = pd.to_numeric(all_rows[col], errors="coerce")
            total = vals.sum() if vals.notna().any() else None
            if total and total > 0:
                merged[col] = str(int(total))

    # Text: polygon first, then first non-null in group
    text_cols = ("name", "operator", "operator:wikidata", "phone", "email",
                 "website", "opening_hours", "ref")
    for col in text_cols:
        poly_val = polygon_row.get(col)
        poly_valid = (
            poly_val is not None
            and str(poly_val).strip().lower() not in ("", "nan", "none")
        )
        if not poly_valid and len(group_rows) > 0 and col in group_rows.columns:
            merged[col] = _first_non_null(group_rows[col])

    # Multi-value union: pipe-joined distinct non-null values
    for col in ("speciality", "healthcare:speciality"):
        if col in all_rows.columns:
            vals = (
                all_rows[col].dropna().astype(str)
                .str.strip().replace("nan", np.nan).replace("none", np.nan).dropna()
            )
            distinct = list(dict.fromkeys(v for v in vals if v))
            merged[col] = "|".join(distinct) if distinct else merged.get(col)

    # FEMA struct: polygon's if present, else highest-confidence sub-feature
    poly_has_fema = (
        merged.get("fema_lifeline") is not None
        and isinstance(merged.get("fema_lifeline"), dict)
        and merged["fema_lifeline"].get("primary") not in (None, "")
    )
    if not poly_has_fema and len(group_rows) > 0:
        fema_cand = group_rows[
            group_rows.get("fema_lifeline", pd.Series(dtype=object)).apply(
                lambda x: isinstance(x, dict) and x.get("primary") is not None
            )
        ] if "fema_lifeline" in group_rows.columns else pd.DataFrame()
        if len(fema_cand) > 0:
            best = (
                fema_cand.sort_values("confidence_score", ascending=False).iloc[0]
                if "confidence_score" in fema_cand.columns
                else fema_cand.iloc[0]
            )
            if "fema_lifeline" in best.index:
                merged["fema_lifeline"] = best["fema_lifeline"]

    return merged


# ── ISCED re-categorisation ───────────────────────────────────────────────────

def apply_isced_recategorisation(
    merged: dict,
    amenity_val: Optional[str],
    isced_level: Optional[str],
) -> dict:
    """Adjust FEMA taxonomy for school polygons based on their ISCED level.

    Only modifies records where ``amenity_val == 'school'`` and
    ``isced_level`` is set.  If the ISCED level maps to a university-tier
    key, the fema_lifeline struct is promoted accordingly.
    """
    if str(amenity_val) != "school" or not isced_level:
        return merged

    fema_key = isced_to_fema_key(isced_level)
    if not fema_key:
        return merged

    if fema_key == "essential_government_functions":
        merged["fema_lifeline"] = _UNIVERSITY_FEMA_STRUCT.copy()
    else:
        merged["fema_lifeline"] = _SCHOOL_FEMA_STRUCT.copy()

    return merged


# ── Main collapse orchestrator ────────────────────────────────────────────────

def collapse_campus_layer(
    silver_gdf: gpd.GeoDataFrame,
    layer_name: str,
    campus_amenities: list[str],
    attr_gdf: Optional[pd.DataFrame] = None,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Collapse campus-style POIs in one silver OSM layer.

    Args:
        silver_gdf: Full silver ``lifeline_points`` GeoDataFrame (all layers).
        layer_name: OSM layer to process, e.g. ``'health'`` or ``'education'``.
        campus_amenities: Amenity values whose polygons define campus boundaries.
        attr_gdf: Optional extended attribute table for the layer.  Used to
            retrieve ``isced:level`` for school ISCED recategorisation.

    Returns:
        ``(updated_silver, campus_buildings, campus_polygons)``

        - ``updated_silver``: primary campus POIs replace original polygon records;
          sub-features collapsed into them are removed.  Standalone POIs (not
          inside any campus polygon) are left unchanged.
        - ``campus_buildings``: sub-features with ``osm_campus_polygon_id`` and
          ``campus_primary_lifeline_id`` back-links.
        - ``campus_polygons``: campus boundary polygon geometries with
          ``campus_primary_lifeline_id`` links.
    """
    layer_mask = silver_gdf["tmp_osm_layer"] == layer_name
    layer_gdf = silver_gdf[layer_mask].copy().reset_index(drop=True)
    other_gdf = silver_gdf[~layer_mask].copy()

    campus_polys, sub_features = separate_campus_features(layer_gdf, campus_amenities)

    if len(campus_polys) == 0:
        print(f"  campus_collapse [{layer_name}]: no campus polygons found — skipping")
        return silver_gdf.copy(), gpd.GeoDataFrame(), gpd.GeoDataFrame()

    # Optionally attach isced:level to campus polygon rows for school reclassification
    isced_map: dict[str, str] = {}
    if attr_gdf is not None and "isced:level" in attr_gdf.columns and "lifeline_id" in attr_gdf.columns:
        for _, r in attr_gdf[attr_gdf["isced:level"].notna()].iterrows():
            isced_map[str(r["lifeline_id"])] = str(r["isced:level"])

    groups = group_points_by_polygon(sub_features, campus_polys)

    polys_reset = campus_polys.reset_index(drop=True)
    pts_reset = sub_features.reset_index(drop=True)

    primary_records: list[dict] = []
    buildings_records: list[dict] = []
    polygon_records: list[dict] = []
    collapsed_sub_indices: set[int] = set()

    for poly_iloc in range(len(polys_reset)):
        poly_row = polys_reset.iloc[poly_iloc]
        poly_geom = poly_row.geometry
        poly_lifeline_id = str(poly_row.get("lifeline_id", ""))

        sub_ilocs = groups.get(poly_iloc, [])
        group_rows = pts_reset.iloc[sub_ilocs].copy() if sub_ilocs else pd.DataFrame(columns=pts_reset.columns)
        collapsed_sub_indices.update(sub_ilocs)

        # Conflate attributes from sub-features into the polygon record
        merged = conflate_campus_attributes(poly_row, group_rows)

        # Apply ISCED recategorisation for school campuses
        amenity_val = poly_row.get("osm_category")
        isced_val = isced_map.get(poly_lifeline_id)
        merged = apply_isced_recategorisation(merged, amenity_val, isced_val)

        # Derive stable campus polygon ID string
        osm_type = poly_row.get("type", "way") or "way"
        osm_id = poly_row.get("id", "")
        osm_campus_polygon_id = f"osm/{osm_type}/{osm_id}" if osm_id else poly_lifeline_id

        # Primary campus point: centroid of the campus polygon
        merged["geometry"] = poly_geom.centroid
        merged["osm_campus_polygon_id"] = osm_campus_polygon_id
        merged["campus_feature_count"] = len(sub_ilocs) + 1
        primary_records.append(merged)

        # Sub-features: retain with back-links
        for pt_iloc in sub_ilocs:
            pt_dict = pts_reset.iloc[pt_iloc].to_dict()
            pt_dict["osm_campus_polygon_id"] = osm_campus_polygon_id
            pt_dict["campus_primary_lifeline_id"] = poly_lifeline_id
            buildings_records.append(pt_dict)

        # Campus polygon: boundary geometry + link to primary
        poly_dict = poly_row.to_dict()
        poly_dict["geometry"] = poly_geom
        poly_dict["campus_primary_lifeline_id"] = poly_lifeline_id
        poly_dict["osm_campus_polygon_id"] = osm_campus_polygon_id
        polygon_records.append(poly_dict)

    # Standalone sub-features not inside any polygon remain on the primary layer
    standalone = pts_reset.iloc[
        [i for i in range(len(pts_reset)) if i not in collapsed_sub_indices]
    ].copy()

    n_polys = len(polys_reset)
    n_collapsed = len(collapsed_sub_indices)
    n_standalone = len(standalone)
    print(
        f"  campus_collapse [{layer_name}]: {n_polys} campus polygons, "
        f"{n_collapsed} sub-features collapsed, {n_standalone} standalone"
    )

    # Build output GeoDataFrames
    def _to_gdf(records: list[dict], crs: str = "EPSG:4326") -> gpd.GeoDataFrame:
        if not records:
            return gpd.GeoDataFrame(columns=["geometry"], crs=crs)
        df = pd.DataFrame(records)
        return gpd.GeoDataFrame(df, geometry="geometry", crs=crs)

    primary_gdf = _to_gdf(primary_records)
    buildings_gdf = _to_gdf(buildings_records)
    campus_polygons_gdf = _to_gdf(polygon_records)

    # Reconstruct the layer: campus primaries + standalone sub-features
    updated_layer = gpd.GeoDataFrame(
        pd.concat([primary_gdf, standalone], ignore_index=True),
        geometry="geometry",
        crs="EPSG:4326",
    )

    updated_silver = gpd.GeoDataFrame(
        pd.concat([other_gdf, updated_layer], ignore_index=True),
        geometry="geometry",
        crs="EPSG:4326",
    )

    return updated_silver, buildings_gdf, campus_polygons_gdf


# ── CMS attribute aggregation after collapse ──────────────────────────────────

def aggregate_cms_attrs_for_campuses(silver_path: "Path") -> int:
    """Aggregate CMS ``_cnt`` fields for collapsed hospital campus groups.

    After campus collapse, sub-feature ``lifeline_id``s are moved to
    ``campus_buildings.parquet`` and replaced in ``lifeline_points.parquet``
    by a single campus-primary record.  The CMS attribute table
    (``attr_health_cms.parquet``) is still keyed on the original sub-feature
    IDs, leaving the campus primary without bed/staffing counts.

    This function:

    1. Reads ``campus_buildings.parquet`` to map each collapsed health
       sub-feature to its campus primary ``lifeline_id``.
    2. For each campus primary, collects CMS rows for itself *and* all
       sub-features, **deduplicates by** ``cms_provider_num`` (keeping the
       highest-score match per provider), then sums every ``cms_*_cnt``
       column across the de-duplicated group.  This prevents sub-features
       that all matched the same CMS record from having their bed/staffing
       counts summed multiple times.
    3. For non-count metadata columns uses the *best* value:
       - ``cms_match_score``      → maximum (most confident match in group)
       - ``cms_match_distance_m`` → minimum (closest geocode hit in group)
       - ``cms_match_method``     → ``"spatial"`` if any hit is spatial, else
                                    ``"zip_fuzzy"``
       - ``cms_provider_num``     → value from highest-score row in group
    4. Replaces all per-sub-feature rows with one merged row per campus
       primary, removes orphaned sub-feature rows, and writes the result back
       to ``attr_health_cms.parquet``.

    Parameters
    ----------
    silver_path:
        Path to the silver data directory (contains ``attr_health_cms.parquet``
        and ``campus_buildings.parquet``).

    Returns
    -------
    int
        Number of campus primaries whose CMS attributes were aggregated.
    """
    from pathlib import Path as _Path
    import pandas as _pd

    silver_path = _Path(silver_path)
    cms_path = silver_path / "attr_health_cms.parquet"
    buildings_path = silver_path / "campus_buildings.parquet"

    if not cms_path.exists():
        return 0
    if not buildings_path.exists():
        return 0

    cms = _pd.read_parquet(cms_path)
    if len(cms) == 0:
        return 0

    buildings = _pd.read_parquet(buildings_path)
    if len(buildings) == 0:
        return 0

    # Restrict to health-layer buildings that have a primary link
    if "tmp_osm_layer" in buildings.columns:
        buildings = buildings[buildings["tmp_osm_layer"] == "health"].copy()
    if "campus_primary_lifeline_id" not in buildings.columns or len(buildings) == 0:
        return 0

    # Map: campus_primary_lifeline_id → set of sub-feature lifeline_ids
    primary_to_subs: dict[str, set[str]] = {}
    for _, row in buildings.dropna(subset=["campus_primary_lifeline_id", "lifeline_id"]).iterrows():
        primary = str(row["campus_primary_lifeline_id"])
        sub = str(row["lifeline_id"])
        primary_to_subs.setdefault(primary, set()).add(sub)

    if not primary_to_subs:
        return 0

    # Detect cms_*_cnt numeric columns
    cnt_cols = [c for c in cms.columns if c.startswith("cms_") and c.endswith("_cnt")]
    # Metadata columns (non-sum)
    meta_cols = ["cms_provider_num", "cms_match_score", "cms_match_method", "cms_match_distance_m"]

    cms_indexed = cms.set_index("lifeline_id")
    rows_to_remove: set[str] = set()
    new_rows: list[dict] = []
    updated = 0

    for primary_id, sub_ids in primary_to_subs.items():
        all_ids = sub_ids | {primary_id}
        group = cms_indexed[cms_indexed.index.isin(all_ids)]

        if len(group) == 0:
            continue  # no CMS data for any member of this campus

        # Capture ALL original group IDs before any dedup — needed to cleanly remove
        # every sub-feature row (including duplicates) from the output table.
        _original_group_ids = set(group.index.astype(str).tolist())

        # Dedup by cms_provider_num before summing to avoid counting the same
        # provider's beds multiple times.  When 3 sub-features all match the
        # same CMS record (e.g. 187 beds each), sum would produce 3×187=561;
        # keeping only the best-scoring match per provider gives the correct 187.
        if "cms_provider_num" in group.columns and len(group) > 1:
            _prov = group["cms_provider_num"].astype(str).str.strip()
            _valid_prov = _prov.notna() & (_prov != "") & (_prov.str.lower() != "nan") & (_prov.str.lower() != "none")
            if _valid_prov.any():
                _grp = group.copy()
                if "cms_match_score" in _grp.columns:
                    _grp["_sort_key"] = _pd.to_numeric(_grp["cms_match_score"], errors="coerce").fillna(0)
                    _grp = _grp.sort_values("_sort_key", ascending=False).drop(columns=["_sort_key"])
                _with_prov = _grp[_valid_prov].drop_duplicates(subset=["cms_provider_num"])
                _without_prov = _grp[~_valid_prov]
                group = _pd.concat([_with_prov, _without_prov])

        row: dict = {"lifeline_id": primary_id}

        # Sum all _cnt columns (null-safe)
        for col in cnt_cols:
            if col in group.columns:
                numeric = _pd.to_numeric(group[col], errors="coerce")
                total = numeric.sum() if numeric.notna().any() else _pd.NA
                row[col] = int(total) if _pd.notna(total) else _pd.NA
            else:
                row[col] = _pd.NA

        # Metadata: best values from the group
        if "cms_match_score" in group.columns:
            scores = _pd.to_numeric(group["cms_match_score"], errors="coerce")
            best_idx = scores.idxmax() if scores.notna().any() else group.index[0]
            best_row = group.loc[best_idx]
        else:
            best_row = group.iloc[0]

        row["cms_provider_num"] = str(best_row.get("cms_provider_num", "") or "")
        row["cms_match_score"] = float(best_row.get("cms_match_score", 0.0) or 0.0)

        if "cms_match_method" in group.columns:
            methods = group["cms_match_method"].dropna().astype(str)
            row["cms_match_method"] = "spatial" if (methods == "spatial").any() else "zip_fuzzy"
        else:
            row["cms_match_method"] = str(best_row.get("cms_match_method", "") or "")

        if "cms_match_distance_m" in group.columns:
            dists = _pd.to_numeric(group["cms_match_distance_m"], errors="coerce")
            row["cms_match_distance_m"] = float(dists.min()) if dists.notna().any() else None
        else:
            row["cms_match_distance_m"] = None

        new_rows.append(row)
        rows_to_remove.update(_original_group_ids)  # remove all original IDs, not just deduped
        updated += 1

    if not new_rows:
        return 0

    # Remove old rows (primary + all sub-features for touched campuses)
    cms_keep = cms[~cms["lifeline_id"].isin(rows_to_remove)].copy()
    cms_new = _pd.DataFrame(new_rows)

    # Align columns: cms_keep may have columns not in cms_new and vice-versa
    all_cols = list(dict.fromkeys(list(cms_keep.columns) + list(cms_new.columns)))
    for df_, other in [(cms_keep, cms_new), (cms_new, cms_keep)]:
        for col in other.columns:
            if col not in df_.columns:
                df_[col] = _pd.NA

    result = _pd.concat([cms_keep[all_cols], cms_new[all_cols]], ignore_index=True)
    result.to_parquet(cms_path, index=False)

    print(
        f"  campus CMS aggregation: {updated} campus primaries updated "
        f"({len(rows_to_remove) - updated} sub-feature rows replaced)"
    )
    return updated
