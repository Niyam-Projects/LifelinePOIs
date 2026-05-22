"""
Tests for lib/health_dedup.py

All tests use synthetic in-memory GeoDataFrames — no real data files needed.

Run: uv run pytest tests/test_health_dedup.py -v
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
import geopandas as gpd
from shapely.geometry import Point, Polygon

from lib.health_dedup import dedup_hospital_building_centroids, _is_empty, _coalesce_attrs


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_point(lon, lat):
    return Point(lon, lat)


def _make_health_gdf(rows: list[dict]) -> gpd.GeoDataFrame:
    """Build a minimal silver-style health GeoDataFrame from a list of dicts."""
    df = pd.DataFrame(rows)
    gdf = gpd.GeoDataFrame(df, geometry="geometry", crs="EPSG:4326")
    return gdf


def _make_attr_gdf(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# _is_empty
# ---------------------------------------------------------------------------

class TestIsEmpty:
    def test_none(self):
        assert _is_empty(None)

    def test_nan(self):
        assert _is_empty(float("nan"))

    def test_empty_string(self):
        assert _is_empty("")

    def test_whitespace(self):
        assert _is_empty("   ")

    def test_sentinel_nan_string(self):
        assert _is_empty("nan")

    def test_sentinel_none_string(self):
        assert _is_empty("None")

    def test_valid_string(self):
        assert not _is_empty("Hospital General")

    def test_valid_number(self):
        assert not _is_empty(42)


# ---------------------------------------------------------------------------
# _coalesce_attrs
# ---------------------------------------------------------------------------

class TestCoalesceAttrs:
    def test_secondary_fills_gap(self):
        primary   = pd.Series({"display_name": None, "confidence_score": 0.5})
        secondary = pd.Series({"display_name": "General Hospital", "confidence_score": 0.9})
        result = _coalesce_attrs(primary, secondary)
        assert result["display_name"] == "General Hospital"

    def test_primary_wins_when_set(self):
        primary   = pd.Series({"display_name": "Hospital A"})
        secondary = pd.Series({"display_name": "Hospital B"})
        result = _coalesce_attrs(primary, secondary)
        assert "display_name" not in result  # no update needed

    def test_geometry_skipped(self):
        primary   = pd.Series({"geometry": None})
        secondary = pd.Series({"geometry": Point(0, 0)})
        result = _coalesce_attrs(primary, secondary)
        assert "geometry" not in result

    def test_lifeline_id_skipped(self):
        primary   = pd.Series({"lifeline_id": "aaa"})
        secondary = pd.Series({"lifeline_id": "bbb"})
        result = _coalesce_attrs(primary, secondary)
        assert "lifeline_id" not in result

    def test_empty_secondary_value_skipped(self):
        primary   = pd.Series({"phone": None})
        secondary = pd.Series({"phone": None})
        result = _coalesce_attrs(primary, secondary)
        assert "phone" not in result


# ---------------------------------------------------------------------------
# dedup_hospital_building_centroids — using attr_gdf (precise mode)
# ---------------------------------------------------------------------------

class TestDedupWithAttrGdf:
    """Tests using the attr_gdf path for precise building/amenity identification."""

    def _make_pair(self, bldg_name, node_name, distance_m=10.0):
        """Return (health_gdf, attr_gdf) with one building centroid + one amenity node."""
        # Building centroid at origin
        bldg_pt = _make_point(0.0, 0.0)
        # Amenity node shifted by ~distance_m metres east
        # 1 degree longitude ≈ 111,320m at equator → distance_m metres ≈ deg/111320
        node_pt = _make_point(distance_m / 111_320, 0.0)

        health = _make_health_gdf([
            {
                "lifeline_id": "bldg-001",
                "display_name": bldg_name,
                "osm_category": None,  # no amenity tag
                "geometry": bldg_pt,
                "tmp_osm_layer": "health",
            },
            {
                "lifeline_id": "node-001",
                "display_name": node_name,
                "osm_category": "hospital",
                "geometry": node_pt,
                "tmp_osm_layer": "health",
            },
        ])

        attr = _make_attr_gdf([
            {
                "lifeline_id": "bldg-001",
                "building": "hospital",
                "amenity": None,
                "healthcare": None,
            },
            {
                "lifeline_id": "node-001",
                "building": None,
                "amenity": "hospital",
                "healthcare": None,
            },
        ])

        return health, attr

    def test_matching_names_within_proximity_merges(self):
        health, attr = self._make_pair("General Hospital", "General Hospital", distance_m=20)
        result, n = dedup_hospital_building_centroids(health, attr_gdf=attr, proximity_m=50)
        assert n == 1
        assert len(result) == 1
        assert result.iloc[0]["lifeline_id"] == "bldg-001"

    def test_building_centroid_geometry_kept(self):
        health, attr = self._make_pair("Central Hospital", "Central Hospital", distance_m=20)
        result, n = dedup_hospital_building_centroids(health, attr_gdf=attr, proximity_m=50)
        assert n == 1
        bldg_orig = health[health["lifeline_id"] == "bldg-001"].iloc[0].geometry
        assert result.iloc[0].geometry.equals(bldg_orig)

    def test_too_far_apart_no_merge(self):
        health, attr = self._make_pair("General Hospital", "General Hospital", distance_m=200)
        result, n = dedup_hospital_building_centroids(health, attr_gdf=attr, proximity_m=50)
        assert n == 0
        assert len(result) == 2

    def test_name_mismatch_no_merge(self):
        health, attr = self._make_pair("Hospital A", "Clinic B", distance_m=10)
        result, n = dedup_hospital_building_centroids(
            health, attr_gdf=attr, proximity_m=50, name_threshold=0.85
        )
        assert n == 0
        assert len(result) == 2

    def test_both_unnamed_merges(self):
        health, attr = self._make_pair(None, None, distance_m=10)
        result, n = dedup_hospital_building_centroids(health, attr_gdf=attr, proximity_m=50)
        assert n == 1

    def test_only_node_named_merges(self):
        """Node has name, building centroid doesn't — still merge (no conflict)."""
        health, attr = self._make_pair(None, "City Hospital", distance_m=10)
        result, n = dedup_hospital_building_centroids(health, attr_gdf=attr, proximity_m=50)
        assert n == 1
        # Node's display_name should have been coalesced onto the building row
        assert result.iloc[0]["display_name"] == "City Hospital"

    def test_node_attr_fills_gap_in_centroid(self):
        """Node carries a phone number the building centroid lacks."""
        health = _make_health_gdf([
            {"lifeline_id": "b1", "display_name": "Hospital",
             "osm_category": None, "phone": None,
             "geometry": _make_point(0.0, 0.0), "tmp_osm_layer": "health"},
            {"lifeline_id": "n1", "display_name": "Hospital",
             "osm_category": "hospital", "phone": "+1-800-555-0100",
             "geometry": _make_point(0.0001, 0.0), "tmp_osm_layer": "health"},
        ])
        attr = _make_attr_gdf([
            {"lifeline_id": "b1", "building": "hospital", "amenity": None, "healthcare": None},
            {"lifeline_id": "n1", "building": None, "amenity": "hospital", "healthcare": None},
        ])
        result, n = dedup_hospital_building_centroids(health, attr_gdf=attr, proximity_m=50)
        assert n == 1
        assert result.iloc[0]["phone"] == "+1-800-555-0100"

    def test_campus_polygon_not_treated_as_node(self):
        """An amenity=hospital Polygon (campus boundary) must NOT be deduplicated."""
        campus_poly = Polygon([(0, 0), (0.01, 0), (0.01, 0.01), (0, 0.01)])
        health = _make_health_gdf([
            {"lifeline_id": "b1", "display_name": "Hospital",
             "osm_category": None, "geometry": _make_point(0.005, 0.005), "tmp_osm_layer": "health"},
            {"lifeline_id": "p1", "display_name": "Hospital",
             "osm_category": "hospital", "geometry": campus_poly, "tmp_osm_layer": "health"},
        ])
        attr = _make_attr_gdf([
            {"lifeline_id": "b1", "building": "hospital", "amenity": None, "healthcare": None},
            {"lifeline_id": "p1", "building": None, "amenity": "hospital", "healthcare": None},
        ])
        result, n = dedup_hospital_building_centroids(health, attr_gdf=attr, proximity_m=2000)
        # Campus polygon is Polygon, not Point → excluded from node_mask → no merge
        assert n == 0
        assert len(result) == 2

    def test_empty_health_gdf_returns_empty(self):
        health = gpd.GeoDataFrame(
            {"lifeline_id": pd.Series([], dtype=str), "geometry": pd.array([], dtype=object)},
            geometry="geometry",
            crs="EPSG:4326",
        )
        result, n = dedup_hospital_building_centroids(health)
        assert n == 0
        assert len(result) == 0

    def test_no_building_centroids_no_merge(self):
        """Only amenity nodes, no building centroids → nothing to merge."""
        health = _make_health_gdf([
            {"lifeline_id": "n1", "display_name": "Hospital A",
             "osm_category": "hospital", "geometry": _make_point(0, 0), "tmp_osm_layer": "health"},
            {"lifeline_id": "n2", "display_name": "Hospital B",
             "osm_category": "hospital", "geometry": _make_point(0.001, 0), "tmp_osm_layer": "health"},
        ])
        attr = _make_attr_gdf([
            {"lifeline_id": "n1", "building": None, "amenity": "hospital", "healthcare": None},
            {"lifeline_id": "n2", "building": None, "amenity": "hospital", "healthcare": None},
        ])
        result, n = dedup_hospital_building_centroids(health, attr_gdf=attr)
        assert n == 0
        assert len(result) == 2


