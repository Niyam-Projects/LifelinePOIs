"""
Tests for lib/cms_health_enrich.py

All tests use in-memory DataFrames or tmp_path parquet fixtures.
No real data files from E:/lifelinepois/data/ are read.

Run: uv run pytest tests/test_cms_health_enrich.py -v
"""
from __future__ import annotations

import pandas as pd
import pytest

from lib.cms_health_enrich import (
    _normalize_name,
    _detect_cnt_columns,
    _build_result_row,
    _tier1_spatial,
    _tier2_zip_fuzzy,
    _tier3_addr_zip,
    _tier4_name_zip,
    _tier5_census_spatial,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def cnt_cols():
    return ["BED_CNT", "CRTFD_BED_CNT", "OPRTG_ROOM_CNT"]


@pytest.fixture
def minimal_cms(cnt_cols):
    """Minimal CMS DataFrame with the columns tier functions need."""
    return pd.DataFrame({
        "PRVDR_NUM": ["400001", "400002"],
        "FAC_NAME": ["HOSPITAL SAN LUCAS", "HOSPITAL METROPOLITANO DR PILA"],
        "_name_norm": ["san lucas", "metropolitano pila"],
        "_zip5": ["00731", "00717"],
        "_state": ["PR", "PR"],
        "_addr_num": ["917", "2435"],
        "_city_norm": ["PONCE", "PONCE"],
        "BED_CNT": pd.array([120, 200], dtype="Int64"),
        "CRTFD_BED_CNT": pd.array([100, 180], dtype="Int64"),
        "OPRTG_ROOM_CNT": pd.array([4, 8], dtype="Int64"),
        "geocoded_lat": [float("nan"), float("nan")],
        "geocoded_lon": [float("nan"), float("nan")],
    })


@pytest.fixture
def minimal_health():
    """Minimal health POI DataFrame with the columns tier functions need."""
    return pd.DataFrame({
        "lifeline_id": ["lid_001", "lid_002"],
        "name": ["Hospital San Lucas", "El Hospital Metropolitano Dr. Pila"],
        "_name_norm": ["san lucas", "metropolitano pila"],
        "_zip5": ["00731", "00717"],
        "_state": ["PR", "PR"],
        "_city": ["PONCE", "PONCE"],
        "_addr_num": ["917", "2435"],
        "display_name": [
            "Hospital San Lucas, 917 Avenida Tito Castro, Ponce, PR 00731",
            "El Hospital Metropolitano Dr. Pila, 2435 Boulevard Luis A. Ferré, Ponce, PR 00717",
        ],
        "geometry": [None, None],
    })


# ---------------------------------------------------------------------------
# Section 1 — _normalize_name
# ---------------------------------------------------------------------------

class TestNormalizeName:
    """Tests for _normalize_name (N-01 through N-16)."""

    @pytest.mark.parametrize("raw, expected", [
        # N-01: accent stripping — ó must not become a space
        ("Bayamón", "bayamon"),
        # N-02: multiple diacritics
        ("Clínica Ángeles", "clinica angeles"),
        # N-03: Spanish stopwords de, la; strips leading hospital
        ("Hospital de la Montaña", "montana"),
        # N-04: del
        ("Centro del Norte", "centro norte"),
        # N-05: el, de, los, las
        ("El Centro de los Niños las Américas", "centro ninos americas"),
        # N-06: trailing inc; BUT hospital is in the MIDDLE → stays; only start/end stripped
        ("DOCTORS CENTER HOSPITAL BAYAMON INC", "doctors center hospital bayamon"),
        # N-07: ctr abbreviation
        ("Medical CTR Ponce", "medical center ponce"),
        # N-08: med abbreviation
        ("MED Center", "medical center"),
        # N-09: hospital prefix stripped
        ("Hospital San Lucas", "san lucas"),
        # N-10: hospital suffix stripped
        ("San Lucas Hospital", "san lucas"),
        # N-11: hospital is in the MIDDLE (not start/end) → not stripped, stays
        ("Regional Hospital Center", "regional hospital center"),
        # N-12: empty string guard
        ("", ""),
        # N-14: non-string guard
        (42, ""),
    ])
    def test_normalize(self, raw, expected):
        assert _normalize_name(raw) == expected

    def test_none_input(self):
        # N-13
        assert _normalize_name(None) == ""

    def test_menonita_match(self):
        # N-15 — REGRESSION: Hospital Menonita de Caguas / HOSPITAL MENONITA CAGUAS INC
        # Both should normalize to token sets that overlap at token_set_ratio >= 0.75
        from rapidfuzz import fuzz
        n1 = _normalize_name("Hospital General Menonita de Caguas")
        n2 = _normalize_name("HOSPITAL MENONITA CAGUAS INC")
        score = fuzz.token_set_ratio(n1, n2) / 100.0
        assert score >= 0.75, f"token_set_ratio={score:.2f}: '{n1}' vs '{n2}'"

    def test_doctors_center_exact(self):
        # N-16 — REGRESSION: Doctors' Center Hospital Bayamón vs DOCTORS CENTER HOSPITAL BAYAMON INC
        # hospital is in the MIDDLE of both names → stays in both; inc stripped from second
        n1 = _normalize_name("Doctors' Center Hospital Bayamón")
        n2 = _normalize_name("DOCTORS CENTER HOSPITAL BAYAMON INC")
        assert n1 == n2, f"Expected identical normalized forms, got '{n1}' vs '{n2}'"


# ---------------------------------------------------------------------------
# Section 2 — load_cms_providers / _addr_num extraction
# ---------------------------------------------------------------------------

class TestLoadCmsAddrNum:
    """
    Test the addr_num and zip5 extraction logic in load_cms_providers.
    Uses tmp_path to write a minimal parquet so we test the real function path.
    """

    def _make_parquet(self, tmp_path, records: list[dict]) -> "Path":
        import pyarrow as pa
        import pyarrow.parquet as pq
        df = pd.DataFrame(records)
        out = tmp_path / "cms_hospital_providers.parquet"
        pq.write_table(pa.Table.from_pandas(df), out)
        return tmp_path

    def _load(self, tmp_path):
        from lib.cms_health_enrich import load_cms_providers
        # load_cms_providers expects bronze_path / "cms" / "cms_hospital_providers.parquet"
        cms_dir = tmp_path / "cms"
        cms_dir.mkdir()
        return cms_dir.parent, cms_dir

    def test_leading_digits(self, tmp_path):
        # L-01
        from lib.cms_health_enrich import load_cms_providers
        cms_dir = tmp_path / "cms"
        cms_dir.mkdir()
        self._write_cms(cms_dir, [{"ST_ADR": "917 AVE TITO CASTRO", "PRVDR_NUM": "400001",
                                    "FAC_NAME": "HOSPITAL SAN LUCAS", "STATE_CD": "PR",
                                    "CITY_NAME": "PONCE", "ZIP_CD": "00731"}])
        df = load_cms_providers(tmp_path)
        assert df.iloc[0]["_addr_num"] == "917"

    def test_leading_digits_long(self, tmp_path):
        # L-02: two-space gap still produces leading digit
        from lib.cms_health_enrich import load_cms_providers
        cms_dir = tmp_path / "cms"
        cms_dir.mkdir()
        self._write_cms(cms_dir, [{"ST_ADR": "2435  BOULEVARD LUIS A FERRE", "PRVDR_NUM": "400002",
                                    "FAC_NAME": "PILA", "STATE_CD": "PR",
                                    "CITY_NAME": "PONCE", "ZIP_CD": "00717"}])
        df = load_cms_providers(tmp_path)
        assert df.iloc[0]["_addr_num"] == "2435"

    def test_carretera(self, tmp_path):
        # L-03: CARRETERA keyword
        from lib.cms_health_enrich import load_cms_providers
        cms_dir = tmp_path / "cms"
        cms_dir.mkdir()
        self._write_cms(cms_dir, [{"ST_ADR": "CARRETERA 135 KM 64.2", "PRVDR_NUM": "400003",
                                    "FAC_NAME": "CENTRO", "STATE_CD": "PR",
                                    "CITY_NAME": "YAUCO", "ZIP_CD": "00698"}])
        df = load_cms_providers(tmp_path)
        assert df.iloc[0]["_addr_num"] == "135"

    def test_carr_abbrev(self, tmp_path):
        # L-04: CARR. with period and single-digit route number
        # NOTE: the extraction regex requires \d{2,4}, so single-digit "2" does NOT match.
        # _addr_num will be empty for CARR. 2 style addresses — this is a known limitation.
        from lib.cms_health_enrich import load_cms_providers
        cms_dir = tmp_path / "cms"
        cms_dir.mkdir()
        self._write_cms(cms_dir, [{"ST_ADR": "CARR. 2 KM 3.5", "PRVDR_NUM": "400004",
                                    "FAC_NAME": "REGIONAL", "STATE_CD": "PR",
                                    "CITY_NAME": "MAYAGUEZ", "ZIP_CD": "00680"}])
        df = load_cms_providers(tmp_path)
        # Single-digit route numbers fall outside the \d{2,4} pattern — addr_num is empty
        assert df.iloc[0]["_addr_num"] == ""

    def test_carr_no_period(self, tmp_path):
        # L-05: CARR without period
        from lib.cms_health_enrich import load_cms_providers
        cms_dir = tmp_path / "cms"
        cms_dir.mkdir()
        self._write_cms(cms_dir, [{"ST_ADR": "CARR 52 BO JAGUEY", "PRVDR_NUM": "400005",
                                    "FAC_NAME": "HOSPITAL A", "STATE_CD": "PR",
                                    "CITY_NAME": "GUAYANILLA", "ZIP_CD": "00656"}])
        df = load_cms_providers(tmp_path)
        assert df.iloc[0]["_addr_num"] == "52"

    def test_pr_dash(self, tmp_path):
        # L-06: PR-135 highway reference
        from lib.cms_health_enrich import load_cms_providers
        cms_dir = tmp_path / "cms"
        cms_dir.mkdir()
        self._write_cms(cms_dir, [{"ST_ADR": "PR-135 KM 64.2", "PRVDR_NUM": "400006",
                                    "FAC_NAME": "HOSPITAL B", "STATE_CD": "PR",
                                    "CITY_NAME": "YAUCO", "ZIP_CD": "00698"}])
        df = load_cms_providers(tmp_path)
        assert df.iloc[0]["_addr_num"] == "135"

    def test_po_box_no_num(self, tmp_path):
        # L-07: PO Box — no leading 2-4 digit highway number, no leading street number
        from lib.cms_health_enrich import load_cms_providers
        cms_dir = tmp_path / "cms"
        cms_dir.mkdir()
        self._write_cms(cms_dir, [{"ST_ADR": "PO BOX 1234", "PRVDR_NUM": "400007",
                                    "FAC_NAME": "CLINIC C", "STATE_CD": "PR",
                                    "CITY_NAME": "SAN JUAN", "ZIP_CD": "00901"}])
        df = load_cms_providers(tmp_path)
        # PO BOX 1234 — leading digits regex extracts "1234" from "PO BOX 1234"?
        # No: "^(\d+)" requires DIGITS at the very START. "PO BOX 1234" starts with "P" — empty.
        assert df.iloc[0]["_addr_num"] == ""

    def test_zip5_from_zip_cd(self, tmp_path):
        # L-08: ZIP5 truncation from full ZIP+4
        from lib.cms_health_enrich import load_cms_providers
        cms_dir = tmp_path / "cms"
        cms_dir.mkdir()
        self._write_cms(cms_dir, [{"ST_ADR": "100 MAIN ST", "PRVDR_NUM": "400008",
                                    "FAC_NAME": "HOSPITAL D", "STATE_CD": "PR",
                                    "CITY_NAME": "PONCE", "ZIP_CD": "00731-1234"}])
        df = load_cms_providers(tmp_path)
        assert df.iloc[0]["_zip5"] == "00731"

    def test_prvdr_num_is_string(self, tmp_path):
        # L-09: PRVDR_NUM stored as integer in parquet must become a string dtype after load
        from lib.cms_health_enrich import load_cms_providers
        cms_dir = tmp_path / "cms"
        cms_dir.mkdir()
        import pyarrow as pa
        import pyarrow.parquet as pq
        tbl = pa.table({
            "PRVDR_NUM": pa.array([400001], type=pa.int64()),
            "FAC_NAME": ["HOSPITAL E"],
            "ST_ADR": ["100 MAIN ST"],
            "STATE_CD": ["PR"],
            "CITY_NAME": ["PONCE"],
            "ZIP_CD": ["00731"],
        })
        pq.write_table(tbl, cms_dir / "cms_hospital_providers.parquet")
        df = load_cms_providers(tmp_path)
        # After astype(str).str.strip(), PRVDR_NUM should be a string dtype
        assert pd.api.types.is_string_dtype(df["PRVDR_NUM"])

    @staticmethod
    def _write_cms(cms_dir, records):
        import pyarrow as pa
        import pyarrow.parquet as pq
        pq.write_table(pa.Table.from_pandas(pd.DataFrame(records)),
                       cms_dir / "cms_hospital_providers.parquet")


# ---------------------------------------------------------------------------
# Section 3 — _build_result_row
# ---------------------------------------------------------------------------

class TestBuildResultRow:
    """Tests for _build_result_row (B-01 through B-09)."""

    def _cms_row(self, **kwargs):
        defaults = {
            "PRVDR_NUM": "400001",
            "PRVDR_CTGRY_CD": "01",
            "PRVDR_CTGRY_SBTYP_CD": "A",
            "BED_CNT": pd.array([120], dtype="Int64")[0],
            "CRTFD_BED_CNT": pd.array([100], dtype="Int64")[0],
            "OPRTG_ROOM_CNT": pd.array([4], dtype="Int64")[0],
        }
        defaults.update(kwargs)
        return pd.Series(defaults)

    def test_core_columns_present(self):
        # B-01
        row = _build_result_row("lid_1", self._cms_row(), 0.9, "zip_fuzzy", None,
                                ["BED_CNT", "CRTFD_BED_CNT", "OPRTG_ROOM_CNT"])
        for key in ("lifeline_id", "cms_provider_num", "cms_match_score",
                    "cms_match_method", "cms_match_distance_m"):
            assert key in row, f"Missing key: {key}"

    def test_compat_bed_cnt(self):
        # B-02
        row = _build_result_row("lid_1", self._cms_row(BED_CNT=pd.array([120], dtype="Int64")[0]),
                                0.9, "zip_fuzzy", None, ["BED_CNT"])
        assert row["cms_bed_cnt"] == 120

    def test_compat_bed_cnt_na(self):
        # B-03: NaN BED_CNT → 0 (backward-compat)
        row = _build_result_row("lid_1", self._cms_row(BED_CNT=pd.NA),
                                0.9, "zip_fuzzy", None, ["BED_CNT"])
        assert row["cms_bed_cnt"] == 0

    def test_extra_cnt_column(self):
        # B-04: STAFF_CNT (non-compat) → cms_staff_cnt
        row = _build_result_row("lid_1",
                                self._cms_row(STAFF_CNT=pd.array([50], dtype="Int64")[0]),
                                0.9, "zip_fuzzy", None, ["BED_CNT", "STAFF_CNT"])
        assert row["cms_staff_cnt"] == 50

    def test_extra_cnt_na(self):
        # B-05: non-compat NaN → pd.NA (not zero)
        row = _build_result_row("lid_1", self._cms_row(STAFF_CNT=pd.NA),
                                0.9, "zip_fuzzy", None, ["BED_CNT", "STAFF_CNT"])
        assert pd.isna(row["cms_staff_cnt"])

    def test_score_rounded(self):
        # B-06: score rounded to 4dp
        row = _build_result_row("lid_1", self._cms_row(), 0.88765, "zip_fuzzy", None, [])
        assert row["cms_match_score"] == 0.8877

    def test_distance_none(self):
        # B-07: None distance passes through
        row = _build_result_row("lid_1", self._cms_row(), 0.9, "zip_fuzzy", None, [])
        assert row["cms_match_distance_m"] is None

    def test_provider_category(self):
        # B-08
        row = _build_result_row("lid_1", self._cms_row(PRVDR_CTGRY_CD="01"),
                                0.9, "zip_fuzzy", None, [])
        assert row["cms_provider_category"] == "01"

    def test_provider_subtype(self):
        # B-09
        row = _build_result_row("lid_1", self._cms_row(PRVDR_CTGRY_SBTYP_CD="A"),
                                0.9, "zip_fuzzy", None, [])
        assert row["cms_provider_subtype"] == "A"


# ---------------------------------------------------------------------------
# Section 4 — _tier2_zip_fuzzy
# ---------------------------------------------------------------------------

class TestTier2ZipFuzzy:
    """Tests for _tier2_zip_fuzzy (T2-01 through T2-09)."""

    def _health(self, **kwargs):
        base = {
            "lifeline_id": ["lid_001"],
            "_name_norm": ["san lucas"],
            "_zip5": ["00731"],
            "_state": ["PR"],
            "_city": ["PONCE"],
        }
        base.update(kwargs)
        return pd.DataFrame(base)

    def _cms(self, **kwargs):
        base = {
            "PRVDR_NUM": ["400001"],
            "_name_norm": ["san lucas"],
            "_zip5": ["00731"],
            "STATE_CD": ["PR"],
            "CITY_NAME": ["PONCE"],
            "BED_CNT": pd.array([120], dtype="Int64"),
            "CRTFD_BED_CNT": pd.array([100], dtype="Int64"),
            "OPRTG_ROOM_CNT": pd.array([4], dtype="Int64"),
        }
        base.update(kwargs)
        return pd.DataFrame(base)

    def test_state_zip_match(self):
        # T2-01: matching state+ZIP and similar name → match returned
        results = _tier2_zip_fuzzy(self._health(), self._cms(), 0.80,
                                   ["BED_CNT", "CRTFD_BED_CNT", "OPRTG_ROOM_CNT"], set())
        assert len(results) == 1
        assert results[0]["lifeline_id"] == "lid_001"

    def test_name_below_threshold(self):
        # T2-02: same ZIP/state but completely different names → no match
        results = _tier2_zip_fuzzy(
            self._health(_name_norm=["completely unrelated"]),
            self._cms(),
            0.80,
            ["BED_CNT"],
            set(),
        )
        assert results == []

    def test_zip_only_fallback(self):
        # T2-03: state is empty, but ZIP matches → tier 2a' path fires
        results = _tier2_zip_fuzzy(
            self._health(_state=[""]),
            self._cms(),
            0.80,
            ["BED_CNT"],
            set(),
        )
        assert len(results) == 1

    def test_already_matched_skipped(self):
        # T2-04: lifeline_id in already_matched → excluded
        results = _tier2_zip_fuzzy(self._health(), self._cms(), 0.80,
                                   ["BED_CNT"], already_matched={"lid_001"})
        assert results == []

    def test_cms_already_matched_skipped(self):
        # T2-05: PRVDR_NUM in cms_already_matched → not used
        results = _tier2_zip_fuzzy(self._health(), self._cms(), 0.80,
                                   ["BED_CNT"], set(), cms_already_matched={"400001"})
        assert results == []

    def test_empty_health(self):
        # T2-06: zero-row health DataFrame
        empty_h = pd.DataFrame(columns=["lifeline_id", "_name_norm", "_zip5", "_state", "_city"])
        results = _tier2_zip_fuzzy(empty_h, self._cms(), 0.80, ["BED_CNT"], set())
        assert results == []

    def test_empty_cms(self):
        # T2-07: zero-row CMS DataFrame
        empty_c = pd.DataFrame(columns=["PRVDR_NUM", "_name_norm", "_zip5", "STATE_CD",
                                         "CITY_NAME", "BED_CNT"])
        results = _tier2_zip_fuzzy(self._health(), empty_c, 0.80, ["BED_CNT"], set())
        assert results == []

    def test_best_score_wins(self):
        # T2-08: two CMS candidates for same POI, both above threshold → best score selected,
        # exactly one result returned (drop_duplicates on lifeline_id)
        cms2 = pd.DataFrame({
            "PRVDR_NUM": ["400001", "400002"],
            # Both names are close to "san lucas" and score above 0.80
            "_name_norm": ["san lucas", "san lucas ii"],
            "_zip5": ["00731", "00731"],
            "STATE_CD": ["PR", "PR"],
            "CITY_NAME": ["PONCE", "PONCE"],
            "BED_CNT": pd.array([120, 50], dtype="Int64"),
            "CRTFD_BED_CNT": pd.array([100, 40], dtype="Int64"),
            "OPRTG_ROOM_CNT": pd.array([4, 2], dtype="Int64"),
        })
        results = _tier2_zip_fuzzy(self._health(), cms2, 0.80,
                                   ["BED_CNT", "CRTFD_BED_CNT", "OPRTG_ROOM_CNT"], set())
        assert len(results) == 1  # only one result per POI

    def test_method_recorded(self):
        # T2-09: cms_match_method == "zip_fuzzy"
        results = _tier2_zip_fuzzy(self._health(), self._cms(), 0.80, ["BED_CNT"], set())
        assert len(results) == 1
        assert results[0]["cms_match_method"] == "zip_fuzzy"

    def test_san_juan_capestrano_state_from_display_name(self):
        # T2-10 — REGRESSION: CPC Hospital San Juan Capestrano — state "" on POI but
        # extractable from display_name (",  PR 00926"); CMS name "san juan capestrano"
        # is an exact token-subset of POI name "cpc hospital san juan capestrano"
        # (token_set_ratio = 100).  ZIP codes differ (00926 vs 00928).
        # Tier 2d (state-from-display_name, token_set_ratio >= 0.90) closes the gap.
        from lib.cms_health_enrich import _normalize_name
        cms_name = _normalize_name("SAN JUAN CAPESTRANO  HOSPITAL INC")   # "san juan capestrano"
        poi_name = _normalize_name("CPC Hospital San Juan Capestrano")     # "cpc hospital san juan capestrano"
        health = pd.DataFrame({
            "lifeline_id": ["lid_capestrano"],
            "_name_norm": [poi_name],
            "_zip5": ["00926"],
            "_state": [""],
            "_city": [""],
            "_addr_num": [""],
            "display_name": ["CPC Hospital San Juan Capestrano, Camino Las Lomas, San Juan, PR 00926"],
        })
        cms = pd.DataFrame({
            "PRVDR_NUM": ["400071"],
            "_name_norm": [cms_name],
            "_zip5": ["00928"],
            "STATE_CD": ["PR"],
            "CITY_NAME": ["RIO PIEDRAS"],
            "_addr_num": ["877"],
            "BED_CNT": pd.array([108], dtype="Int64"),
            "CRTFD_BED_CNT": pd.array([108], dtype="Int64"),
            "OPRTG_ROOM_CNT": pd.array([pd.NA], dtype="Int64"),
        })
        results = _tier2_zip_fuzzy(health, cms, 0.80,
                                   ["BED_CNT", "CRTFD_BED_CNT", "OPRTG_ROOM_CNT"], set())
        assert len(results) == 1
        assert results[0]["lifeline_id"] == "lid_capestrano"
        assert results[0]["cms_match_method"] == "zip_fuzzy"

    def test_auxilio_mutuo_city_token_set(self):
        # T2-11 — REGRESSION: Hospital Auxilio Mutuo — ZIP mismatch (00919 vs 00917),
        # but state=PR + city=HATO REY match on both sides.  POI name "auxilio mutuo"
        # is a perfect token-subset of CMS name "auxilio mutuo hosp transplant"
        # (token_set_ratio=100, token_sort_ratio≈65).  Tier 2b generates the candidate
        # but fails scoring; Tier 2e (state+city, token_set_ratio >= threshold) closes the gap.
        from lib.cms_health_enrich import _normalize_name
        poi_name = _normalize_name("Hospital Auxilio Mutuo")
        cms_name = _normalize_name("AUXILIO MUTUO HOSP TRANSPLANT")
        health = pd.DataFrame({
            "lifeline_id": ["lid_auxilio"],
            "_name_norm": [poi_name],
            "_zip5": ["00919"],
            "_state": ["PR"],
            "_city": ["HATO REY"],
            "_addr_num": ["735"],
        })
        cms = pd.DataFrame({
            "PRVDR_NUM": ["400028"],
            "_name_norm": [cms_name],
            "_zip5": ["00917"],
            "STATE_CD": ["PR"],
            "CITY_NAME": ["HATO REY"],
            "_addr_num": [""],
            "BED_CNT": pd.array([268], dtype="Int64"),
            "CRTFD_BED_CNT": pd.array([268], dtype="Int64"),
            "OPRTG_ROOM_CNT": pd.array([pd.NA], dtype="Int64"),
        })
        results = _tier2_zip_fuzzy(health, cms, 0.80,
                                   ["BED_CNT", "CRTFD_BED_CNT", "OPRTG_ROOM_CNT"], set())
        assert len(results) == 1
        assert results[0]["lifeline_id"] == "lid_auxilio"
        assert results[0]["cms_match_method"] == "zip_fuzzy"


# ---------------------------------------------------------------------------
# Section 5 — _tier3_addr_zip
# ---------------------------------------------------------------------------

class TestTier3AddrZip:
    """Tests for _tier3_addr_zip (T3-01 through T3-11)."""

    def _health(self, **kwargs):
        base = {
            "lifeline_id": ["lid_001"],
            "_name_norm": ["san lucas"],
            "_zip5": ["00731"],
            "_state": ["PR"],
            "_city": ["PONCE"],
            "_addr_num": ["917"],
            "display_name": ["Hospital San Lucas, 917 Ave Tito Castro, Ponce PR 00731"],
        }
        base.update(kwargs)
        return pd.DataFrame(base)

    def _cms(self, **kwargs):
        base = {
            "PRVDR_NUM": ["400001"],
            "_name_norm": ["san lucas"],
            "_zip5": ["00731"],
            "STATE_CD": ["PR"],
            "CITY_NAME": ["PONCE"],
            "_addr_num": ["917"],
            "BED_CNT": pd.array([120], dtype="Int64"),
            "CRTFD_BED_CNT": pd.array([100], dtype="Int64"),
            "OPRTG_ROOM_CNT": pd.array([4], dtype="Int64"),
        }
        base.update(kwargs)
        return pd.DataFrame(base)

    def test_addr_zip_exact_match(self):
        # T3-01: matching _addr_num + _zip5 + similar name → match
        results = _tier3_addr_zip(self._health(), self._cms(),
                                  ["BED_CNT", "CRTFD_BED_CNT", "OPRTG_ROOM_CNT"], set())
        assert len(results) == 1
        assert results[0]["lifeline_id"] == "lid_001"

    def test_san_lucas_case(self):
        # T3-02 — REGRESSION: Hospital Episcopal San Lucas II vs Hospital San Lucas
        # addr 917, zip 00731 — addr+zip join fires, then low name threshold (0.50) confirms
        results = _tier3_addr_zip(
            self._health(_name_norm=["episcopal san lucas ii"]),
            self._cms(),
            ["BED_CNT"],
            set(),
            addr_name_threshold=0.50,
        )
        assert len(results) == 1

    def test_dr_pila_case(self):
        # T3-03 — REGRESSION: Hospital Metropolitano Dr. Pila
        results = _tier3_addr_zip(
            self._health(
                lifeline_id=["lid_002"],
                _name_norm=["metropolitano pila"],
                _zip5=["00717"],
                _addr_num=["2435"],
                display_name=["Hospital Metropolitano Dr. Pila, 2435 Boulevard Luis A. Ferre, Ponce PR 00717"],
            ),
            self._cms(
                PRVDR_NUM=["400002"],
                _name_norm=["metropolitano pila"],
                _zip5=["00717"],
                _addr_num=["2435"],
            ),
            ["BED_CNT"],
            set(),
        )
        assert len(results) == 1

    def test_no_addr_num_on_poi(self):
        # T3-04: empty _addr_num on POI → no match
        results = _tier3_addr_zip(self._health(_addr_num=[""]), self._cms(),
                                  ["BED_CNT"], set())
        assert results == []

    def test_no_addr_num_on_cms(self):
        # T3-05: empty _addr_num on CMS → no match
        results = _tier3_addr_zip(self._health(), self._cms(_addr_num=[""]),
                                  ["BED_CNT"], set())
        assert results == []

    def test_wrong_addr_num(self):
        # T3-06: addr mismatch → inner join produces no candidates
        results = _tier3_addr_zip(self._health(_addr_num=["100"]), self._cms(_addr_num=["200"]),
                                  ["BED_CNT"], set())
        assert results == []

    def test_name_sanity_below_threshold(self):
        # T3-07: addr+ZIP match but completely different names → rejected
        results = _tier3_addr_zip(
            self._health(_name_norm=["zzzz unrelated hospital"]),
            self._cms(),
            ["BED_CNT"],
            set(),
            addr_name_threshold=0.50,
        )
        assert results == []

    def test_already_matched_skipped(self):
        # T3-08
        results = _tier3_addr_zip(self._health(), self._cms(),
                                  ["BED_CNT"], already_matched={"lid_001"})
        assert results == []

    def test_cms_already_matched_skipped(self):
        # T3-09
        results = _tier3_addr_zip(self._health(), self._cms(),
                                  ["BED_CNT"], set(), cms_already_matched={"400001"})
        assert results == []

    def test_method_recorded(self):
        # T3-10
        results = _tier3_addr_zip(self._health(), self._cms(), ["BED_CNT"], set())
        assert results[0]["cms_match_method"] == "addr_zip"

    def test_carretera_cms_match(self):
        # T3-11: CMS _addr_num="135" (from CARRETERA 135) matches POI _addr_num="135" (from PR-135)
        results = _tier3_addr_zip(
            self._health(_name_norm=["yauco medical"], _zip5=["00698"], _addr_num=["135"],
                         display_name=["Yauco Medical, PR-135, Yauco PR 00698"]),
            self._cms(_name_norm=["yauco medical"], _zip5=["00698"], _addr_num=["135"]),
            ["BED_CNT"],
            set(),
        )
        assert len(results) == 1

    def test_dr_pila_addr_city_zip_mismatch(self):
        # T3-12 — REGRESSION: Hospital Metropolitano Dr. Pila — addr_num + city confirm the match
        # even though ZIP codes differ (OSM=00733 vs CMS=00717) and the POI has no addr:state.
        # Tier 3b (addr_num + city) bridges the ZIP gap; method recorded as "addr_city".
        from lib.cms_health_enrich import _normalize_name
        name = _normalize_name("HOSPITAL METROPOLITANO DR PILA")
        results = _tier3_addr_zip(
            self._health(
                lifeline_id=["lid_pila"],
                _name_norm=[name],
                _zip5=["00733"],      # OSM ZIP — differs from CMS
                _state=[""],          # no addr:state in OSM for this PR hospital
                _city=["PONCE"],
                _addr_num=["2435"],
                display_name=["El Hospital Metropolitano Dr. Pila, 2435 Boulevard Luis A. Ferré, Ponce, PR 00733"],
            ),
            self._cms(
                PRVDR_NUM=["400002"],
                _name_norm=[name],
                _zip5=["00717"],      # CMS ZIP — differs from POI
                STATE_CD=["PR"],
                CITY_NAME=["PONCE"],
                _addr_num=["2435"],
            ),
            ["BED_CNT", "CRTFD_BED_CNT", "OPRTG_ROOM_CNT"],
            set(),
        )
        assert len(results) == 1
        assert results[0]["lifeline_id"] == "lid_pila"
        assert results[0]["cms_match_method"] == "addr_city"


# ---------------------------------------------------------------------------
# Section 6 — _tier4_name_zip
# ---------------------------------------------------------------------------

class TestTier4NameZip:
    """Tests for _tier4_name_zip (T4-01 through T4-12)."""

    def _health(self, **kwargs):
        base = {
            "lifeline_id": ["lid_001"],
            "_name_norm": ["menonita caguas"],
            "_zip5": ["00725"],
            "_state": ["PR"],
            "_city": ["CAGUAS"],
        }
        base.update(kwargs)
        return pd.DataFrame(base)

    def _cms(self, **kwargs):
        base = {
            "PRVDR_NUM": ["400010"],
            "_name_norm": ["general menonita caguas"],
            "_zip5": ["00725"],
            "STATE_CD": ["PR"],
            "CITY_NAME": ["CAGUAS"],
            "BED_CNT": pd.array([180], dtype="Int64"),
            "CRTFD_BED_CNT": pd.array([160], dtype="Int64"),
            "OPRTG_ROOM_CNT": pd.array([6], dtype="Int64"),
        }
        base.update(kwargs)
        return pd.DataFrame(base)

    def test_menonita_caguas(self):
        # T4-01 — REGRESSION: Hospital General Menonita de Caguas / HOSPITAL MENONITA CAGUAS INC
        # token_set_ratio catches subset relationship: "menonita caguas" ⊂ "general menonita caguas"
        results = _tier4_name_zip(self._health(), self._cms(),
                                  ["BED_CNT", "CRTFD_BED_CNT", "OPRTG_ROOM_CNT"], set(), 0.75)
        assert len(results) == 1
        assert results[0]["lifeline_id"] == "lid_001"

    def test_doctors_center(self):
        # T4-02 — REGRESSION: Doctors Center Bayamon — identical after normalize → score 1.0
        results = _tier4_name_zip(
            self._health(_name_norm=["doctors center bayamon"], _zip5=["00957"]),
            self._cms(_name_norm=["doctors center bayamon"], _zip5=["00957"]),
            ["BED_CNT"],
            set(),
            0.75,
        )
        assert len(results) == 1

    def test_score_below_threshold(self):
        # T4-03: low token_set_ratio → no match
        results = _tier4_name_zip(
            self._health(_name_norm=["completely different hospital"]),
            self._cms(),
            ["BED_CNT"],
            set(),
            0.75,
        )
        assert results == []

    def test_zip_required_on_health(self):
        # T4-04: empty _zip5 on POI → excluded
        results = _tier4_name_zip(self._health(_zip5=[""]), self._cms(), ["BED_CNT"], set(), 0.75)
        assert results == []

    def test_zip_required_on_cms(self):
        # T4-05: empty _zip5 on CMS → filtered before merge
        results = _tier4_name_zip(self._health(), self._cms(_zip5=[""]), ["BED_CNT"], set(), 0.75)
        assert results == []

    def test_zip_mismatch(self):
        # T4-06: different ZIPs → inner join produces no candidates
        results = _tier4_name_zip(
            self._health(_zip5=["00725"]),
            self._cms(_zip5=["00901"]),
            ["BED_CNT"],
            set(),
            0.75,
        )
        assert results == []

    def test_already_matched_skipped(self):
        # T4-07
        results = _tier4_name_zip(self._health(), self._cms(), ["BED_CNT"],
                                  already_matched={"lid_001"})
        assert results == []

    def test_cms_already_matched_skipped(self):
        # T4-08
        results = _tier4_name_zip(self._health(), self._cms(), ["BED_CNT"], set(), 0.75,
                                  cms_already_matched={"400010"})
        assert results == []

    def test_best_score_wins(self):
        # T4-09: two CMS candidates → one result per POI (best score)
        cms2 = pd.DataFrame({
            "PRVDR_NUM": ["400010", "400011"],
            "_name_norm": ["general menonita caguas", "menonita hospital caguas regional"],
            "_zip5": ["00725", "00725"],
            "STATE_CD": ["PR", "PR"],
            "CITY_NAME": ["CAGUAS", "CAGUAS"],
            "BED_CNT": pd.array([180, 90], dtype="Int64"),
            "CRTFD_BED_CNT": pd.array([160, 80], dtype="Int64"),
            "OPRTG_ROOM_CNT": pd.array([6, 3], dtype="Int64"),
        })
        results = _tier4_name_zip(self._health(), cms2,
                                  ["BED_CNT", "CRTFD_BED_CNT", "OPRTG_ROOM_CNT"], set(), 0.75)
        assert len(results) == 1

    def test_method_recorded(self):
        # T4-10
        results = _tier4_name_zip(self._health(), self._cms(), ["BED_CNT"], set(), 0.75)
        assert results[0]["cms_match_method"] == "name_zip"

    def test_token_set_better_than_sort(self):
        # T4-11: token_set_ratio catches what token_sort_ratio misses at same threshold
        from rapidfuzz import fuzz
        h_name = "menonita caguas"
        c_name = "general menonita caguas"
        sort_score = fuzz.token_sort_ratio(h_name, c_name) / 100.0
        set_score = fuzz.token_set_ratio(h_name, c_name) / 100.0
        # The set score should exceed sort score for this subset/superset pair
        assert set_score > sort_score
        # And the tier fires
        results = _tier4_name_zip(
            self._health(_name_norm=[h_name]),
            self._cms(_name_norm=[c_name]),
            ["BED_CNT"],
            set(),
            0.75,
        )
        assert len(results) == 1

    def test_name_empty_poi_skipped(self):
        # T4-12: empty _name_norm on POI → excluded
        results = _tier4_name_zip(self._health(_name_norm=[""]), self._cms(),
                                  ["BED_CNT"], set(), 0.75)
        assert results == []


# ---------------------------------------------------------------------------
# Section 7 — Cross-tier deduplication (integration)
# ---------------------------------------------------------------------------

class TestCrossTierDeduplication:
    """
    Verify that already_matched / cms_already_matched sets prevent double-matching
    when tiers are chained sequentially.
    """

    def _health2(self):
        return pd.DataFrame({
            "lifeline_id": ["lid_001", "lid_002"],
            "_name_norm": ["san lucas", "pila metropolitan"],
            "_zip5": ["00731", "00717"],
            "_state": ["PR", "PR"],
            "_city": ["PONCE", "PONCE"],
            "_addr_num": ["917", "2435"],
            "display_name": ["Hospital San Lucas", "Hospital Metropolitano Pila"],
        })

    def _cms2(self):
        return pd.DataFrame({
            "PRVDR_NUM": ["400001", "400002"],
            "_name_norm": ["san lucas", "pila metropolitan"],
            "_zip5": ["00731", "00717"],
            "STATE_CD": ["PR", "PR"],
            "CITY_NAME": ["PONCE", "PONCE"],
            "_addr_num": ["917", "2435"],
            "BED_CNT": pd.array([120, 200], dtype="Int64"),
            "CRTFD_BED_CNT": pd.array([100, 180], dtype="Int64"),
            "OPRTG_ROOM_CNT": pd.array([4, 8], dtype="Int64"),
        })

    def test_tier2_doesnt_reuse_tier1_poi(self):
        # D-01: lifeline_id already in already_matched → Tier 2 skips it
        results = _tier2_zip_fuzzy(
            self._health2().iloc[:1],  # only lid_001
            self._cms2(),
            0.80,
            ["BED_CNT"],
            already_matched={"lid_001"},
        )
        assert results == []

    def test_tier3_doesnt_reuse_tier2_cms(self):
        # D-02: PRVDR_NUM claimed by Tier 2 → Tier 3 cannot use it
        results = _tier3_addr_zip(
            self._health2(),
            self._cms2(),
            ["BED_CNT"],
            set(),
            cms_already_matched={"400001", "400002"},
        )
        assert results == []

    def test_tier4_doesnt_reuse_tier3_cms(self):
        # D-03: PRVDR_NUM claimed by Tier 3 → Tier 4 cannot use it
        results = _tier4_name_zip(
            pd.DataFrame({
                "lifeline_id": ["lid_003"],
                "_name_norm": ["menonita caguas"],
                "_zip5": ["00725"],
                "_state": ["PR"],
                "_city": ["CAGUAS"],
            }),
            pd.DataFrame({
                "PRVDR_NUM": ["400010"],
                "_name_norm": ["general menonita caguas"],
                "_zip5": ["00725"],
                "STATE_CD": ["PR"],
                "CITY_NAME": ["CAGUAS"],
                "BED_CNT": pd.array([180], dtype="Int64"),
            }),
            ["BED_CNT"],
            set(),
            0.75,
            cms_already_matched={"400010"},
        )
        assert results == []

    def test_tier4_doesnt_reuse_tier3_poi(self):
        # D-04: lifeline_id already matched in Tier 3 → Tier 4 skips it
        results = _tier4_name_zip(
            pd.DataFrame({
                "lifeline_id": ["lid_003"],
                "_name_norm": ["menonita caguas"],
                "_zip5": ["00725"],
                "_state": ["PR"],
                "_city": ["CAGUAS"],
            }),
            pd.DataFrame({
                "PRVDR_NUM": ["400010"],
                "_name_norm": ["general menonita caguas"],
                "_zip5": ["00725"],
                "STATE_CD": ["PR"],
                "CITY_NAME": ["CAGUAS"],
                "BED_CNT": pd.array([180], dtype="Int64"),
            }),
            ["BED_CNT"],
            already_matched={"lid_003"},
        )
        assert results == []

    def test_same_cms_can_match_different_pois_within_tier(self):
        # D-05: within a single tier, two POIs may each match the same CMS
        # (cross-POI dedup happens later in build_attr_health_cms)
        health = pd.DataFrame({
            "lifeline_id": ["lid_A", "lid_B"],
            "_name_norm": ["san lucas", "san lucas"],
            "_zip5": ["00731", "00731"],
            "_state": ["PR", "PR"],
            "_city": ["PONCE", "PONCE"],
        })
        cms = pd.DataFrame({
            "PRVDR_NUM": ["400001"],
            "_name_norm": ["san lucas"],
            "_zip5": ["00731"],
            "STATE_CD": ["PR"],
            "CITY_NAME": ["PONCE"],
            "BED_CNT": pd.array([120], dtype="Int64"),
            "CRTFD_BED_CNT": pd.array([100], dtype="Int64"),
            "OPRTG_ROOM_CNT": pd.array([4], dtype="Int64"),
        })
        results = _tier2_zip_fuzzy(health, cms, 0.80,
                                   ["BED_CNT", "CRTFD_BED_CNT", "OPRTG_ROOM_CNT"], set())
        # Both POIs should get a result (dedup is caller's responsibility)
        assert len(results) == 2


# ---------------------------------------------------------------------------
# Section 8 — Edge cases and guard conditions
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Edge cases and guard conditions (E-01 through E-07)."""

    def test_normalize_pr_highway_address(self):
        # E-01: normalize a PR highway reference — should not crash
        result = _normalize_name("PR-135 KM 64")
        assert isinstance(result, str)

    def test_all_tiers_empty_returns_empty_list(self):
        # E-02: all tier functions return [] for zero-row DataFrames
        empty_h = pd.DataFrame(columns=["lifeline_id", "_name_norm", "_zip5", "_state",
                                         "_city", "_addr_num", "display_name"])
        empty_c = pd.DataFrame(columns=["PRVDR_NUM", "_name_norm", "_zip5", "STATE_CD",
                                         "CITY_NAME", "_addr_num", "BED_CNT"])
        assert _tier2_zip_fuzzy(empty_h, empty_c, 0.80, [], set()) == []
        assert _tier3_addr_zip(empty_h, empty_c, [], set()) == []
        assert _tier4_name_zip(empty_h, empty_c, [], set(), 0.75) == []

    def test_build_result_row_missing_compat_columns(self):
        # E-03: BED_CNT not present → cms_bed_cnt=0 (no KeyError)
        row_series = pd.Series({"PRVDR_NUM": "400001", "PRVDR_CTGRY_CD": "", "PRVDR_CTGRY_SBTYP_CD": ""})
        result = _build_result_row("lid_1", row_series, 0.9, "zip_fuzzy", None,
                                   ["BED_CNT", "CRTFD_BED_CNT", "OPRTG_ROOM_CNT"])
        assert result["cms_bed_cnt"] == 0

    def test_detect_cnt_columns(self):
        # E-04: _detect_cnt_columns finds BED_CNT and STAFF_CNT
        df = pd.DataFrame(columns=["BED_CNT", "STAFF_CNT", "FAC_NAME"])
        cols = _detect_cnt_columns(df)
        assert "BED_CNT" in cols
        assert "STAFF_CNT" in cols
        assert "FAC_NAME" not in cols

    def test_detect_cnt_case_insensitive(self):
        # E-05: lowercase column name still detected (upper-case comparison)
        df = pd.DataFrame(columns=["bed_cnt"])
        cols = _detect_cnt_columns(df)
        assert "bed_cnt" in cols

    def test_tier2_no_zip_on_poi(self):
        # E-06: all POIs have short _zip5 → no Tier 2a candidates; function returns []
        health = pd.DataFrame({
            "lifeline_id": ["lid_X"],
            "_name_norm": ["regional"],
            "_zip5": [""],
            "_state": [""],
            "_city": [""],
        })
        cms = pd.DataFrame({
            "PRVDR_NUM": ["400001"],
            "_name_norm": ["regional"],
            "_zip5": ["00731"],
            "STATE_CD": ["PR"],
            "CITY_NAME": ["PONCE"],
            "BED_CNT": pd.array([100], dtype="Int64"),
        })
        results = _tier2_zip_fuzzy(health, cms, 0.80, ["BED_CNT"], set())
        assert results == []

    def test_tier4_no_zip_on_all_cms(self):
        # E-07: all CMS _zip5 empty → cms_m empty after filter → returns []
        health = pd.DataFrame({
            "lifeline_id": ["lid_Y"],
            "_name_norm": ["menonita caguas"],
            "_zip5": ["00725"],
            "_state": ["PR"],
            "_city": ["CAGUAS"],
        })
        cms = pd.DataFrame({
            "PRVDR_NUM": ["400010"],
            "_name_norm": ["general menonita caguas"],
            "_zip5": [""],
            "STATE_CD": ["PR"],
            "CITY_NAME": ["CAGUAS"],
            "BED_CNT": pd.array([180], dtype="Int64"),
        })
        results = _tier4_name_zip(health, cms, ["BED_CNT"], set(), 0.75)
        assert results == []


# ---------------------------------------------------------------------------
# Section 9 — Known matching gaps (currently failing, drive future fixes)
# ---------------------------------------------------------------------------

class TestKnownMatchingGaps:
    """
    Test cases that document real-world match failures in the current pipeline.
    Each test asserts the match SHOULD be found — they are intentionally failing
    until the underlying gap is resolved.  Do NOT mark these xfail; their red
    status is the signal that drives the fix.

    When a gap is closed, move the test to the appropriate tier class above and
    add a # REGRESSION comment so it becomes a regression anchor.

    Gaps closed and promoted:
      - test_dr_pila_zip_mismatch          → TestTier3AddrZip::test_dr_pila_addr_city_zip_mismatch
      - test_san_juan_capestrano_spatial_only → TestTier2ZipFuzzy::test_san_juan_capestrano_state_from_display_name
      - test_auxilio_mutuo_city_token_set  → TestTier2ZipFuzzy::test_auxilio_mutuo_city_token_set
    """
    pass  # All gaps currently closed; add new gap tests here as they are discovered.


# ---------------------------------------------------------------------------
# Section 7b — Tier 1 multi-candidate selection
# ---------------------------------------------------------------------------

class TestTier1MultiCandidate:
    """
    Tests for _tier1_spatial k=min(10,n) nearest-neighbor expansion and
    hospital-category / bed-count tie-breaking.
    """

    def _make_health(self, lat, lon, name):
        import geopandas as gpd
        from shapely.geometry import Point
        return gpd.GeoDataFrame(
            {
                "lifeline_id": ["lid_001"],
                "name": [name],
                "_name_norm": [_normalize_name(name)],
                "_zip5": ["00731"],
                "_state": ["PR"],
                "_city": ["PONCE"],
                "_addr_num": [""],
            },
            geometry=[Point(lon, lat)],
            crs="EPSG:4326",
        )

    def _make_cms(self, lats, lons, names, categories, beds, nums=None):
        n = len(lats)
        if nums is None:
            nums = [f"4000{i:02d}" for i in range(n)]
        return pd.DataFrame({
            "PRVDR_NUM": nums,
            "FAC_NAME": names,
            "_name_norm": [_normalize_name(x) for x in names],
            "_zip5": ["00731"] * n,
            "_state": ["PR"] * n,
            "_addr_num": [""] * n,
            "PRVDR_CTGRY_CD": categories,
            "BED_CNT": pd.array(beds, dtype="Int64"),
            "CRTFD_BED_CNT": pd.array([0] * n, dtype="Int64"),
            "OPRTG_ROOM_CNT": pd.array([0] * n, dtype="Int64"),
            "geocoded_lat": lats,
            "geocoded_lon": lons,
        })

    def test_nearest_candidate_selected(self):
        """T1-01: When two providers are within range, the closer/better-named one wins."""
        health = self._make_health(18.0, -66.6, "San Lucas Hospital")
        cms = self._make_cms(
            lats=[18.0, 18.0002],
            lons=[-66.6, -66.6002],
            names=["San Lucas Hospital", "Unrelated Clinic"],
            categories=["01", "02"],
            beds=[100, 50],
        )
        results, _ = _tier1_spatial(health, cms, 500.0, 0.30, ["BED_CNT"])
        assert len(results) == 1
        assert results[0]["cms_provider_num"] == "400000"  # San Lucas (exact match)

    def test_hospital_category_tiebreaker(self):
        """T1-02: Two candidates at identical distance and same name; PRVDR_CTGRY_CD='01' wins."""
        health = self._make_health(18.0, -66.6, "Regional Medical Center")
        # Both CMS providers at exactly the same coords AND identical names → same name score
        cms = self._make_cms(
            lats=[18.0, 18.0],
            lons=[-66.6, -66.6],
            names=["Regional Medical Center", "Regional Medical Center"],
            categories=["02", "01"],   # second is the general hospital
            beds=[100, 100],
            nums=["400001", "400002"],
        )
        results, _ = _tier1_spatial(health, cms, 500.0, 0.30, ["BED_CNT"])
        assert len(results) == 1
        # Category "01" provider (400002) should win the tie
        assert results[0]["cms_provider_num"] == "400002"

    def test_bed_count_tiebreaker(self):
        """T1-03: Same name score, same category; higher BED_CNT wins."""
        health = self._make_health(18.0, -66.6, "Regional Medical Center")
        cms = self._make_cms(
            lats=[18.0, 18.0],
            lons=[-66.6, -66.6],
            names=["Regional Medical Center", "Regional Medical Center"],
            categories=["01", "01"],
            beds=[50, 200],   # second has more beds
            nums=["400001", "400002"],
        )
        results, _ = _tier1_spatial(health, cms, 500.0, 0.30, ["BED_CNT"])
        assert len(results) == 1
        assert results[0]["cms_provider_num"] == "400002"

    def test_below_threshold_excluded(self):
        """T1-04: Provider is within distance but name score < threshold → no match."""
        health = self._make_health(18.0, -66.6, "San Lucas Hospital")
        cms = self._make_cms(
            lats=[18.0],
            lons=[-66.6],
            names=["Completely Different Name"],
            categories=["01"],
            beds=[100],
        )
        results, _ = _tier1_spatial(health, cms, 500.0, 0.90, ["BED_CNT"])
        assert results == []

    def test_outside_distance_excluded(self):
        """T1-05: Provider is outside radius → no match."""
        health = self._make_health(18.0, -66.6, "San Lucas Hospital")
        cms = self._make_cms(
            lats=[18.5],    # ~55 km away
            lons=[-66.6],
            names=["San Lucas Hospital"],
            categories=["01"],
            beds=[100],
        )
        results, _ = _tier1_spatial(health, cms, 200.0, 0.30, ["BED_CNT"])
        assert results == []

    def test_already_matched_poi_skipped(self):
        """T1-06: lifeline_id in already_matched → Tier 1 skips that POI."""
        health = self._make_health(18.0, -66.6, "San Lucas Hospital")
        cms = self._make_cms(
            lats=[18.0],
            lons=[-66.6],
            names=["San Lucas Hospital"],
            categories=["01"],
            beds=[100],
        )
        results, _ = _tier1_spatial(health, cms, 500.0, 0.30, ["BED_CNT"],
                                    already_matched={"lid_001"})
        assert results == []

    def test_match_method_recorded(self):
        """T1-07: Matched result has cms_match_method='spatial'."""
        health = self._make_health(18.0, -66.6, "San Lucas Hospital")
        cms = self._make_cms(
            lats=[18.0],
            lons=[-66.6],
            names=["San Lucas Hospital"],
            categories=["01"],
            beds=[100],
        )
        results, _ = _tier1_spatial(health, cms, 500.0, 0.30, ["BED_CNT"])
        assert len(results) == 1
        assert results[0]["cms_match_method"] == "spatial"


# ---------------------------------------------------------------------------
# Section 10 — Tier 5: Census geocode + spatial
# ---------------------------------------------------------------------------

class TestTier5CensusSpatial:
    """
    Tests for _tier5_census_spatial using a monkeypatched _geocode_cms_census
    to avoid real Census API calls.
    """

    def _make_health(self, lat, lon, name, lid="lid_001"):
        import geopandas as gpd
        from shapely.geometry import Point
        return gpd.GeoDataFrame(
            {
                "lifeline_id": [lid],
                "name": [name],
                "_name_norm": [_normalize_name(name)],
                "_zip5": ["00731"],
                "_state": ["PR"],
                "_city": ["PONCE"],
                "_addr_num": [""],
            },
            geometry=[Point(lon, lat)],
            crs="EPSG:4326",
        )

    def _make_cms(self, names, nums=None, beds=None, categories=None):
        n = len(names)
        if nums is None:
            nums = [f"5000{i:02d}" for i in range(n)]
        if beds is None:
            beds = [100] * n
        if categories is None:
            categories = ["01"] * n
        return pd.DataFrame({
            "PRVDR_NUM": nums,
            "FAC_NAME": names,
            "_name_norm": [_normalize_name(x) for x in names],
            "_zip5": ["00731"] * n,
            "_state": ["PR"] * n,
            "_addr_num": [""] * n,
            "PRVDR_CTGRY_CD": categories,
            "BED_CNT": pd.array(beds, dtype="Int64"),
            "CRTFD_BED_CNT": pd.array([0] * n, dtype="Int64"),
            "OPRTG_ROOM_CNT": pd.array([0] * n, dtype="Int64"),
            # No geocoded_lat/lon → makes them candidates for Tier 5
            "geocoded_lat": [float("nan")] * n,
            "geocoded_lon": [float("nan")] * n,
        })

    def test_basic_spatial_match(self, monkeypatch):
        """T5-01: Geocoded CMS provider within distance + name threshold → match."""
        import lib.cms_health_enrich as mod

        health = self._make_health(18.0, -66.6, "San Lucas Hospital")
        cms = self._make_cms(["San Lucas Hospital"], nums=["500001"])

        # Patch geocoder to return (lon, lat) for row 0
        monkeypatch.setattr(mod, "_geocode_cms_census",
                            lambda rows, **kw: {0: (-66.6, 18.0)})

        results = _tier5_census_spatial(health, cms, ["BED_CNT"], set(),
                                        census_distance_m=500.0,
                                        census_name_threshold=0.50)
        assert len(results) == 1
        assert results[0]["cms_provider_num"] == "500001"

    def test_method_label(self, monkeypatch):
        """T5-02: Match method is 'census_spatial'."""
        import lib.cms_health_enrich as mod

        health = self._make_health(18.0, -66.6, "San Lucas Hospital")
        cms = self._make_cms(["San Lucas Hospital"], nums=["500001"])

        monkeypatch.setattr(mod, "_geocode_cms_census",
                            lambda rows, **kw: {0: (-66.6, 18.0)})

        results = _tier5_census_spatial(health, cms, ["BED_CNT"], set(),
                                        census_distance_m=500.0,
                                        census_name_threshold=0.50)
        assert results[0]["cms_match_method"] == "census_spatial"

    def test_name_below_threshold_excluded(self, monkeypatch):
        """T5-03: Provider is within distance but name score < threshold → no match."""
        import lib.cms_health_enrich as mod

        health = self._make_health(18.0, -66.6, "San Lucas Hospital")
        cms = self._make_cms(["Completely Different Name"], nums=["500001"])

        monkeypatch.setattr(mod, "_geocode_cms_census",
                            lambda rows, **kw: {0: (-66.6, 18.0)})

        results = _tier5_census_spatial(health, cms, ["BED_CNT"], set(),
                                        census_distance_m=500.0,
                                        census_name_threshold=0.90)
        assert results == []

    def test_outside_distance_excluded(self, monkeypatch):
        """T5-04: Provider geocoded far away → no match even with good name."""
        import lib.cms_health_enrich as mod

        health = self._make_health(18.0, -66.6, "San Lucas Hospital")
        cms = self._make_cms(["San Lucas Hospital"], nums=["500001"])

        # Return a location ~100 km away
        monkeypatch.setattr(mod, "_geocode_cms_census",
                            lambda rows, **kw: {0: (-66.6, 19.0)})

        results = _tier5_census_spatial(health, cms, ["BED_CNT"], set(),
                                        census_distance_m=200.0,
                                        census_name_threshold=0.50)
        assert results == []

    def test_already_matched_poi_skipped(self, monkeypatch):
        """T5-05: lifeline_id already matched → Tier 5 skips it."""
        import lib.cms_health_enrich as mod

        health = self._make_health(18.0, -66.6, "San Lucas Hospital")
        cms = self._make_cms(["San Lucas Hospital"], nums=["500001"])

        monkeypatch.setattr(mod, "_geocode_cms_census",
                            lambda rows, **kw: {0: (-66.6, 18.0)})

        results = _tier5_census_spatial(health, cms, ["BED_CNT"],
                                        already_matched={"lid_001"},
                                        census_distance_m=500.0,
                                        census_name_threshold=0.50)
        assert results == []

    def test_cms_already_matched_skipped(self, monkeypatch):
        """T5-06: PRVDR_NUM already matched → Tier 5 skips that CMS provider."""
        import lib.cms_health_enrich as mod

        health = self._make_health(18.0, -66.6, "San Lucas Hospital")
        cms = self._make_cms(["San Lucas Hospital"], nums=["500001"])

        monkeypatch.setattr(mod, "_geocode_cms_census",
                            lambda rows, **kw: {0: (-66.6, 18.0)})

        results = _tier5_census_spatial(health, cms, ["BED_CNT"], set(),
                                        census_distance_m=500.0,
                                        census_name_threshold=0.50,
                                        cms_already_matched={"500001"})
        assert results == []

    def test_geocoder_returns_empty(self, monkeypatch):
        """T5-07: Census API returns no hits → gracefully returns []."""
        import lib.cms_health_enrich as mod

        health = self._make_health(18.0, -66.6, "San Lucas Hospital")
        cms = self._make_cms(["San Lucas Hospital"], nums=["500001"])

        monkeypatch.setattr(mod, "_geocode_cms_census",
                            lambda rows, **kw: {})

        results = _tier5_census_spatial(health, cms, ["BED_CNT"], set(),
                                        census_distance_m=500.0,
                                        census_name_threshold=0.50)
        assert results == []

    def test_hospital_category_tiebreaker(self, monkeypatch):
        """T5-08: Two CMS providers at same geocoded coords; PRVDR_CTGRY_CD='01' wins."""
        import lib.cms_health_enrich as mod

        health = self._make_health(18.0, -66.6, "Regional Medical Center")
        cms = self._make_cms(
            ["Regional Medical Center Children", "Regional Medical Center"],
            nums=["500001", "500002"],
            categories=["02", "01"],
            beds=[100, 100],
        )

        # Both geocode to same point
        monkeypatch.setattr(mod, "_geocode_cms_census",
                            lambda rows, **kw: {0: (-66.6, 18.0), 1: (-66.6, 18.0)})

        results = _tier5_census_spatial(health, cms, ["BED_CNT"], set(),
                                        census_distance_m=500.0,
                                        census_name_threshold=0.30)
        assert len(results) == 1
        assert results[0]["cms_provider_num"] == "500002"  # general hospital
