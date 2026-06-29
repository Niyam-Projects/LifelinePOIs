"""
Food Gold Layer Production for LifelinePOIs.

Produces a wide GeoParquet gold file specifically for the FEMA
"Food, Hydration, Shelter" lifeline by filtering EPA FRS-derived
silver data based on relevant NAICS and SIC codes.

Schema:
  - Retains relevant columns from the source silver layer (EPA FRS).
  - Adds:
    lifeline_id            - UUID5 based on stable identifier.
    lifeline_key           - "food_commercial_distribution".
    lifeline_component_key - "food".
    fema_lifeline          - struct: {primary, hierarchy, alternates}.
    display_name           - primary human-readable name.
    confidence_score       - 1.0 for matched records.
    confidence_tier        - "HIGH".
    source_provenance      - e.g., "epa_frs".
    food_facility_subtype  - "warehouse_distribution" or "retail_store".
"""
from __future__ import annotations

import re
import uuid
from pathlib import Path

import geopandas as gpd
import pandas as pd

from lib.naics_lifeline_map import make_fema_lifeline_struct


_FOOD_NS = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")

_FOOD_LIFELINE_KEY = "food_commercial_distribution"
_FOOD_COMPONENT_KEY = "food"

# SIC codes treated as food infrastructure (warehousing)
_FOOD_SIC_SET = {"4225"}

# NAICS codes that always indicate a warehouse / distribution facility
_WAREHOUSE_NAICS = {"493120"}

# Name patterns that indicate a distribution or warehouse facility
_WAREHOUSE_NAME_RE = re.compile(
    r"\b(distribution|distribut|warehouse|warehousing|fulfillment|cold[\s-]storage|distr\.?)\b",
    re.IGNORECASE,
)


def _make_uuid5(layer_name: str, id_val: str) -> str:
    return str(uuid.uuid5(_FOOD_NS, f"food/{layer_name}/{id_val}"))


