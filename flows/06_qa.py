import marimo

__generated_with = "0.10.0"
app = marimo.App(width="wide")


@app.cell(hide_code=True)
def _():
    import marimo as mo
    return (mo,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    # Flow 06 · Interactive QA Dashboard

    Visual QA for inspecting Silver layer confidence scores, low-confidence
    clusters, and divergences between OSM and authoritative sources.

    **Interactive:** fill in the form below and click **Load Data**.
    **Script:** `marimo run flows/06_qa.py -- --help`
    """)
    return


@app.cell
def _():
    from pathlib import Path

    import geopandas as gpd
    import pandas as pd
    import altair as alt
    from pydantic import BaseModel, Field

    import sys as _sys
    _sys.path.insert(0, str(Path(".").resolve()))
    from src.lifelinepoi.config import LifelineConfig

    return BaseModel, Field, LifelineConfig, Path, alt, gpd, pd


@app.cell
def _(BaseModel, Field):
    class FlowParams(BaseModel):
        config_path: str = Field(default="config.lifeline.yaml", description="Path to config YAML file")
        layer_filter: str = Field(default="", description="Filter to a single layer (empty = all layers)")

    return (FlowParams,)


@app.cell
def _(FlowParams, mo):
    params_form = (
        mo.md("""
        ## Parameters

        Config file: {config_path}

        Layer filter (blank = all layers): {layer_filter}
        """)
        .batch(
            config_path=mo.ui.text(value="config.lifeline.yaml", label="Config path"),
            layer_filter=mo.ui.text(value="", placeholder="e.g. power", label=""),
        )
        .form(submit_button_label="▶ Load Data")
    )
    params_form
    return (params_form,)


@app.cell
def _(FlowParams, mo):
    import sys as _sys
    is_script_mode = mo.app_meta().mode == "script"
    if is_script_mode and (not mo.cli_args() or "help" in mo.cli_args()):
        print("Usage: marimo run flows/06_qa.py -- [options]\n")
        for _name, _field in FlowParams.model_fields.items():
            _default = f"(default: {_field.default})" if _field.default is not None else "(required)"
            print(f"  --{_name.replace('_', '-'):<28} {_field.description} {_default}")
        _sys.exit(0)
    return (is_script_mode,)


@app.cell
def _(FlowParams, is_script_mode, mo, params_form):
    mo.stop(
        not is_script_mode and params_form.value is None,
        mo.callout(mo.md("**Fill in the parameters above and click _Load Data_ to start.**"), kind="info"),
    )
    if is_script_mode:
        flow_params = FlowParams(**{k.replace("-", "_"): v for k, v in mo.cli_args().items()})
    else:
        flow_params = FlowParams(**params_form.value)
    return (flow_params,)


@app.cell
def _(LifelineConfig, flow_params, mo):
    cfg = LifelineConfig.from_yaml(flow_params.config_path)
    mo.md(f"**Config loaded.** OSM PBF: `{cfg.osm.pbf_path}`  |  Layers: `{', '.join(cfg.osm.layers)}`")
    return (cfg,)


@app.cell
def _(Path, cfg, flow_params, gpd, mo):
    silver_path = Path(cfg.storage.silver_path)
    master_path = silver_path / "lifeline_points.parquet"

    if not master_path.exists():
        master = None
        load_status = mo.callout(
            mo.md("⚠️ **Silver master not found.** Run `flows/02_silver_conflation.py` first."),
            kind="warn",
        )
    else:
        master = gpd.read_parquet(master_path)
        if flow_params.layer_filter:
            master = master[master["lifeline_layer"] == flow_params.layer_filter]
        load_status = mo.callout(
            mo.md(f"✅ Loaded **{len(master):,} records** from `silver/lifeline_points.parquet`"),
            kind="success",
        )
    load_status
    return (load_status, master, silver_path)


@app.cell
def _(alt, master, mo):
    if master is None:
        mo.stop(True, mo.md("No data loaded."))

    score_hist = (
        alt.Chart(master[["confidence_score", "lifeline_layer"]].rename(columns={"confidence_score": "score"}))
        .mark_bar(opacity=0.75)
        .encode(
            x=alt.X("score:Q", bin=alt.Bin(step=0.05), title="Confidence Score"),
            y=alt.Y("count():Q", title="Feature Count"),
            color=alt.Color("lifeline_layer:N", title="Layer"),
            tooltip=["lifeline_layer:N", "count():Q"],
        )
        .properties(title="Confidence Score Distribution by Layer", width=600, height=300)
        .interactive()
    )

    tier_bar = (
        alt.Chart(master[["confidence_tier", "lifeline_layer"]])
        .mark_bar()
        .encode(
            x=alt.X("lifeline_layer:N", title="Layer"),
            y=alt.Y("count():Q", title="Count"),
            color=alt.Color(
                "confidence_tier:N",
                scale=alt.Scale(domain=["high", "medium", "low"], range=["#2ca02c", "#ff7f0e", "#d62728"]),
                title="Tier",
            ),
            tooltip=["lifeline_layer:N", "confidence_tier:N", "count():Q"],
        )
        .properties(title="Confidence Tier by Layer", width=400, height=300)
    )

    mo.hstack([mo.altair_chart(score_hist), mo.altair_chart(tier_bar)])
    return (score_hist, tier_bar)


@app.cell
def _(master, mo):
    if master is None:
        mo.stop(True)

    summary = (
        master.groupby(["lifeline_layer", "confidence_tier"])
        .agg(count=("lifeline_id", "count"), avg_score=("confidence_score", "mean"))
        .reset_index()
        .round({"avg_score": 3})
    )
    mo.vstack([
        mo.md("### Summary by Layer × Confidence Tier"),
        mo.ui.table(summary),
    ])
    return (summary,)


@app.cell
def _(master, mo):
    if master is None:
        mo.stop(True)

    low = master[master["confidence_tier"] == "low"].copy()
    mo.vstack([
        mo.md(f"### Low-Confidence Records ({len(low):,} total)"),
        mo.md("These records have no authoritative match and sparse OSM attributes."),
        mo.ui.table(
            low[["lifeline_id", "lifeline_layer", "display_name", "confidence_score",
                 "source_provenance", "h3_index"]].head(200)
        ),
    ])
    return (low,)


@app.cell
def _(master, mo):
    if master is None:
        mo.stop(True)

    geom_stats = (
        master.copy()
        .assign(geom_type=master.geometry.geom_type)
        .groupby(["lifeline_layer", "geom_type"])
        .size()
        .reset_index(name="count")
    )
    mo.vstack([
        mo.md("### Geometry Type Breakdown"),
        mo.ui.table(geom_stats),
    ])
    return (geom_stats,)


if __name__ == "__main__":
    app.run()
