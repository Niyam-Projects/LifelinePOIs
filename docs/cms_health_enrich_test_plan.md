# CMS Health Enrichment — Test Plan

**File under test:** `lib/cms_health_enrich.py`  
**Test file to create:** `tests/test_cms_health_enrich.py`  
**Framework:** pytest (already in `pyproject.toml` dev dependencies)  
**Run command:** `uv run pytest tests/ -v`

---

## Design Principles

1. **No real data.** All tests use in-memory DataFrames constructed inline. No parquet files, no filesystem access, no network calls. Every test is deterministic and fast.
2. **Unit-first.** Each public and private function has its own test class. Integration tests that chain tiers together come last.
3. **Real-world grounding.** Test cases are drawn directly from confirmed PR hospital matching failures and successes discovered during development — these are regression anchors, not arbitrary examples.
4. **One assertion per concern.** Tests are small and clearly named so failures pinpoint the broken behaviour immediately.
5. **Balanced coverage.** For each match tier, we test: a happy-path match, a below-threshold rejection, an empty-input short-circuit, and cross-tier deduplication via `cms_already_matched` / `already_matched`.

---

## Test Module Structure

```
tests/
└── test_cms_health_enrich.py   # all tests in one file, grouped by class
```

One file is sufficient — the module is focused. If it grows beyond ~500 lines, split by function group.

---

## Section 1 — `_normalize_name`

**Class:** `TestNormalizeName`

| ID | Test name | Input | Expected output | Rationale |
|----|-----------|-------|-----------------|-----------|
| N-01 | `test_accent_stripping` | `"Bayamón"` | `"bayamon"` | NFKD → ASCII; `ó` must not become `"bayam n"` (old bug) |
| N-02 | `test_accent_multiple` | `"Clínica Ángeles"` | `"clinica angeles"` | Multiple diacritics in one string |
| N-03 | `test_spanish_stopwords_de` | `"Hospital de la Montaña"` | `"montana"` | Removes `de`, `la`; strips leading `hospital` |
| N-04 | `test_spanish_stopwords_del` | `"Centro del Norte"` | `"centro norte"` | Removes `del` |
| N-05 | `test_spanish_stopwords_el_los_las` | `"El Centro de los Niños las Américas"` | `"centro ninos americas"` | Removes `el`, `de`, `los`, `las` |
| N-06 | `test_trailing_inc` | `"DOCTORS CENTER HOSPITAL BAYAMON INC"` | `"doctors center bayamon"` | Strips `inc`; strips leading `hospital` |
| N-07 | `test_abbreviation_ctr` | `"Medical CTR Ponce"` | `"medical center ponce"` | Expands `ctr` → `center` |
| N-08 | `test_abbreviation_med` | `"MED Center"` | `"medical center"` | Expands `med` → `medical` |
| N-09 | `test_hospital_prefix_stripped` | `"Hospital San Lucas"` | `"san lucas"` | `hospital` at start removed |
| N-10 | `test_hospital_suffix_stripped` | `"San Lucas Hospital"` | `"san lucas"` | `hospital` at end removed |
| N-11 | `test_hospital_middle_kept` | `"Regional Hospital Center"` | `"regional center"` | `hospital` only stripped from start/end, kept in middle… wait, it IS in middle so stays, but then `hospital` prefix/suffix not triggered |
| N-12 | `test_empty_string` | `""` | `""` | Guard: empty input |
| N-13 | `test_none_input` | `None` | `""` | Guard: None input |
| N-14 | `test_non_string` | `42` | `""` | Guard: non-string input |
| N-15 | `test_menonita_match` | `"Hospital General Menonita de Caguas"` and `"HOSPITAL MENONITA CAGUAS INC"` both normalized | Same token sets overlap at `token_set_ratio >= 0.75` | Key PR regression case |
| N-16 | `test_doctors_center_exact` | `"Doctors' Center Hospital Bayamón"` and `"DOCTORS CENTER HOSPITAL BAYAMON INC"` | Both normalize to `"doctors center bayamon"` | Key PR regression case — should be identical after normalize |

---

## Section 2 — `load_cms_providers` (addr_num extraction)

The full `load_cms_providers` requires a real parquet file. Test only the `_addr_num` extraction logic via a DataFrame constructed to mimic its output structure. We can do this by calling the function with a minimal fixture file written to a temp directory, **or** by extracting the regex logic into a tiny helper. The simpler approach is to create a tiny temp parquet in `tmp_path` and verify the `_addr_num` column.

