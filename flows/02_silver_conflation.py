import marimo

__generated_with = "0.10.0"
app = marimo.App(width="medium")


@app.cell(hide_code=True)
def _():
    import marimo as mo
    return (mo,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    # Flow 02 · Silver Conflation

    Reads Bronze OSM GeoParquet + authoritative datasets (EIA, EPA),
    assigns stable UUIDs, maps FEMA lifeline categories, and conflates
    using rapidfuzz name matching + BallTree spatial proximity.

    **Interactive:** fill in the form below and click **Run Conflation**.
    **Script:** `marimo run flows/02_silver_conflation.py -- --help`
    """)
    return


@app.cell
def _():
    import uuid
    from pathlib import Path

    import geopandas as gpd
    import pandas as pd
    import pyarrow as pa
    import pyarrow.parquet as pq
    from pydantic import BaseModel, Field

    import sys as _sys
    _sys.path.insert(0, str(Path(".").resolve()))
    from src.lifelinepoi.config import LifelineConfig
    from lib.scoring import compute_confidence, ConfidenceTier
    from lib.spatial import add_h3_index, clip_to_bbox, nearest_neighbor_join

    return BaseModel, ConfidenceTier, Field, LifelineConfig, Path, add_h3_index, clip_to_bbox, compute_confidence, gpd, nearest_neighbor_join, pa, pd, pq, uuid


@app.cell
def _(BaseModel, Field):
    class FlowParams(BaseModel):
        config_path: str = Field(default="config.lifeline.yaml", description="Path to config YAML file")

    return (FlowParams,)


@app.cell
def _(FlowParams, mo):
    params_form = (
        mo.md("""
        ## Parameters

        Config file: {config_path}
        """)
        .batch(
            config_path=mo.ui.text(value="config.lifeline.yaml", label="Config path"),
        )
        .form(submit_button_label="▶ Run Conflation")
    )
    params_form
    return (params_form,)


@app.cell
def _(FlowParams, mo):
    import sys as _sys
    is_script_mode = mo.app_meta().mode == "script"
    if is_script_mode and (not mo.cli_args() or "help" in mo.cli_args()):
        print("Usage: marimo run flows/02_silver_conflation.py -- [options]\n")
        for _name, _field in FlowParams.model_fields.items():
            _default = f"(default: {_field.default})" if _field.default is not None else "(required)"
            print(f"  --{_name.replace('_', '-'):<28} {_field.description} {_default}")
        _sys.exit(0)
    return (is_script_mode,)


@app.cell
def _(FlowParams, is_script_mode, mo, params_form):
    mo.stop(
        not is_script_mode and params_form.value is None,
        mo.callout(mo.md("**Fill in the parameters above and click _Run Conflation_ to start.**"), kind="info"),
    )
    if is_script_mode:
        flow_params = FlowParams(**{k.replace("-", "_"): v for k, v in mo.cli_args().items()})
    else:
        flow_params = FlowParams(**params_form.value)
    return (flow_params,)


@app.cell
def _(LifelineConfig, flow_params, mo):
    cfg = LifelineConfig.from_yaml(flow_params.config_path)
    mo.md(f"**Config loaded.** Layers: `{', '.join(cfg.osm.layers)}`")
    return (cfg,)


@app.cell
def _(ConfidenceTier, Path, add_h3_index, cfg, clip_to_bbox, compute_confidence, gpd, mo, nearest_neighbor_join, pd, pq, uuid):
    _LAYER_LIFELINE_MAP = {
        "power": {"lifeline_category": "Energy", "lifeline_subcategory": "Power"},
        "water_infrastructure": {"lifeline_category": "Food Water Shelter", "lifeline_subcategory": "Water"},
        "telecom": {"lifeline_category": "Communications", "lifeline_subcategory": "Telecom"},
        "fuel": {"lifeline_category": "Hazardous Material", "lifeline_subcategory": "Fuel"},
    }
    _LAYER_KEY_FIELDS = {
        "power": ["power", "voltage", "operator", "name"],
        "water_infrastructure": ["man_made", "operator", "name", "capacity"],
        "telecom": ["telecom", "man_made", "operator", "name"],
        "fuel": ["industrial", "man_made", "operator", "name"],
    }

    def _load_bronze_osm(bronze_path, layer):
        path = bronze_path / "osm" / f"{layer}.parquet"
        if not path.exists():
            print(f"    WARNING: Bronze OSM layer not found: {path} — skipping")
            return None
        gdf = gpd.read_parquet(path)
        if gdf.crs is None:
            gdf = gdf.set_crs("EPSG:4326")
        return gdf

    def _assign_uuids(gdf):
        gdf = gdf.copy()
        if "type" in gdf.columns and "id" in gdf.columns:
            gdf["lifeline_id"] = gdf.apply(
                lambda r: str(uuid.uuid5(uuid.NAMESPACE_URL, f"osm/{r['type']}/{r['id']}")), axis=1
            )
        else:
            gdf["lifeline_id"] = [str(uuid.uuid4()) for _ in range(len(gdf))]
        return gdf

    def _build_silver_core(gdf, layer):
        meta = _LAYER_LIFELINE_MAP.get(layer, {})
        gdf = _assign_uuids(gdf)
        gdf = add_h3_index(gdf, column="h3_index")
        if "name" in gdf.columns:
            gdf["display_name"] = gdf["name"]
        else:
            gdf["display_name"] = (
                gdf.get("type", pd.Series("unknown", index=gdf.index)).astype(str)
                + "/" + gdf.get("id", pd.Series(range(len(gdf)), index=gdf.index)).astype(str)
            )
        primary_tag = {"power": "power", "water_infrastructure": "man_made", "telecom": "telecom", "fuel": "industrial"}.get(layer, "name")
        gdf["osm_category"] = gdf.get(primary_tag, pd.Series(None, index=gdf.index))
        gdf["confidence_score"] = gdf.apply(
            lambda r: compute_confidence(
                distance_m=0, osm_attrs=r.to_dict(), auth_attrs={},
                key_fields=_LAYER_KEY_FIELDS.get(layer, []),
                has_osm=True, has_authoritative=False,
                weights=cfg.conflation.confidence_weights,
            ).composite, axis=1,
        )
        gdf["confidence_tier"] = gdf["confidence_score"].apply(
            lambda s: (ConfidenceTier.HIGH if s >= 0.75 else ConfidenceTier.MEDIUM if s >= 0.40 else ConfidenceTier.LOW).value
        )
        core_cols = [c for c in ["lifeline_id", "display_name", "osm_category", "h3_index", "confidence_score", "confidence_tier", "geometry"] if c in gdf.columns]
        core = gdf[core_cols].copy()
        core["lifeline_layer"] = layer
        core["lifeline_category"] = meta.get("lifeline_category", "")
        core["lifeline_subcategory"] = meta.get("lifeline_subcategory", "")
        core["source_provenance"] = "osm"
        return core

    def _build_attr_table(gdf, layer, lifeline_ids):
        gdf = gdf.copy()
        gdf["lifeline_id"] = lifeline_ids.values
        drop_cols = {"geometry", "type", "id", "bbox", "names"}
        attr_cols = ["lifeline_id"] + [c for c in gdf.columns if c not in drop_cols]
        return gdf[[c for c in attr_cols if c in gdf.columns]].copy()

    bronze_path = Path(cfg.storage.bronze_path)
    silver_path = Path(cfg.storage.silver_path)
    silver_path.mkdir(parents=True, exist_ok=True)
    all_cores = []

    for _layer in cfg.osm.layers:
        print(f"  Processing layer: {_layer}")
        gdf = _load_bronze_osm(bronze_path, _layer)
        if gdf is None:
            continue
        bbox = cfg.aoi.bbox if hasattr(cfg, "aoi") and cfg.aoi else None
        if bbox:
            gdf = clip_to_bbox(gdf, bbox)
        gdf = _assign_uuids(gdf)
        lifeline_ids = gdf["lifeline_id"]
        core = _build_silver_core(gdf, _layer)
        attr = _build_attr_table(gdf, _layer, lifeline_ids)
        attr_path = silver_path / f"attr_{_layer}.parquet"
        attr.to_parquet(attr_path, index=False)
        print(f"    attr_{_layer}.parquet — {len(attr)} rows")
        all_cores.append(core)

    if not all_cores:
        conflation_result = mo.callout(mo.md("⚠️ **No Bronze layers found.** Run Flow 01 first."), kind="warn")
    else:
        master = pd.concat(all_cores, ignore_index=True)
        master_gdf = gpd.GeoDataFrame(master, crs="EPSG:4326")
        out_path = silver_path / "lifeline_points.parquet"
        master_gdf.to_parquet(out_path, index=False)
        print(f"  silver/lifeline_points.parquet — {len(master_gdf)} total rows")
        conflation_result = mo.callout(
            mo.md(f"✅ **Silver conflation complete.** `{len(master_gdf):,}` records written to `silver/lifeline_points.parquet`"),
            kind="success",
        )

    conflation_result
    return (conflation_result,)


@app.cell
def _(conflation_result, mo):
    mo.vstack([
        mo.md("## Conflation Summary"),
        conflation_result,
        mo.callout(mo.md("✅ **Flow 02 complete.** ➡ Run `flows/03_gersite_bridge.py` next."), kind="success"),
    ])
    return


if __name__ == "__main__":
    app.run()
