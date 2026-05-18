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
    # Flow 02b · Campus Collapse

    Collapses campus-style OSM POIs to one gold-level point per campus polygon.

    For each campus polygon (`amenity=hospital`, `amenity=university`,
    `amenity=school`, `amenity=college`), all sub-features (building nodes,
    ancillary service points) whose centroid falls within the polygon boundary
    are collapsed into a single primary campus POI.  Attributes are conflated
    (e.g. `emergency=yes` propagates if *any* sub-feature has it; `beds` sums
    across the group).  School campuses are re-categorised using `isced:level`.

    Three Silver outputs are written:
    - **`silver/lifeline_points.parquet`** — updated: campus primaries replace sub-features
    - **`silver/campus_buildings.parquet`** — sub-features with `osm_campus_polygon_id` links
    - **`silver/campus_polygons.parquet`** — campus polygon boundaries with primary POI links

    **Interactive:** fill in the form below and click **Run Campus Collapse**.
    **Script:** `marimo run flows/02b_campus_collapse.py -- --help`
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
    from lib.campus_collapse import collapse_campus_layer

    return BaseModel, Field, LifelineConfig, Path, collapse_campus_layer, gpd, pd


@app.cell
def _(BaseModel, Field):
    class FlowParams(BaseModel):
        config_path: str = Field(default="config.lifeline.yaml", description="Path to config YAML file")

    return (FlowParams,)


@app.cell
def _(mo):
    params_form = (
        mo.md("""
        ## Parameters

        Config file: {config_path}
        """)
        .batch(
            config_path=mo.ui.text(value="config.lifeline.yaml", label="Config path"),
        )
        .form(submit_button_label="▶ Run Campus Collapse")
    )
    params_form
    return (params_form,)


@app.cell
def _(FlowParams, mo):
    import sys as _sys
    is_script_mode = mo.app_meta().mode == "script"
    if is_script_mode and "help" in mo.cli_args():
        print("Usage: marimo run flows/02b_campus_collapse.py -- [options]\n")
        for _name, _field in FlowParams.model_fields.items():
            _default = f"(default: {_field.default})" if _field.default is not None else "(required)"
            print(f"  --{_name.replace('_', '-'):<28} {_field.description} {_default}")
        _sys.exit(0)
    return (is_script_mode,)


@app.cell
def _(FlowParams, is_script_mode, mo, params_form):
    mo.stop(
        not is_script_mode and params_form.value is None,
        mo.callout(mo.md("**Fill in the parameters above and click _Run Campus Collapse_ to start.**"), kind="info"),
    )
    if is_script_mode:
        flow_params = FlowParams(**{k.replace("-", "_"): v for k, v in mo.cli_args().items()})
    else:
        flow_params = FlowParams(**params_form.value)
    return (flow_params,)


@app.cell
def _(LifelineConfig, flow_params, mo):
    cfg = LifelineConfig.from_yaml(flow_params.config_path)
    mo.md(f"**Config loaded.** Campus collapse enabled: `{cfg.campus_collapse.enabled}`")
    return (cfg,)


