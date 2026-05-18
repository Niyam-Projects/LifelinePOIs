import marimo

__generated_with = "0.23.5"
app = marimo.App(width="medium")


@app.cell(hide_code=True)
def _():
    import marimo as mo

    return (mo,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    # Flow 05 · PMTiles Generation

    Reads Gold GeoParquet layers and generates a PMTiles archive using
    freestiler (Rust-powered, in-process — no tippecanoe required).

    **Interactive:** fill in the form below and click **Generate Tiles**.
    **Script:** `marimo run flows/05_generate_tiles.py -- --help`
    """)
    return


@app.cell
def _():
    import time
    from pathlib import Path

    import geopandas as gpd
    import pandas as pd
    from pydantic import BaseModel, Field

    import sys as _sys
    _sys.path.insert(0, str(Path(".").resolve()))
    from src.lifelinepoi.config import LifelineConfig

    return BaseModel, Field, LifelineConfig, Path, gpd, pd, time


@app.cell
def _(BaseModel, Field):
    class FlowParams(BaseModel):
        config_path: str = Field(default="config.lifeline.yaml", description="Path to config YAML file")
        layer: str = Field(default="", description="Single layer only (empty = all layers)")
        per_layer: bool = Field(default=False, description="Also output one .pmtiles file per layer")
        min_zoom: int = Field(default=0, description="Min zoom override (0 = use config value)")
        max_zoom: int = Field(default=0, description="Max zoom override (0 = use config value)")

    return (FlowParams,)


@app.cell
def _(mo):
    params_form = (
        mo.md("""
        ## Parameters

        Config file: {config_path}

        Single layer filter (blank = all layers): {layer}

        {per_layer} Output per-layer .pmtiles files in addition to combined tileset

        Min zoom override (0 = use config): {min_zoom}

        Max zoom override (0 = use config): {max_zoom}
        """)
        .batch(
            config_path=mo.ui.text(value="config.lifeline.yaml", label="Config path"),
            layer=mo.ui.text(value="", placeholder="e.g. power", label=""),
            per_layer=mo.ui.checkbox(value=False, label=""),
            min_zoom=mo.ui.slider(0, 8, value=0, step=1, label="Min zoom"),
            max_zoom=mo.ui.slider(0, 22, value=0, step=1, label="Max zoom"),
        )
        .form(submit_button_label="▶ Generate Tiles")
    )
    params_form
    return (params_form,)


@app.cell
def _(FlowParams, mo):
    import sys as _sys
    is_script_mode = mo.app_meta().mode == "script"
    if is_script_mode and "help" in mo.cli_args():
        print("Usage: marimo run flows/05_generate_tiles.py -- [options]\n")
        for _name, _field in FlowParams.model_fields.items():
            _default = f"(default: {_field.default})" if _field.default is not None else "(required)"
            print(f"  --{_name.replace('_', '-'):<28} {_field.description} {_default}")
        _sys.exit(0)
    return (is_script_mode,)


@app.cell
def _(FlowParams, is_script_mode, mo, params_form):
    mo.stop(
        not is_script_mode and params_form.value is None,
        mo.callout(mo.md("**Fill in the parameters above and click _Generate Tiles_ to start.**"), kind="info"),
    )
    if is_script_mode:
        flow_params = FlowParams(**{k.replace("-", "_"): v for k, v in mo.cli_args().items()})
    else:
        flow_params = FlowParams(**params_form.value)
    return (flow_params,)


@app.cell
def _(LifelineConfig, flow_params, mo):
    cfg = LifelineConfig.from_yaml(flow_params.config_path)
    mo.md(f"**Config loaded.** Layer name: `{cfg.tiles.layer_name}`  |  Zoom: z{cfg.tiles.min_zoom}–z{cfg.tiles.max_zoom}")
    return (cfg,)


@app.cell
def _(Path, cfg, flow_params, gpd, mo, pd, time):
    import freestiler as _freestiler

    def _load_gold_layer(gold_path, layer):
        path = gold_path / f"wide_{layer}.parquet"
        if not path.exists():
            print(f"    WARNING: Gold layer not found: {path} — skipping")
            return None
        gdf = gpd.read_parquet(path)
        if gdf.crs is None:
            gdf = gdf.set_crs("EPSG:4326")
        elif gdf.crs.to_epsg() != 4326:
            gdf = gdf.to_crs("EPSG:4326")
        print(f"    Loaded {len(gdf):,} features from wide_{layer}.parquet")
        return gdf

    def _prepare_for_tiles(gdf):
        gdf = gdf.copy()
        drop_cols = [c for c in ["bbox", "names"] if c in gdf.columns]
        if drop_cols:
            gdf = gdf.drop(columns=drop_cols)
        for col in gdf.columns:
            if col == "geometry":
                continue
            sample = gdf[col].dropna()
            if len(sample) > 0 and isinstance(sample.iloc[0], (dict, list)):
                gdf[col] = gdf[col].apply(lambda v: str(v) if v is not None else None)
        non_point_mask = gdf.geometry.geom_type != "Point"
        if non_point_mask.any():
            gdf.loc[non_point_mask, "geometry"] = gdf.loc[non_point_mask].geometry.centroid
        return gdf

    def _write_pmtiles(gdf, output_path, layer_name, min_zoom, max_zoom):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        t0 = time.perf_counter()
        _freestiler.freestile(
            input=gdf, output=str(output_path),
            layer_name=layer_name, min_zoom=min_zoom, max_zoom=max_zoom,
        )
        elapsed = time.perf_counter() - t0
        size_mb = output_path.stat().st_size / 1_048_576
        print(f"    -> {output_path.name}: {size_mb:.1f} MB in {elapsed:.1f}s")

    gold_path = Path(cfg.storage.gold_path)
    tiles_path = Path(cfg.storage.tiles_path)
    tiles_path.mkdir(parents=True, exist_ok=True)

    min_zoom = flow_params.min_zoom if flow_params.min_zoom > 0 else cfg.tiles.min_zoom
    max_zoom = flow_params.max_zoom if flow_params.max_zoom > 0 else cfg.tiles.max_zoom
    layer_name = cfg.tiles.layer_name
    print(f"Zoom range: z{min_zoom}–z{max_zoom}")

    layers = [flow_params.layer] if flow_params.layer else cfg.osm.layers
    all_gdfs = []

    for _layer in layers:
        print(f"\n  Processing: {_layer}")
        gdf = _load_gold_layer(gold_path, _layer)
        if gdf is None:
            continue
        gdf = _prepare_for_tiles(gdf)
        gdf["source_layer"] = _layer
        if flow_params.per_layer:
            per_layer_path = tiles_path / f"{_layer}.pmtiles"
            print(f"    Writing per-layer tileset: {per_layer_path.name}")
            _write_pmtiles(gdf, per_layer_path, _layer, min_zoom, max_zoom)
        all_gdfs.append(gdf)

    if not all_gdfs:
        tiles_result = mo.callout(mo.md("⚠️ **No Gold layers found.** Run Flow 04 first."), kind="warn")
    else:
        combined = gpd.GeoDataFrame(pd.concat(all_gdfs, ignore_index=True), crs="EPSG:4326")
        combined_path = tiles_path / "lifeline_poi.pmtiles"
        print(f"\n  Writing combined tileset ({len(combined):,} features)")
        _write_pmtiles(combined, combined_path, layer_name, min_zoom, max_zoom)
        tiles_result = mo.callout(
            mo.md(f"✅ **PMTiles generation complete.** `{len(combined):,}` features → `{combined_path}`"),
            kind="success",
        )

    tiles_result
    return (tiles_result,)


@app.cell
def _(is_script_mode, mo, tiles_result):
    if is_script_mode:
        print("Tiles Summary")
        print("Flow 05 complete. Run flows/06_qa.py for visual QA.")
    else:
        mo.vstack([
            mo.md("## Tiles Summary"),
            tiles_result,
            mo.callout(mo.md("✅ **Flow 05 complete.** ➡ Run `flows/06_qa.py` for visual QA."), kind="success"),
        ])
    return


if __name__ == "__main__":
    app.run()