**Class:** `TestLoadCmsAddrNum`  
*(Uses `pytest` `tmp_path` fixture to write a minimal parquet)*

| ID | Test name | `ST_ADR` value | Expected `_addr_num` | Rationale |
|----|-----------|----------------|----------------------|-----------|
| L-01 | `test_leading_digits` | `"917 AVE TITO CASTRO"` | `"917"` | Standard US-style house number |
| L-02 | `test_leading_digits_long` | `"2435  BOULEVARD LUIS A FERRE"` | `"2435"` | Two-space gap; still leading digit |
| L-03 | `test_carretera` | `"CARRETERA 135 KM 64.2"` | `"135"` | Highway address — CARRETERA keyword |
| L-04 | `test_carr_abbrev` | `"CARR. 2 KM 3.5"` | `"2"` | Abbreviated CARR. with period |
| L-05 | `test_carr_no_period` | `"CARR 52 BO JAGUEY"` | `"52"` | CARR without period |
| L-06 | `test_pr_dash` | `"PR-135 KM 64.2"` | `"135"` | Puerto Rico highway reference |
| L-07 | `test_po_box_no_num` | `"PO BOX 1234"` | `""` | PO Box should produce empty (no leading digits that match the 2–4 digit highway pattern) |
| L-08 | `test_zip5_from_zip_cd` | `ZIP_CD = "00731-1234"` | `_zip5 = "00731"` | ZIP5 truncation |
| L-09 | `test_prvdr_num_is_string` | `PRVDR_NUM` column contains integer `400001` | `_addr_num` type check not needed, but `PRVDR_NUM` column dtype must be `object` | PRVDR_NUM normalization prevents merge dtype mismatch |

---

## Section 3 — `_build_result_row`

**Class:** `TestBuildResultRow`

| ID | Test name | Notes |
|----|-----------|-------|
| B-01 | `test_core_columns_present` | Output dict contains all required keys: `lifeline_id`, `cms_provider_num`, `cms_match_score`, `cms_match_method`, `cms_match_distance_m` |
| B-02 | `test_compat_bed_cnt` | `BED_CNT=120` in cms row → `cms_bed_cnt=120` |
| B-03 | `test_compat_bed_cnt_na` | `BED_CNT=NaN` → `cms_bed_cnt=0` (backward-compat fallback) |
| B-04 | `test_extra_cnt_column` | Extra `STAFF_CNT=50` → `cms_staff_cnt=50` |
| B-05 | `test_extra_cnt_na` | Extra `STAFF_CNT=NaN` → `cms_staff_cnt=pd.NA` (not zero — only compat cols default to 0) |
| B-06 | `test_score_rounded` | `score=0.88765` → `cms_match_score=0.8877` (4dp) |
| B-07 | `test_distance_none` | `distance_m=None` → `cms_match_distance_m` is `None` |
| B-08 | `test_provider_category` | `PRVDR_CTGRY_CD="01"` → `cms_provider_category="01"` |
| B-09 | `test_provider_subtype` | `PRVDR_CTGRY_SBTYP_CD="A"` → `cms_provider_subtype="A"` |

---

## Section 4 — `_tier2_zip_fuzzy`

Build minimal DataFrames in each test. Both DataFrames need the columns the function reads: `lifeline_id`, `_name_norm`, `_zip5`, `_state`, `_city` (health); `_name_norm`, `_zip5`, `_state`, `_city_norm`, `PRVDR_NUM`, any `_CNT` cols (cms).

**Class:** `TestTier2ZipFuzzy`

| ID | Test name | Scenario |
|----|-----------|----------|
| T2-01 | `test_state_zip_match` | Health and CMS share state+ZIP, names are similar (>0.80) → match returned |
| T2-02 | `test_name_below_threshold` | Same ZIP/state but `token_sort_ratio < 0.80` → no match |
| T2-03 | `test_zip_only_fallback` | Health `_state=""`, CMS and health share ZIP only → match via Tier 2a' |
| T2-04 | `test_already_matched_skipped` | POI lifeline_id is in `already_matched` set → excluded, no result |
| T2-05 | `test_cms_already_matched_skipped` | CMS PRVDR_NUM is in `cms_already_matched` → that CMS not used |
| T2-06 | `test_empty_health` | Zero-row health DataFrame → returns `[]` |
| T2-07 | `test_empty_cms` | Zero-row CMS DataFrame → returns `[]` |
| T2-08 | `test_best_score_wins` | Two CMS candidates for same POI, different scores → highest score selected |
| T2-09 | `test_method_recorded` | Result dict has `cms_match_method == "zip_fuzzy"` |

