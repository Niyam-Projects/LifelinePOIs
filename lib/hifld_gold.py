"""
HIFLD Gold Layer Production for LifelinePOIs.

Produces authoritative GeoParquet gold files from HIFLD bronze data,
optionally enriched with matched OSM silver points.

Schema changes from HIFLD source:
  ADDED (prepended):
    lifeline_id       - UUID5(hifld/{layer}/{id_field}) — stable row identifier
    fema_lifeline     - struct: {primary, hierarchy, alternates} from FEMA taxonomy
    display_name      - primary human-readable name (layer-specific source field)
    confidence_score  - 1.0 when OSM-matched, 0.85 for HIFLD-only
    confidence_tier   - always "HIGH" (authoritative source)
    source_provenance - "hifld" or "hifld+osm"
    osm_lifeline_id   - UUID of matched OSM silver point (None if no OSM match)

  REMOVED from HIFLD source:
    bbox              - redundant; GeoParquet provides native spatial indexing
    bpd_metadata      - internal BPD catalog metadata, not user-facing
    type              - always "Feature" from GeoJSON origin; redundant
    geometry (WKB)    - REPLACED (see Transformed)
    properties (struct) - FLATTENED (all keys promoted; see Transformed)

  TRANSFORMED:
    geometry          - WKB binary → GeoParquet-standard Shapely Point, EPSG:4326
    properties dict   - flattened: all property keys become top-level columns
                        (applies to: lm_commercial, lm_private, microwave, wastewater_treatment_plants)
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Optional

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point

from lib.naics_lifeline_map import make_fema_lifeline_struct


# ── UUID5 namespace for HIFLD gold IDs ──────────────────────────────────────
_HIFLD_NS = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")  # URL namespace


def _make_uuid5(layer_name: str, id_val: str) -> str:
    return str(uuid.uuid5(_HIFLD_NS, f"hifld/{layer_name}/{id_val}"))


def _resolve_field(df: pd.DataFrame, field: str) -> pd.Series:
    """
    Resolve a field name that may be dotted (e.g. 'properties.londec').
    After flattening, dotted names become direct top-level columns.
    """
    if field in df.columns:
        return pd.to_numeric(df[field], errors="coerce")
    # Try just the suffix (e.g. 'londec' from 'properties.londec')
    flat = field.split(".")[-1]
    if flat in df.columns:
        return pd.to_numeric(df[flat], errors="coerce")
    # Try reading from a still-nested 'properties' struct column
    if "." in field and "properties" in df.columns:
        sub_key = field.split(".", 1)[1]
        try:
            return pd.to_numeric(
                df["properties"].apply(
                    lambda p: p.get(sub_key) if isinstance(p, dict) else None
                ),
                errors="coerce",
            )
        except Exception:
            pass
    return pd.Series([None] * len(df), dtype=float)


def load_and_flatten(
    bronze_path: Path,
    layer_name: str,
    layer_def,
) -> gpd.GeoDataFrame:
    """
    Load a HIFLD bronze parquet, flatten nested properties if needed,
    rebuild geometry from lat/lon, and return a GeoDataFrame (EPSG:4326).

    Drops non-data columns: bbox, geometry (WKB blob), and for nested
    schemas also: bpd_metadata, type, properties.
    """
    parquet_path = Path(bronze_path) / "hifld" / f"{layer_name}.parquet"
    df = pd.read_parquet(parquet_path)

    if layer_def.nested_properties:
        # Flatten the 'properties' dict column to top-level columns
        if "properties" in df.columns:
            props_df = pd.json_normalize(df["properties"].tolist())
            props_df.index = df.index
            # Drop original properties + metadata cols before merging
            drop_cols = [c for c in ["bpd_metadata", "type", "bbox", "properties", "geometry"] if c in df.columns]
            df = df.drop(columns=drop_cols)
            # Merge flattened props; skip columns already present
            for col in props_df.columns:
                if col not in df.columns:
                    df[col] = props_df[col].values
    else:
        # Flat schema — just drop the WKB geometry and bbox
        drop_cols = [c for c in ["bbox", "geometry"] if c in df.columns]
        df = df.drop(columns=drop_cols)

    # Resolve lat/lon (handles dotted names gracefully after flatten)
    lons = _resolve_field(df, layer_def.lon_field)
    lats = _resolve_field(df, layer_def.lat_field)

    valid = lons.notna() & lats.notna()
    df = df[valid].copy()
    lons = lons[valid]
    lats = lats[valid]

    geometries = [Point(lon, lat) for lon, lat in zip(lons, lats)]
    gdf = gpd.GeoDataFrame(df, geometry=geometries, crs="EPSG:4326")
    return gdf.reset_index(drop=True)


def join_osm_silver(
    hifld_gdf: gpd.GeoDataFrame,
    silver_path: Path,
    osm_layer: str,
    max_distance_m: float = 50.0,
) -> gpd.GeoDataFrame:
    """
    Spatial-join HIFLD gold rows to the nearest OSM silver point within
    max_distance_m metres.  Adds:
      - osm_lifeline_id   (str | None)
      - source_provenance ("hifld+osm" | "hifld")
    """
    from sklearn.neighbors import BallTree

    silver_file = Path(silver_path) / "lifeline_points.parquet"
    hifld_gdf = hifld_gdf.copy()
    hifld_gdf["osm_lifeline_id"] = None
    hifld_gdf["source_provenance"] = "hifld"

    if not silver_file.exists():
        return hifld_gdf

    try:
        silver = gpd.read_parquet(silver_file)
    except Exception:
        silver = pd.read_parquet(silver_file)

    if "tmp_osm_layer" not in silver.columns:
        return hifld_gdf

    layer_silver = silver[silver["tmp_osm_layer"] == osm_layer].copy()
    if len(layer_silver) == 0:
        return hifld_gdf

    projected_crs = "EPSG:3857"
    hifld_proj = hifld_gdf.to_crs(projected_crs)
    silver_proj = layer_silver.to_crs(projected_crs)

    def _coords(gdf: gpd.GeoDataFrame) -> np.ndarray:
        return np.array(
            [
                [
                    g.centroid.x if g.geom_type != "Point" else g.x,
                    g.centroid.y if g.geom_type != "Point" else g.y,
                ]
                for g in gdf.geometry
            ]
        )

    silver_coords = _coords(silver_proj)
    hifld_coords = _coords(hifld_proj)

    tree = BallTree(silver_coords, metric="euclidean")
    distances, indices = tree.query(hifld_coords, k=1)
    distances = distances.flatten()
    indices = indices.flatten()

    silver_ids = layer_silver["lifeline_id"].values if "lifeline_id" in layer_silver.columns else None

    for i, (dist, idx) in enumerate(zip(distances, indices)):
        if dist <= max_distance_m and silver_ids is not None:
            hifld_gdf.at[i, "osm_lifeline_id"] = str(silver_ids[idx])
            hifld_gdf.at[i, "source_provenance"] = "hifld+osm"

    return hifld_gdf


def enrich(
    hifld_gdf: gpd.GeoDataFrame,
    layer_name: str,
    layer_def,
) -> gpd.GeoDataFrame:
    """
    Add gold-layer enrichment columns and prepend them before HIFLD-native columns.
    """
    hifld_gdf = hifld_gdf.copy()

    # Generate stable lifeline_id via UUID5
    id_field = layer_def.id_field
    def _row_id(row, i):
        if id_field in row.index and pd.notna(row[id_field]):
            return _make_uuid5(layer_name, str(row[id_field]))
        return _make_uuid5(layer_name, str(i))

    lifeline_ids = [_row_id(row, i) for i, row in hifld_gdf.iterrows()]

    # Confidence
    confidence_scores = np.where(
        hifld_gdf["source_provenance"] == "hifld+osm", 1.0, 0.85
    )

    # Display name
    dn_field = layer_def.display_name_field
    if dn_field and dn_field in hifld_gdf.columns:
        display_names = hifld_gdf[dn_field].astype(str).values
    else:
        display_names = [None] * len(hifld_gdf)

    enrichment = pd.DataFrame(
        {
            "lifeline_id": lifeline_ids,
            "fema_lifeline": [make_fema_lifeline_struct(layer_def.lifeline_key)] * len(hifld_gdf),
            "display_name": display_names,
            "confidence_score": confidence_scores,
            "confidence_tier": "HIGH",
            "source_provenance": hifld_gdf["source_provenance"].values,
            "osm_lifeline_id": hifld_gdf["osm_lifeline_id"].values,
        },
        index=hifld_gdf.index,
    )

    # Drop source_provenance / osm_lifeline_id from hifld_gdf (now in enrichment)
    native = hifld_gdf.drop(columns=["source_provenance", "osm_lifeline_id"], errors="ignore")

    # Prepend enrichment cols before native HIFLD cols
    result = gpd.GeoDataFrame(
        pd.concat([enrichment, native.drop(columns=["geometry"], errors="ignore")], axis=1),
        geometry=native.geometry,
        crs=native.crs,
    )
    return result.reset_index(drop=True)


def produce_hifld_gold(
    bronze_path: Path,
    silver_path: Path,
    gold_path: Path,
    layer_name: str,
    layer_def,
    max_distance_m: float = 50.0,
) -> int:
    """
    Full pipeline: load → flatten → join OSM silver → enrich → write GeoParquet.
    Returns the number of rows written.
    """
    gdf = load_and_flatten(bronze_path, layer_name, layer_def)
    gdf = join_osm_silver(gdf, silver_path, layer_def.osm_layer, max_distance_m)
    gdf = enrich(gdf, layer_name, layer_def)

    Path(gold_path).mkdir(parents=True, exist_ok=True)
    out_path = Path(gold_path) / f"hifld_{layer_name}.parquet"
    gdf.to_parquet(out_path, index=False)
    return len(gdf)
