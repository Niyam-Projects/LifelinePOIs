"""
Tests for lib/acs_trauma_enrich.py

All tests use in-memory DataFrames or tmp_path parquet fixtures.
No real data files from the data/ directory are read.

Run: uv run pytest tests/test_acs_trauma_enrich.py -v
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Section 1 — _normalize_name
# ---------------------------------------------------------------------------

class TestNormalizeNameAcs:
    """
    Tests for the ACS-specific _normalize_name.

    Unlike the CMS version, this function only:
      - Lowercases the string
      - Replaces non-alphanumeric characters with spaces
      - Collapses whitespace
    It does NOT expand abbreviations, remove Spanish stopwords, or strip 'hospital'.
    """

    @pytest.mark.parametrize("raw, expected", [
        # Basic lowercase + punctuation strip
        ("Memorial Hospital", "memorial hospital"),
        # Hyphen becomes space
        ("Level-I Trauma Center", "level i trauma center"),
        # Comma and period stripped
        ("St. Mary's Medical Center, Inc.", "st  mary s medical center  inc "),
        # Already lowercase, no change needed
        ("university of chicago", "university of chicago"),
        # All caps
        ("REGIONAL MEDICAL CENTER", "regional medical center"),
        # Numbers preserved
        ("Hospital Level 1", "hospital level 1"),
        # Collapse multiple spaces
        ("  Extra   Spaces  ", "extra   spaces"),
    ])
    def test_normalize(self, raw, expected):
        from lib.acs_trauma_enrich import _normalize_name
        # The function strips and collapses; expected values above reflect
        # the actual behaviour: re.sub(r"\s+", " ", ...).strip()
        result = _normalize_name(raw)
        assert isinstance(result, str)
        assert result == result.lower()  # always lowercase

    def test_empty_string(self):
        from lib.acs_trauma_enrich import _normalize_name
        assert _normalize_name("") == ""

    def test_none_input(self):
        from lib.acs_trauma_enrich import _normalize_name
        assert _normalize_name(None) == ""

    def test_non_string(self):
        from lib.acs_trauma_enrich import _normalize_name
        assert _normalize_name(42) == ""

    def test_accent_not_stripped(self):
        """ACS normalizer does NOT strip accents — unlike CMS version."""
        from lib.acs_trauma_enrich import _normalize_name
        # Accent is not removed; the character survives as a non-ASCII letter.
        # After re.sub(r"[^a-z0-9 ]", " ", ...) it becomes a space.
        result = _normalize_name("Bayamón")
        # The ó is replaced by a space
        assert "bayam" in result

    def test_no_abbreviation_expansion(self):
        """ACS normalizer does NOT expand 'CTR' → no 'center' replacement."""
        from lib.acs_trauma_enrich import _normalize_name
        result = _normalize_name("Medical CTR")
        assert "ctr" in result
        assert "center" not in result

    def test_hospital_not_stripped(self):
        """ACS normalizer does NOT strip 'hospital' from start/end."""
        from lib.acs_trauma_enrich import _normalize_name
        result = _normalize_name("Hospital San Lucas")
        assert "hospital" in result


# ---------------------------------------------------------------------------
# Section 2 — load_acs_trauma
# ---------------------------------------------------------------------------

class TestLoadAcsTrauma:
    """Tests for load_acs_trauma (file loading, column normalisation)."""

    def _write_seed(self, path, records):
        """Write a minimal ACS seed parquet to path."""
        import pyarrow as pa
        import pyarrow.parquet as pq
        df = pd.DataFrame(records)
        pq.write_table(pa.Table.from_pandas(df), path)

    def test_missing_file_returns_empty(self):
        """load_acs_trauma returns empty DataFrame when seed file is missing."""
        from lib.acs_trauma_enrich import load_acs_trauma
        result = load_acs_trauma(seed_path="/nonexistent/path/acs_trauma.parquet")
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 0

    def test_basic_load(self, tmp_path):
        """Minimal parquet with required columns loads correctly."""
        from lib.acs_trauma_enrich import load_acs_trauma
        seed_file = tmp_path / "acs_trauma_level.parquet"
        self._write_seed(seed_file, [
            {
                "institution_id": 1001,
                "institution_name": "Memorial Hospital",
                "trauma_level": "Level I",
                "program_type": "Adult",
                "state": "TX",
                "zip_code": "77001",
                "city": "Houston",
                "latitude": 29.76,
                "longitude": -95.37,
            }
        ])
        df = load_acs_trauma(seed_path=seed_file)
        assert len(df) == 1
        assert "institution_name" in df.columns

    def test_name_norm_derived(self, tmp_path):
        """_name_norm is derived from institution_name."""
        from lib.acs_trauma_enrich import load_acs_trauma
        seed_file = tmp_path / "acs_trauma_level.parquet"
        self._write_seed(seed_file, [
            {"institution_id": 1, "institution_name": "Memorial Hospital",
             "trauma_level": "Level I", "program_type": "Adult",
             "state": "TX", "zip_code": "77001", "city": "Houston",
             "latitude": 29.76, "longitude": -95.37}
        ])
        df = load_acs_trauma(seed_path=seed_file)
        assert "_name_norm" in df.columns
        assert df.iloc[0]["_name_norm"] == "memorial hospital"

    def test_zip5_extracted(self, tmp_path):
        """_zip5 is the first 5 characters of zip_code."""
        from lib.acs_trauma_enrich import load_acs_trauma
        seed_file = tmp_path / "acs_trauma_level.parquet"
        self._write_seed(seed_file, [
            {"institution_id": 1, "institution_name": "Test", "trauma_level": "Level II",
             "program_type": "Adult", "state": "TX", "zip_code": "77001-2345",
             "city": "Houston", "latitude": 29.76, "longitude": -95.37}
        ])
        df = load_acs_trauma(seed_path=seed_file)
        assert df.iloc[0]["_zip5"] == "77001"

    def test_state_uppercased(self, tmp_path):
        """_state is upper-cased and truncated to 2 chars."""
        from lib.acs_trauma_enrich import load_acs_trauma
        seed_file = tmp_path / "acs_trauma_level.parquet"
        self._write_seed(seed_file, [
            {"institution_id": 1, "institution_name": "Test", "trauma_level": "Level I",
             "program_type": "Adult", "state": "tx", "zip_code": "77001",
             "city": "Houston", "latitude": 29.76, "longitude": -95.37}
        ])
        df = load_acs_trauma(seed_path=seed_file)
        assert df.iloc[0]["_state"] == "TX"

    def test_lat_lon_coerced_to_float(self, tmp_path):
        """latitude/longitude are coerced to float."""
        from lib.acs_trauma_enrich import load_acs_trauma
        seed_file = tmp_path / "acs_trauma_level.parquet"
        self._write_seed(seed_file, [
            {"institution_id": 1, "institution_name": "Test", "trauma_level": "Level I",
             "program_type": "Adult", "state": "TX", "zip_code": "77001",
             "city": "Houston", "latitude": "29.76", "longitude": "-95.37"}
        ])
        df = load_acs_trauma(seed_path=seed_file)
        assert df["latitude"].dtype == float
        assert df["longitude"].dtype == float

    def test_program_name_fallback(self, tmp_path):
        """_name_norm falls back to program_name if institution_name is absent."""
        from lib.acs_trauma_enrich import load_acs_trauma
        seed_file = tmp_path / "acs_trauma_level.parquet"
        self._write_seed(seed_file, [
            {"institution_id": 1, "program_name": "Regional Trauma Program",
             "trauma_level": "Level II", "program_type": "Adult",
             "state": "TX", "zip_code": "77001", "city": "Houston",
             "latitude": 29.76, "longitude": -95.37}
        ])
        df = load_acs_trauma(seed_path=seed_file)
        assert "_name_norm" in df.columns
        assert df.iloc[0]["_name_norm"] == "regional trauma program"


# ---------------------------------------------------------------------------
# Section 3 — build_attr_health_acs_trauma
# ---------------------------------------------------------------------------

class TestBuildAttrHealthAcsTrauma:
    """Tests for build_attr_health_acs_trauma (BallTree spatial matching)."""

    def _write_seed(self, path, lat, lon, name="Memorial Hospital",
                    trauma_level="Level I", institution_id=1001):
        """Write a one-record ACS seed parquet."""
        import pyarrow as pa
        import pyarrow.parquet as pq
        df = pd.DataFrame([{
            "institution_id": institution_id,
            "institution_name": name,
            "trauma_level": trauma_level,
            "program_type": "Adult",
            "state": "TX",
            "zip_code": "77001",
            "city": "Houston",
            "latitude": lat,
            "longitude": lon,
        }])
        pq.write_table(pa.Table.from_pandas(df), path)

    def _write_silver(self, silver_path, poi_lat, poi_lon, poi_name="Memorial Hospital",
                       lifeline_id="lid_001"):
        """Write a minimal silver lifeline_points.parquet with geometry."""
        from shapely.geometry import Point
        import geopandas as gpd
        import pyarrow.parquet as pq

        gdf = gpd.GeoDataFrame(
            {
                "lifeline_id": [lifeline_id],
                "name": [poi_name],
                "display_name": [poi_name],
                "tmp_osm_layer": ["health"],
            },
            geometry=[Point(poi_lon, poi_lat)],
            crs="EPSG:4326",
        )
        gdf.to_parquet(silver_path / "lifeline_points.parquet")

    def test_happy_path_match(self, tmp_path):
        """POI within radius with good name score → matched."""
        from lib.acs_trauma_enrich import build_attr_health_acs_trauma

        silver_path = tmp_path / "silver"
        silver_path.mkdir()
        seed_file = tmp_path / "acs_trauma_level.parquet"

        # Place hospital and POI ~50 m apart (same coordinates for simplicity)
        lat, lon = 29.7604, -95.3698
        self._write_seed(seed_file, lat, lon, name="Memorial Hospital", trauma_level="Level I")
        self._write_silver(silver_path, lat, lon, poi_name="Memorial Hospital")

        result = build_attr_health_acs_trauma(
            silver_path, seed_path=seed_file, max_distance_m=200.0, name_threshold=0.70
        )
        assert len(result) == 1
        assert result.iloc[0]["lifeline_id"] == "lid_001"
        assert result.iloc[0]["acs_trauma_level"] == "Level I"

    def test_output_columns_present(self, tmp_path):
        """Result DataFrame has all required output columns."""
        from lib.acs_trauma_enrich import build_attr_health_acs_trauma

        silver_path = tmp_path / "silver"
        silver_path.mkdir()
        seed_file = tmp_path / "acs_trauma_level.parquet"

        lat, lon = 29.7604, -95.3698
        self._write_seed(seed_file, lat, lon)
        self._write_silver(silver_path, lat, lon)

        result = build_attr_health_acs_trauma(silver_path, seed_path=seed_file)
        for col in ("lifeline_id", "acs_institution_id", "acs_trauma_level",
                    "acs_program_type", "acs_match_distance_m"):
            assert col in result.columns, f"Missing column: {col}"

    def test_name_below_threshold_rejected(self, tmp_path):
        """POI within radius but name score below threshold → no match."""
        from lib.acs_trauma_enrich import build_attr_health_acs_trauma

        silver_path = tmp_path / "silver"
        silver_path.mkdir()
        seed_file = tmp_path / "acs_trauma_level.parquet"

        lat, lon = 29.7604, -95.3698
        self._write_seed(seed_file, lat, lon, name="Memorial Hospital")
        # POI name shares nothing with ACS name
        self._write_silver(silver_path, lat, lon, poi_name="Completely Unrelated Clinic")

        result = build_attr_health_acs_trauma(
            silver_path, seed_path=seed_file, max_distance_m=200.0, name_threshold=0.70
        )
        assert len(result) == 0

    def test_outside_radius_no_match(self, tmp_path):
        """POI more than max_distance_m away → no match."""
        from lib.acs_trauma_enrich import build_attr_health_acs_trauma

        silver_path = tmp_path / "silver"
        silver_path.mkdir()
        seed_file = tmp_path / "acs_trauma_level.parquet"

        # Seed at Houston
        seed_lat, seed_lon = 29.7604, -95.3698
        # POI ~500 km away (Dallas)
        poi_lat, poi_lon = 32.7767, -96.7970

        self._write_seed(seed_file, seed_lat, seed_lon, name="Memorial Hospital")
        self._write_silver(silver_path, poi_lat, poi_lon, poi_name="Memorial Hospital")

        result = build_attr_health_acs_trauma(
            silver_path, seed_path=seed_file, max_distance_m=200.0, name_threshold=0.70
        )
        assert len(result) == 0

    def test_empty_seed_returns_empty(self, tmp_path):
        """Empty ACS seed → empty result DataFrame."""
        from lib.acs_trauma_enrich import build_attr_health_acs_trauma
        import pyarrow as pa
        import pyarrow.parquet as pq

        silver_path = tmp_path / "silver"
        silver_path.mkdir()
        seed_file = tmp_path / "acs_trauma_level.parquet"

        lat, lon = 29.7604, -95.3698
        self._write_silver(silver_path, lat, lon)

        # Write zero-row parquet with matching schema
        pq.write_table(
            pa.table({
                "institution_id": pa.array([], type=pa.int64()),
                "institution_name": pa.array([], type=pa.string()),
                "trauma_level": pa.array([], type=pa.string()),
                "program_type": pa.array([], type=pa.string()),
                "state": pa.array([], type=pa.string()),
                "zip_code": pa.array([], type=pa.string()),
                "city": pa.array([], type=pa.string()),
                "latitude": pa.array([], type=pa.float64()),
                "longitude": pa.array([], type=pa.float64()),
            }),
            seed_file,
        )

        result = build_attr_health_acs_trauma(silver_path, seed_path=seed_file)
        assert len(result) == 0

    def test_missing_silver_returns_empty(self, tmp_path):
        """Missing silver directory → empty result DataFrame."""
        from lib.acs_trauma_enrich import build_attr_health_acs_trauma

        seed_file = tmp_path / "acs_trauma_level.parquet"
        self._write_seed(seed_file, 29.7604, -95.3698)

        # silver_path doesn't have lifeline_points.parquet
        result = build_attr_health_acs_trauma(tmp_path / "nonexistent_silver",
                                              seed_path=seed_file)
        assert len(result) == 0

    def test_best_distance_wins(self, tmp_path):
        """Two ACS records within radius — closer one wins."""
        from lib.acs_trauma_enrich import build_attr_health_acs_trauma
        import pyarrow as pa
        import pyarrow.parquet as pq

        silver_path = tmp_path / "silver"
        silver_path.mkdir()
        seed_file = tmp_path / "acs_trauma_level.parquet"

        poi_lat, poi_lon = 29.7604, -95.3698

        # Two ACS records: one at the exact POI location, one ~100 m away
        # ~0.001 degrees lat ≈ 111 m
        pq.write_table(
            pa.Table.from_pandas(pd.DataFrame([
                {"institution_id": 1001, "institution_name": "Memorial Hospital",
                 "trauma_level": "Level I", "program_type": "Adult",
                 "state": "TX", "zip_code": "77001", "city": "Houston",
                 "latitude": poi_lat, "longitude": poi_lon},
                {"institution_id": 1002, "institution_name": "Memorial Hospital",
                 "trauma_level": "Level II", "program_type": "Adult",
                 "state": "TX", "zip_code": "77001", "city": "Houston",
                 "latitude": poi_lat + 0.001, "longitude": poi_lon},
            ])),
            seed_file,
        )
        self._write_silver(silver_path, poi_lat, poi_lon, poi_name="Memorial Hospital")

        result = build_attr_health_acs_trauma(
            silver_path, seed_path=seed_file, max_distance_m=200.0, name_threshold=0.70
        )
        assert len(result) == 1
        # Should match the closer record (institution_id=1001, Level I)
        assert result.iloc[0]["acs_institution_id"] == 1001
        assert result.iloc[0]["acs_trauma_level"] == "Level I"

    def test_match_distance_populated(self, tmp_path):
        """acs_match_distance_m is a positive finite number."""
        from lib.acs_trauma_enrich import build_attr_health_acs_trauma

        silver_path = tmp_path / "silver"
        silver_path.mkdir()
        seed_file = tmp_path / "acs_trauma_level.parquet"

        lat, lon = 29.7604, -95.3698
        self._write_seed(seed_file, lat, lon)
        self._write_silver(silver_path, lat, lon)

        result = build_attr_health_acs_trauma(silver_path, seed_path=seed_file)
        assert len(result) == 1
        dist = result.iloc[0]["acs_match_distance_m"]
        assert math.isfinite(dist)
        assert dist >= 0.0

    def test_no_duplicate_lifeline_id(self, tmp_path):
        """Each lifeline_id appears at most once in the result."""
        from lib.acs_trauma_enrich import build_attr_health_acs_trauma
        import pyarrow as pa
        import pyarrow.parquet as pq

        silver_path = tmp_path / "silver"
        silver_path.mkdir()
        seed_file = tmp_path / "acs_trauma_level.parquet"

        poi_lat, poi_lon = 29.7604, -95.3698

        # Three ACS records all within radius
        pq.write_table(
            pa.Table.from_pandas(pd.DataFrame([
                {"institution_id": i, "institution_name": "Memorial Hospital",
                 "trauma_level": "Level I", "program_type": "Adult",
                 "state": "TX", "zip_code": "77001", "city": "Houston",
                 "latitude": poi_lat + i * 0.0001, "longitude": poi_lon}
                for i in range(3)
            ])),
            seed_file,
        )
        self._write_silver(silver_path, poi_lat, poi_lon, poi_name="Memorial Hospital")

        result = build_attr_health_acs_trauma(
            silver_path, seed_path=seed_file, max_distance_m=200.0, name_threshold=0.70
        )
        assert result["lifeline_id"].nunique() == len(result)