---

## Section 5 — `_tier3_addr_zip`

**Class:** `TestTier3AddrZip`

| ID | Test name | Scenario |
|----|-----------|----------|
| T3-01 | `test_addr_zip_exact_match` | POI `_addr_num="917"`, `_zip5="00731"` matches CMS same values, similar name → match |
| T3-02 | `test_san_lucas_case` | POI name `"hospital san lucas"`, addr `"917"`, zip `"00731"` vs CMS `"hospital episcopal san lucas ii"`, addr `"917"`, zip `"00731"` → match (token_sort_ratio is reasonable at low threshold 0.50) |
| T3-03 | `test_dr_pila_case` | POI `"hospital metropolitano pila"`, addr `"2435"`, zip `"00717"` vs CMS `"hospital metropolitano dr pila"`, addr `"2435"`, zip `"00717"` → match |
| T3-04 | `test_no_addr_num_on_poi` | POI `_addr_num=""` → not included in candidates, no match |
| T3-05 | `test_no_addr_num_on_cms` | CMS `_addr_num=""` → not included in candidates, no match |
| T3-06 | `test_wrong_addr_num` | POI `_addr_num="100"`, CMS `_addr_num="200"` same ZIP → no match (inner join misses) |
| T3-07 | `test_name_sanity_below_threshold` | addr+ZIP match but names score 0.20 (completely different) → rejected at `addr_name_threshold=0.50` |
| T3-08 | `test_already_matched_skipped` | lifeline_id in `already_matched` → no result |
| T3-09 | `test_cms_already_matched_skipped` | PRVDR_NUM in `cms_already_matched` → not used |
| T3-10 | `test_method_recorded` | Result has `cms_match_method == "addr_zip"` |
| T3-11 | `test_carretera_cms_match` | CMS `_addr_num="135"` (from `CARRETERA 135 KM 64.2`) matches POI `_addr_num="135"` (from `PR-135` display_name) → match |

---

## Section 6 — `_tier4_name_zip`

**Class:** `TestTier4NameZip`

| ID | Test name | Scenario |
|----|-----------|----------|
| T4-01 | `test_menonita_caguas` | `"general menonita caguas"` vs `"menonita caguas"` (normalized), same ZIP → `token_set_ratio=1.0` → match |
| T4-02 | `test_doctors_center` | `"doctors center bayamon"` vs `"doctors center bayamon"` → identical after normalize → score 1.0 → match |
| T4-03 | `test_score_below_threshold` | `token_set_ratio < 0.75` → no match |
| T4-04 | `test_zip_required_on_health` | POI `_zip5=""` → excluded, no match |
| T4-05 | `test_zip_required_on_cms` | CMS `_zip5=""` → filtered out before merge, no match |
| T4-06 | `test_zip_mismatch` | Different ZIPs → inner join produces no candidates |
| T4-07 | `test_already_matched_skipped` | lifeline_id in `already_matched` → excluded |
| T4-08 | `test_cms_already_matched_skipped` | PRVDR_NUM in `cms_already_matched` → excluded |
| T4-09 | `test_best_score_wins` | Two CMS candidates, same ZIP, different name scores → best wins |
| T4-10 | `test_method_recorded` | Result has `cms_match_method == "name_zip"` |
| T4-11 | `test_token_set_better_than_sort` | Pair that scores `token_sort_ratio ≈ 0.70` but `token_set_ratio ≈ 1.00` — verifies Tier 4 catches what Tier 2 misses at the same threshold |
| T4-12 | `test_name_empty_poi_skipped` | POI `_name_norm=""` → excluded from candidates |

---

## Section 7 — Cross-tier deduplication (integration)

These tests exercise that tiers don't double-match. They call each tier function in sequence, passing the accumulated `already_matched` and `cms_already_matched` sets.

**Class:** `TestCrossTierDeduplication`

