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
    # Flow 03 · GERSite Bridge

    Spatially joins Silver lifeline points against GERSite building footprints
    to associate infrastructure POIs with physical building structures.

    **Interactive:** fill in the form below and click **Run Bridge**.
    **Script:** `marimo run flows/03_gersite_bridge.py -- --help`
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
    from lib.spatial import nearest_neighbor_join

    return BaseModel, Field, LifelineConfig, Path, gpd, nearest_neighbor_join, pd


@app.cell
def _(BaseModel, Field):
    class FlowParams(BaseModel):
        config_path: str = Field(default="config.lifeline.yaml", description="Path to config YAML file")
        gersite_path: str = Field(default="data/gersite/buildings.parquet", description="Path to GERSite buildings GeoParquet or GeoJSON")
        max_distance_m: float = Field(default=100.0, description="Max join distance in meters")
        skip_missing: bool = Field(default=True, description="Skip with stub table if GERSite data not found")

    return (FlowParams,)


@app.cell
def _(FlowParams, mo):
    params_form = (
        mo.md("""
        ## Parameters

        Config file: {config_path}

        GERSite buildings path: {gersite_path}

        Max join distance (meters): {max_distance_m}

        {skip_missing} Skip if GERSite data not available (create stub table)
        """)
        .batch(
            config_path=mo.ui.text(value="config.lifeline.yaml", label="Config path"),
            gersite_path=mo.ui.text(value="data/gersite/buildings.parquet", label="GERSite path"),
            max_distance_m=mo.ui.slider(10.0, 500.0, value=100.0, step=10.0, label="Max distance (m)"),
            skip_missing=mo.ui.checkbox(value=True, label=""),
        )
        .form(submit_button_label="▶ Run Bridge")
    )
    params_form
    return (params_form,)


@app.cell
def _(FlowParams, mo):
    import sys as _sys
    is_script_mode = mo.app_meta().mode == "script"
    if is_script_mode and (not mo.cli_args() or "help" in mo.cli_args()):
        print("Usage: marimo run flows/03_gersite_bridge.py -- [options]\n")
        for _name, _field in FlowParams.model_fields.items():
            _default = f"(default: {_field.default})" if _field.default is not None else "(required)"
            print(f"  --{_name.replace('_', '-'):<28} {_field.description} {_default}")
        _sys.exit(0)
    return (is_script_mode,)


@app.cell
def _(FlowParams, is_script_mode, mo, params_form):
    mo.stop(
        not is_script_mode and params_form.value is None,
        mo.callout(mo.md("**Fill in the parameters above and click _Run Bridge_ to start.**"), kind="info"),
    )
    if is_script_mode:
        flow_params = FlowParams(**{k.replace("-", "_"): v for k, v in mo.cli_args().items()})
    else:
        flow_params = FlowParams(**params_form.value)
    return (flow_params,)


@app.cell
def _(LifelineConfig, flow_params, mo):
    cfg = LifelineConfig.from_yaml(flow_params.config_path)
    mo.md(f"**Config loaded.**")
    return (cfg,)


@app.cell
def _(Path, cfg, flow_params, gpd, mo, nearest_neighbor_join, pd):
    def _load_silver_points():
        path = Path(cfg.storage.silver_path) / "lifeline_points.parquet"
        if not path.exists():
            return None
        gdf = gpd.read_parquet(path)
        if gdf.crs is None:
            gdf = gdf.set_crs("EPSG:4326")
        return gdf

    def _load_gersite_buildings():
        path = Path(flow_params.gersite_path)
        if not path.exists():
            return None
        gdf = gpd.read_parquet(path) if str(path).endswith(".parquet") else gpd.read_file(path)
        if gdf.crs is None:
            gdf = gdf.set_crs("EPSG:4326")
        elif gdf.crs.to_epsg() != 4326:
            gdf = gdf.to_crs("EPSG:4326")
        return gdf

    def _build_bridge_table(points, buildings):
        pts = points.copy()
        pts["geometry"] = pts.geometry.apply(lambda g: g.centroid if g.geom_type != "Point" else g)
        joined = nearest_neighbor_join(pts, buildings, max_distance_m=flow_params.max_distance_m)
        bridge_cols = ["lifeline_id", "match_distance_m"]
        auth_cols = [c for c in joined.columns if c.endswith("_auth")]
        bridge = joined[bridge_cols + auth_cols].copy()
        bridge.columns = [c.replace("_auth", "") if c.endswith("_auth") else c for c in bridge.columns]
        bridge["has_building_match"] = bridge["match_distance_m"].notna()
        return bridge

    silver_path = Path(cfg.storage.silver_path)
    silver_path.mkdir(parents=True, exist_ok=True)

    points = _load_silver_points()
    if points is None:
        bridge_result = mo.callout(mo.md("❌ **Silver points not found.** Run Flow 02 first."), kind="danger")
        bridge = None
    else:
        buildings = _load_gersite_buildings()
        if buildings is None:
            if flow_params.skip_missing:
                print("  No GERSite data — creating stub bridge table (all unmatched)")
                bridge = pd.DataFrame({
                    "lifeline_id": points["lifeline_id"],
                    "match_distance_m": float("nan"),
                    "has_building_match": False,
                })
                bridge_result = mo.callout(mo.md("⚠️ **GERSite data not found.** Stub bridge table created."), kind="warn")
            else:
                bridge_result = mo.callout(
                    mo.md("❌ **GERSite buildings not found.** Provide `--gersite-path` or enable `skip_missing`."),
                    kind="danger",
                )
                bridge = None
        else:
            print(f"  Points: {len(points):,}  |  Buildings: {len(buildings):,}")
            bridge = _build_bridge_table(points, buildings)
            matched = bridge["has_building_match"].sum()
            bridge_result = mo.callout(
                mo.md(f"✅ **Bridge complete.** Matched `{matched:,}` of `{len(bridge):,}` points (`{100*matched/len(bridge):.1f}%`)"),
                kind="success",
            )

    if bridge is not None:
        out_path = silver_path / "bridge_poi_building.parquet"
        bridge.to_parquet(out_path, index=False)
        print(f"  silver/bridge_poi_building.parquet — {len(bridge):,} rows")

    bridge_result
    return (bridge_result,)


@app.cell
def _(bridge_result, mo):
    mo.vstack([
        mo.md("## Bridge Summary"),
        bridge_result,
        mo.callout(mo.md("✅ **Flow 03 complete.** ➡ Run `flows/04_gold_production.py` next."), kind="success"),
    ])
    return


if __name__ == "__main__":
    app.run()