def _split_codes(val: object) -> list[str]:
    """Split a pipe- or comma-delimited code string into individual trimmed strings."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return []
    return [c.strip() for c in re.split(r"[|,]", str(val)) if c.strip()]


def _primary_food_naics(codes_val: object, food_naics_set: set) -> str | None:
    """Return the first food NAICS code found in a (possibly multi-value) field, or None."""
    for c in _split_codes(codes_val):
        if c in food_naics_set:
            return c
    return None


def _has_food_sic(codes_val: object) -> bool:
    return any(c in _FOOD_SIC_SET for c in _split_codes(codes_val))


def _facility_subtype(row: pd.Series, naics_col: str, sic_col: str | None, name_col: str) -> str:
    """Classify the food facility as warehouse_distribution or retail_store."""
    matched_naics = row.get("_matched_naics", None)
    name = str(row.get(name_col, "") or "")

    if matched_naics in _WAREHOUSE_NAICS:
        return "warehouse_distribution"
    if sic_col and _has_food_sic(row.get(sic_col, "")):
        return "warehouse_distribution"
    if _WAREHOUSE_NAME_RE.search(name):
        return "warehouse_distribution"
    return "retail_store"


def produce_food_gold(
    silver_path: Path,
    gold_path: Path,
    naics_mapping_path: Path,
) -> int:
    """
    Full pipeline for food gold layer: load silver EPA FRS ->
    filter by NAICS/SIC codes -> enrich with FEMA taxonomy -> write GeoParquet.

    Returns the number of rows written.
    """
    # 1. Load silver
    silver_file = silver_path / "lifeline_points.parquet"
    if not silver_file.exists():
        raise FileNotFoundError(f"Silver lifeline points not found at {silver_file}")
    try:
        silver = gpd.read_parquet(silver_file)
    except Exception:
        silver = pd.read_parquet(silver_file)

    # 2. Load NAICS food mapping
    if not naics_mapping_path.exists():
        raise FileNotFoundError(f"NAICS food mapping not found at {naics_mapping_path}")
    naics_map = pd.read_csv(naics_mapping_path)
    naics_map = naics_map.rename(columns={"code": "naics"})
    naics_map["naics"] = naics_map["naics"].astype(str)
    food_naics_set = set(naics_map["naics"].tolist())

    # 3. Resolve NAICS and SIC column names (handles naics_codes, naics, etc.)
    all_cols = list(silver.columns)
    naics_col = next(
        (c for c in all_cols if c == "naics_codes"),
        next((c for c in all_cols if "naics" in c.lower()), None),
    )
    sic_col = next(
        (c for c in all_cols if c == "sic_codes"),
        next((c for c in all_cols if "sic" in c.lower()), None),
    )
    if naics_col is None:
        raise ValueError("No NAICS column found in silver layer.")

    # 4. Exclude OSM records (bus_stop, amenity nodes, etc.) -- keep only EPA FRS rows
    food_df = silver.copy()
    if "source_provenance" in food_df.columns:
        food_df = food_df[
            food_df["source_provenance"].str.startswith("epa", na=False)
        ].copy()
    elif "osm_category" in food_df.columns:
        food_df = food_df[food_df["osm_category"].isna()].copy()

    # 5. Match: any food NAICS present in the (possibly multi-value) field, or food SIC
    food_df["_matched_naics"] = food_df[naics_col].apply(
        lambda x: _primary_food_naics(x, food_naics_set)
    )
    naics_mask = food_df["_matched_naics"].notna()
    if sic_col:
        sic_mask = food_df[sic_col].apply(_has_food_sic)
        matched_df = food_df[naics_mask | sic_mask].copy()
    else:
        matched_df = food_df[naics_mask].copy()

    if len(matched_df) == 0:
        print("Warning: No matches found for food NAICS/SIC codes in silver layer.")
        return 0

    # 6. Classify facility subtype
    name_col = next(
        (c for c in ("display_name", "name") if c in matched_df.columns),
        matched_df.columns[0],
    )
    matched_df["food_facility_subtype"] = matched_df.apply(
        lambda r: _facility_subtype(r, naics_col, sic_col, name_col), axis=1
    )

    # 7. Build enrichment columns (replace any inherited silver values)
    id_col = "lifeline_id" if "lifeline_id" in matched_df.columns else None

    def _row_id(idx: int) -> str:
        if id_col and pd.notna(matched_df.at[idx, id_col]):
            return _make_uuid5("wide_food", str(matched_df.at[idx, id_col]))
        return _make_uuid5("wide_food", str(idx))

    lifeline_ids = [_row_id(i) for i in matched_df.index]
    fema_struct = make_fema_lifeline_struct(_FOOD_LIFELINE_KEY)

    enrichment = pd.DataFrame(
        {
            "lifeline_id": lifeline_ids,
            "lifeline_key": _FOOD_LIFELINE_KEY,
            "lifeline_component_key": _FOOD_COMPONENT_KEY,
            "fema_lifeline": [fema_struct] * len(matched_df),
            "display_name": matched_df[name_col].values,
            "confidence_score": 1.0,
            "confidence_tier": "HIGH",
            "source_provenance": matched_df.get("source_provenance", "epa_frs"),
            "food_facility_subtype": matched_df["food_facility_subtype"].values,
        },
        index=matched_df.index,
    )

    # 8. Combine: drop columns enrichment replaces plus CSV / merge artifacts
    cols_to_drop = list(enrichment.columns) + [
        "code_type", "fema_id", "lifeline_component", "lifeline_subcomponent",
        "lifeline_category", "tier", "boost", "naics_sector", "bls_title",
        "naics_sector_x", "naics_sector_y",
        "_matched_naics",
    ] + [c for c in matched_df.columns if c.startswith("tmp_")]

    final_df = pd.concat(
        [enrichment, matched_df.drop(columns=cols_to_drop, errors="ignore")],
        axis=1,
    )

    # 9. Write as GeoParquet
    if "geometry" in final_df.columns:
        final_gdf = gpd.GeoDataFrame(final_df, geometry=final_df["geometry"], crs="EPSG:4326")
    else:
        final_gdf = gpd.GeoDataFrame(
            final_df,
            geometry=gpd.GeoSeries([None] * len(final_df), crs="EPSG:4326"),
        )

    Path(gold_path).mkdir(parents=True, exist_ok=True)
    out_path = Path(gold_path) / "wide_food.parquet"
    final_gdf.to_parquet(out_path, index=False)

    return len(final_gdf)