| ID | Test name | Scenario |
|----|-----------|----------|
| D-01 | `test_tier2_doesnt_reuse_tier1_poi` | POI matched in Tier 1 (lifeline_id in `already_matched`) → Tier 2 skips it |
| D-02 | `test_tier3_doesnt_reuse_tier2_cms` | CMS PRVDR_NUM claimed by Tier 2 → Tier 3 can't claim it |
| D-03 | `test_tier4_doesnt_reuse_tier3_cms` | CMS PRVDR_NUM claimed by Tier 3 → Tier 4 can't claim it |
| D-04 | `test_tier4_doesnt_reuse_tier3_poi` | POI matched in Tier 3 → Tier 4 skips it |
| D-05 | `test_same_cms_can_match_different_pois_within_tier` | Within a single tier, two distinct POIs may still match the same CMS (cross-POI dedup is handled later in `build_attr_health_cms`) |

---

## Section 8 — Edge cases and guard conditions

**Class:** `TestEdgeCases`

| ID | Test name | Scenario |
|----|-----------|----------|
| E-01 | `test_normalize_pr_highway_address` | `_normalize_name("PR-135 KM 64")` → does not crash; output is reasonable |
| E-02 | `test_all_tiers_empty_returns_empty_list` | All functions return `[]` when called with zero-row DataFrames |
| E-03 | `test_build_result_row_missing_compat_columns` | `BED_CNT` not present in cms row → `cms_bed_cnt=0` (no KeyError) |
| E-04 | `test_detect_cnt_columns` | DataFrame with `["BED_CNT", "STAFF_CNT", "FAC_NAME"]` → returns `["BED_CNT", "STAFF_CNT"]` |
| E-05 | `test_detect_cnt_case_insensitive` | Column `"bed_cnt"` (lowercase) → still detected |
| E-06 | `test_tier2_no_zip_on_poi` | All health POIs have `_zip5=""` (len < 5) → no Tier 2a candidates, may fall to 2c |
| E-07 | `test_tier4_no_zip_on_all_cms` | All CMS `_zip5` empty → `cms_m` empty after filter → returns `[]` |

---

## Fixtures

Define these shared fixtures at module level using `pytest.fixture`:

```python
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
        "display_name": ["Hospital San Lucas, 917 Avenida Tito Castro, Ponce, PR 00731",
                         "El Hospital Metropolitano Dr. Pila, 2435 Boulevard Luis A. Ferré, Ponce, PR 00717"],
        "geometry": [None, None],
    })
```

For tests that don't need geometry, `geometry=None` is fine since Tier 2/3/4 don't use it.

---

## Implementation Notes

### Running tests

```bash
# Install dev extras first (once):
uv pip install -e ".[dev]"

# Run all tests:
uv run pytest tests/ -v

# Run a single class:
uv run pytest tests/test_cms_health_enrich.py::TestNormalizeName -v

# Run with coverage:
uv run pytest tests/ --cov=lib.cms_health_enrich --cov-report=term-missing
```

### What to import

```python
import pandas as pd
import pytest
from lib.cms_health_enrich import (
    _normalize_name,
    _detect_cnt_columns,
    _build_result_row,
    _tier2_zip_fuzzy,
    _tier3_addr_zip,
    _tier4_name_zip,
)
```

`load_cms_providers` integration tests (Section 2) additionally need `tmp_path` and `pyarrow`.

### Parametrize normalize tests

`TestNormalizeName` tests N-01 through N-14 are well-suited to `@pytest.mark.parametrize`:

```python
@pytest.mark.parametrize("raw, expected", [
    ("Bayamón", "bayamon"),
    ("Clínica Ángeles", "clinica angeles"),
    ("Hospital San Lucas", "san lucas"),
    ("Hospital de la Montaña", "montana"),
    ...
])
def test_normalize(raw, expected):
    assert _normalize_name(raw) == expected
```

### PR regression cases

N-15, N-16, T3-02, T3-03, T4-01, T4-02 are **regression anchors** — they represent actual hospitals that failed to match in production and were fixed. These must never be removed. Add a `# REGRESSION: <hospital name>` comment above each so future contributors know the history.

---

## Acceptance Criteria

- [ ] All tests pass: `uv run pytest tests/ -v` exits 0
- [ ] No test imports or reads from `E:/lifelinepois/data/` (no real data dependency)
- [ ] Each function in `cms_health_enrich.py` has at least one test
- [ ] All 6 known PR regression cases are covered (N-15, N-16, T3-02, T3-03, T4-01, T4-02)
- [ ] Cross-tier deduplication tested for all four tier boundaries (D-01 through D-04)
- [ ] `marimo check flows/debug_health_matching.py` still exits 0 after test file is added