# ---------------------------------------------------------------------------
# dedup_hospital_building_centroids — heuristic mode (no attr_gdf)
# ---------------------------------------------------------------------------

class TestDedupHeuristicMode:
    """Tests using the osm_category + geometry-type heuristic (no attr_gdf)."""

    def test_matching_pair_merges(self):
        health = _make_health_gdf([
            {"lifeline_id": "b1", "display_name": "General Hospital",
             "osm_category": None, "geometry": _make_point(0.0, 0.0), "tmp_osm_layer": "health"},
            {"lifeline_id": "n1", "display_name": "General Hospital",
             "osm_category": "hospital", "geometry": _make_point(0.0002, 0.0), "tmp_osm_layer": "health"},
        ])
        result, n = dedup_hospital_building_centroids(health, proximity_m=50)
        assert n == 1
        assert len(result) == 1

    def test_polygon_osm_category_hospital_not_matched_as_node(self):
        """Campus polygon has osm_category=hospital but geom_type=Polygon — excluded by heuristic."""
        campus_poly = Polygon([(0, 0), (0.01, 0), (0.01, 0.01), (0, 0.01)])
        health = _make_health_gdf([
            {"lifeline_id": "b1", "display_name": "Hospital",
             "osm_category": None, "geometry": _make_point(0.005, 0.005), "tmp_osm_layer": "health"},
            {"lifeline_id": "p1", "display_name": "Hospital",
             "osm_category": "hospital", "geometry": campus_poly, "tmp_osm_layer": "health"},
        ])
        # In heuristic mode, campus polygon (Polygon geom) is excluded from node_mask
        result, n = dedup_hospital_building_centroids(health, proximity_m=2000)
        assert n == 0
        assert len(result) == 2