@app.cell
def _(Path, cfg, collapse_campus_layer, gpd, mo, pd):
    from pathlib import Path as _P

    _silver_path = _P(cfg.storage.silver_path)
    _silver_pts_path = _silver_path / "lifeline_points.parquet"

    if not cfg.campus_collapse.enabled:
        campus_collapse_result = mo.callout(
            mo.md("⏭ Campus collapse disabled in config."), kind="neutral"
        )
        _campus_collapse_done = False
    elif not _silver_pts_path.exists():
        campus_collapse_result = mo.callout(
            mo.md("⚠️ `silver/lifeline_points.parquet` not found — run Flow 02 first."), kind="warn"
        )
        _campus_collapse_done = False
    else:
        _silver = gpd.read_parquet(_silver_pts_path)
        if _silver.crs is None:
            _silver = _silver.set_crs("EPSG:4326")

        _all_buildings: list[gpd.GeoDataFrame] = []
        _all_polygons: list[gpd.GeoDataFrame] = []
        _layer_stats: list[tuple[str, int, int, int]] = []

        for _layer_name, _layer_cfg in cfg.campus_collapse.layers.items():
            _amenities = _layer_cfg.campus_polygon_amenities
            if not _amenities:
                continue

            # Load extended attribute table to get isced:level (education layer)
            _attr_path = _silver_path / f"attr_{_layer_name}.parquet"
            _attr_df = pd.read_parquet(_attr_path) if _attr_path.exists() else None

            _n_before = int((_silver["tmp_osm_layer"] == _layer_name).sum())
            _silver, _buildings, _polys = collapse_campus_layer(
                silver_gdf=_silver,
                layer_name=_layer_name,
                campus_amenities=_amenities,
                attr_gdf=_attr_df,
            )
            _n_after = int((_silver["tmp_osm_layer"] == _layer_name).sum())

            if len(_buildings) > 0:
                _all_buildings.append(_buildings)
            if len(_polys) > 0:
                _all_polygons.append(_polys)
            _layer_stats.append((_layer_name, _n_before, _n_after, len(_buildings)))

        # Write updated silver lifeline_points
        _silver.to_parquet(_silver_pts_path, index=False)
        print(f"  Updated silver/lifeline_points.parquet — {len(_silver):,} total rows")

        # Write campus_buildings (append across layers into one file)
        _buildings_path = _silver_path / "campus_buildings.parquet"
        if _all_buildings:
            _combined_buildings = gpd.GeoDataFrame(
                pd.concat(_all_buildings, ignore_index=True),
                geometry="geometry",
                crs="EPSG:4326",
            )
            _combined_buildings.to_parquet(_buildings_path, index=False)
            print(f"  silver/campus_buildings.parquet — {len(_combined_buildings):,} rows")
        else:
            print("  silver/campus_buildings.parquet — 0 rows (no sub-features collapsed)")

        # Write campus_polygons
        _polygons_path = _silver_path / "campus_polygons.parquet"
        if _all_polygons:
            _combined_polys = gpd.GeoDataFrame(
                pd.concat(_all_polygons, ignore_index=True),
                geometry="geometry",
                crs="EPSG:4326",
            )
            _combined_polys.to_parquet(_polygons_path, index=False)
            print(f"  silver/campus_polygons.parquet — {len(_combined_polys):,} rows")
        else:
            print("  silver/campus_polygons.parquet — 0 rows (no campus polygons found)")

        # Aggregate CMS _cnt fields for collapsed hospital campuses:
        # bed_cnt, crna_cnt, physn_cnt, etc. are summed across each campus group
        # so the campus primary carries the total for all buildings on that campus.
        from lib.campus_collapse import aggregate_cms_attrs_for_campuses as _agg_cms
        _cms_updated = _agg_cms(_silver_path)

        _stats_lines = "\n".join(
            f"  - `{lyr}`: {n_before} → {n_after} primary POIs, {n_bldg} sub-features moved"
            for lyr, n_before, n_after, n_bldg in _layer_stats
        )
        _cms_note = (
            f"\n\n  CMS counts aggregated for `{_cms_updated}` hospital campus primaries."
            if _cms_updated > 0 else ""
        )
        campus_collapse_result = mo.callout(
            mo.md(
                f"✅ **Campus collapse complete.**\n\n{_stats_lines}{_cms_note}"
            ),
            kind="success",
        )
        _campus_collapse_done = True

    campus_collapse_result
    return (campus_collapse_result,)


@app.cell
def _(campus_collapse_result, is_script_mode, mo):
    if is_script_mode:
        print("Campus Collapse Summary")
        print("✅ Flow 02b complete. ➡ Run `flows/03_gersite_bridge.py` next.")
    else:
        mo.vstack([
            mo.md("## Campus Collapse Summary"),
            campus_collapse_result,
            mo.callout(mo.md("✅ **Flow 02b complete.** ➡ Run `flows/03_gersite_bridge.py` next."), kind="success"),
        ])
    return


if __name__ == "__main__":
    app.run()
