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
    # Flow 04 · Gold Production

    Joins Silver master + domain attribute tables + GERSite bridge into
    wide analytical GeoParquet tables ready for distribution and tiling.

    **Interactive:** fill in the form below and click **Run Gold Production**.
    **Script:** `marimo run flows/04_gold_production.py -- --help`
    """)
    return


@app.cell
def _():
    from pathlib import Path

    import geopandas as gpd
    import pandas as pd
    from pydantic import BaseModel, Field

    import sys as _sys
    _sys.path.insert(0, str(Path(".").resolve()))
    from src.lifelinepoi.config import LifelineConfig

    return BaseModel, Field, LifelineConfig, Path, gpd, pd


@app.cell
def _(BaseModel, Field):
    class FlowParams(BaseModel):
        config_path: str = Field(default="config.lifeline.yaml", description="Path to config YAML file")
        layer: str = Field(default="", description="Produce a single layer only (empty = all layers)")

    return (FlowParams,)


@app.cell
def _(FlowParams, mo):
    params_form = (
        mo.md("""
        ## Parameters

        Config file: {config_path}

        Single layer filter (blank = all layers): {layer}
        """)
        .batch(
            config_path=mo.ui.text(value="config.lifeline.yaml", label="Config path"),
            layer=mo.ui.text(value="", placeholder="e.g. power", label=""),
        )
        .form(submit_button_label="▶ Run Gold Production")
    )
    params_form
    return (params_form,)


@app.cell
def _(FlowParams, mo):
    import sys as _sys
    is_script_mode = mo.app_meta().mode == "script"
    if is_script_mode and (not mo.cli_args() or "help" in mo.cli_args()):
        print("Usage: marimo run flows/04_gold_production.py -- [options]\n")
        for _name, _field in FlowParams.model_fields.items():
            _default = f"(default: {_field.default})" if _field.default is not None else "(required)"
            print(f"  --{_name.replace('_', '-'):<28} {_field.description} {_default}")
        _sys.exit(0)
    return (is_script_mode,)


@app.cell
def _(FlowParams, is_script_mode, mo, params_form):
    mo.stop(
        not is_script_mode and params_form.value is None,
        mo.callout(mo.md("**Fill in the parameters above and click _Run Gold Production_ to start.**"), kind="info"),
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
def _(Path, cfg, flow_params, gpd, mo, pd):
    def _load_parquet(path):
        if not path.exists():
            return None
        try:
            return gpd.read_parquet(path)
        except Exception:
            return pd.read_parquet(path)

    def _produce_gold_layer(layer, silver_path, gold_path):
        master_path = silver_path / "lifeline_points.parquet"
        if not master_path.exists():
            print(f"    WARNING: silver/lifeline_points.parquet not found — skipping")
            return False
        master = _load_parquet(master_path)
        layer_master = master[master["lifeline_layer"] == layer].copy()
        if len(layer_master) == 0:
            print(f"    WARNING: No records for layer '{layer}' in Silver master — skipping")
            return False
        attr = _load_parquet(silver_path / f"attr_{layer}.parquet")
        bridge = _load_parquet(silver_path / "bridge_poi_building.parquet")
        wide = layer_master.copy()
        if attr is not None and "lifeline_id" in attr.columns:
            attr_no_geom = attr.drop(columns=["geometry"], errors="ignore")
            existing = set(wide.columns) - {"lifeline_id"}
            attr_cols = ["lifeline_id"] + [c for c in attr_no_geom.columns if c not in existing and c != "lifeline_id"]
            wide = wide.merge(attr_no_geom[attr_cols], on="lifeline_id", how="left")
        if bridge is not None and "lifeline_id" in bridge.columns:
            bridge_no_dup = bridge.drop(columns=["geometry"], errors="ignore")
            existing = set(wide.columns) - {"lifeline_id"}
            bridge_cols = ["lifeline_id"] + [c for c in bridge_no_dup.columns if c not in existing and c != "lifeline_id"]
            wide = wide.merge(bridge_no_dup[bridge_cols], on="lifeline_id", how="left")
        gold_path.mkdir(parents=True, exist_ok=True)
        out_path = gold_path / f"wide_{layer}.parquet"
        if isinstance(wide, gpd.GeoDataFrame):
            wide.to_parquet(out_path, index=False)
        else:
            gpd.GeoDataFrame(wide, crs="EPSG:4326").to_parquet(out_path, index=False)
        print(f"    wide_{layer}.parquet — {len(wide):,} rows × {len(wide.columns)} cols")
        return True

    silver_path = Path(cfg.storage.silver_path)
    gold_path = Path(cfg.storage.gold_path)
    layers = [flow_params.layer] if flow_params.layer else cfg.osm.layers
    results = []

    for _layer in layers:
        print(f"  Producing gold layer: {_layer}")
        ok = _produce_gold_layer(_layer, silver_path, gold_path)
        results.append((_layer, ok))

    gold_result = mo.callout(
        mo.md("✅ **Gold production complete.** " + "  ".join(
            f"`{lyr}` ✅" if ok else f"`{lyr}` ⚠️" for lyr, ok in results
        )),
        kind="success",
    )
    gold_result
    return (gold_result,)


@app.cell
def _(gold_result, mo):
    mo.vstack([
        mo.md("## Gold Production Summary"),
        gold_result,
        mo.callout(mo.md("✅ **Flow 04 complete.** ➡ Run `flows/05_generate_tiles.py` next."), kind="success"),
    ])
    return


if __name__ == "__main__":
    app.run()
