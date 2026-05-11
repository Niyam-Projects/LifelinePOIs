import marimo

__generated_with = "0.23.5"
app = marimo.App(width="wide")


@app.cell(hide_code=True)
def _():
    import marimo as mo

    return (mo,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    # Flow 06 · Interactive QA Dashboard

    Full QAQC for the Silver layer: confidence scores, source provenance,
    FEMA taxonomy coverage, EPA minted POIs, data completeness, and
    HIFLD gold inventory with optional snapshot change detection.

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
        layer_filter: str = Field(default="", description="Filter to a single OSM layer (empty = all)")

    return (FlowParams,)


@app.cell
def _(mo):
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
            master = master[master["tmp_osm_layer"] == flow_params.layer_filter]
        load_status = mo.callout(
            mo.md(
                f"✅ Loaded **{len(master):,} records** from `silver/lifeline_points.parquet`  "
                f"| Columns: `{', '.join(master.columns.tolist())}`"
            ),
            kind="success",
        )
    load_status
    return (master,)


@app.cell
def _(alt, master, mo):
    if master is None:
        mo.stop(True, mo.md("No data loaded."))

    _plot_df = (
        master[["confidence_score", "tmp_osm_layer"]]
        .rename(columns={"confidence_score": "score", "tmp_osm_layer": "layer"})
    )
    score_hist = (
        alt.Chart(_plot_df)
        .mark_bar(opacity=0.75)
        .encode(
            x=alt.X("score:Q", bin=alt.Bin(step=0.05), title="Confidence Score"),
            y=alt.Y("count():Q", title="Feature Count"),
            color=alt.Color("layer:N", title="Layer"),
            tooltip=["layer:N", "count():Q"],
        )
        .properties(title="Confidence Score Distribution by Layer", width=600, height=300)
        .interactive()
    )

    _tier_df = master[["confidence_tier", "tmp_osm_layer"]].rename(columns={"tmp_osm_layer": "layer"})
    tier_bar = (
        alt.Chart(_tier_df)
        .mark_bar()
        .encode(
            x=alt.X("layer:N", title="Layer"),
            y=alt.Y("count():Q", title="Count"),
            color=alt.Color(
                "confidence_tier:N",
                scale=alt.Scale(domain=["high", "medium", "low"], range=["#2ca02c", "#ff7f0e", "#d62728"]),
                title="Tier",
            ),
            tooltip=["layer:N", "confidence_tier:N", "count():Q"],
        )
        .properties(title="Confidence Tier by Layer", width=400, height=300)
    )

    mo.hstack([score_hist, tier_bar])
    return


@app.cell
def _(master, mo):
    if master is None:
        mo.stop(True)

    _summary = (
        master.groupby(["tmp_osm_layer", "confidence_tier"])
        .agg(count=("lifeline_id", "count"), avg_score=("confidence_score", "mean"))
        .reset_index()
        .rename(columns={"tmp_osm_layer": "layer"})
        .round({"avg_score": 3})
    )
    mo.vstack([
        mo.md("### Summary by Layer × Confidence Tier"),
        mo.ui.table(_summary),
    ])
    return


@app.cell
def _(alt, master, mo):
    if master is None:
        mo.stop(True)

    _prov_df = (
        master.groupby(["tmp_osm_layer", "source_provenance"])
        .agg(count=("lifeline_id", "count"))
        .reset_index()
        .rename(columns={"tmp_osm_layer": "layer"})
        .sort_values("count", ascending=False)
    )

    prov_bar = (
        alt.Chart(_prov_df)
        .mark_bar()
        .encode(
            x=alt.X("layer:N", title="Layer"),
            y=alt.Y("count:Q", title="Count"),
            color=alt.Color("source_provenance:N", title="Provenance"),
            tooltip=["layer:N", "source_provenance:N", "count:Q"],
        )
        .properties(title="Source Provenance by Layer", width=600, height=300)
    )

    mo.vstack([
        mo.md("### Source Provenance Breakdown"),
        mo.md(
            "Provenance values: `osm` · `osm+hifld` · `osm+epa_naics` · `osm+hifld+epa_naics` · "
            "`epa_frs` · `epa_frs+overture_geocode`"
        ),
        prov_bar,
        mo.ui.table(_prov_df),
    ])
    return


@app.cell
def _(alt, master, mo):
    if master is None:
        mo.stop(True)

    _fema_df = (
        master.assign(
            has_fema=master["tmp_fema_id"].notna(),
        )
        .groupby(["tmp_osm_layer", "has_fema"])
        .agg(count=("lifeline_id", "count"))
        .reset_index()
        .rename(columns={"tmp_osm_layer": "layer"})
    )
    _fema_df["label"] = _fema_df["has_fema"].map({True: "FEMA ID assigned", False: "Unclassified"})

    _totals = _fema_df.groupby("layer")["count"].sum().rename("total")
    _fema_df = _fema_df.join(_totals, on="layer")
    _fema_df["pct"] = (_fema_df["count"] / _fema_df["total"] * 100).round(1)

    fema_bar = (
        alt.Chart(_fema_df)
        .mark_bar()
        .encode(
            x=alt.X("layer:N", title="Layer"),
            y=alt.Y("pct:Q", title="% of Records", scale=alt.Scale(domain=[0, 100])),
            color=alt.Color(
                "label:N",
                scale=alt.Scale(domain=["FEMA ID assigned", "Unclassified"], range=["#2ca02c", "#d62728"]),
                title="FEMA Coverage",
            ),
            tooltip=["layer:N", "label:N", "count:Q", "pct:Q"],
        )
        .properties(title="FEMA Lifeline Taxonomy Coverage by Layer", width=500, height=300)
    )

    _fema_pivot = (
        _fema_df[["layer", "label", "count", "pct"]]
        .pivot(index="layer", columns="label", values=["count", "pct"])
    )
    _fema_pivot.columns = [f"{stat}_{lbl.replace(' ', '_')}" for stat, lbl in _fema_pivot.columns]
    _fema_pivot = _fema_pivot.reset_index().fillna(0)

    mo.vstack([
        mo.md("### FEMA Taxonomy Coverage"),
        fema_bar,
        mo.ui.table(_fema_pivot),
    ])
    return


@app.cell
def _(master, mo):
    if master is None:
        mo.stop(True)

    _epa_mask = master["source_provenance"].fillna("").str.startswith("epa_frs")
    _epa_pois = master[_epa_mask].copy()

    if len(_epa_pois) == 0:
        mo.vstack([
            mo.md("### EPA FRS Minted POIs"),
            mo.callout(mo.md("No EPA-minted POIs found in Silver layer."), kind="neutral"),
        ])
    else:
        _epa_summary = (
            _epa_pois.groupby(["tmp_osm_layer", "source_provenance"])
            .agg(
                count=("lifeline_id", "count"),
                avg_score=("confidence_score", "mean"),
            )
            .reset_index()
            .rename(columns={"tmp_osm_layer": "layer"})
            .round({"avg_score": 3})
        )

        _display_cols = [c for c in [
            "lifeline_id", "display_name", "tmp_osm_layer",
            "source_provenance", "confidence_score", "confidence_tier",
            "tmp_lifeline_component", "tmp_lifeline_subcomponent",
            "epa_registry_id", "naics_codes", "naics_lifeline_tier",
        ] if c in _epa_pois.columns]

        mo.vstack([
            mo.md(f"### EPA FRS Minted POIs ({len(_epa_pois):,} total)"),
            mo.md("These points were synthesized from EPA FRS records not matched to an OSM feature."),
            mo.ui.table(_epa_summary),
            mo.md("#### Detail (first 200)"),
            mo.ui.table(_epa_pois[_display_cols].head(200)),
        ])
    return


@app.cell
def _(master, mo):
    if master is None:
        mo.stop(True)

    _issues = []

    # Duplicate lifeline_id
    _dup_ids = master["lifeline_id"].duplicated(keep=False)
    _n_dups = int(_dup_ids.sum())
    if _n_dups > 0:
        _issues.append(f"⚠️ **{_n_dups:,} duplicate `lifeline_id` values** — UUID generation may be broken.")

    # epa_naics in provenance → should have epa_registry_id
    if "epa_registry_id" in master.columns:
        _naics_mask = master["source_provenance"].fillna("").str.contains("epa_naics")
        _missing_reg = master[_naics_mask & master["epa_registry_id"].isna()]
        if len(_missing_reg) > 0:
            _issues.append(
                f"⚠️ **{len(_missing_reg):,} records** have `epa_naics` provenance but no `epa_registry_id`."
            )

    # h3_index should be populated for OSM-only points
    if "h3_index" in master.columns:
        _osm_only = master[master["source_provenance"] == "osm"]
        _missing_h3 = _osm_only["h3_index"].isna().sum()
        if _missing_h3 > 0:
            _issues.append(f"ℹ️ **{_missing_h3:,} OSM-only records** are missing `h3_index`.")

    # Records with no FEMA component but OSM source (expected to have tag matches)
    if "tmp_fema_id" in master.columns:
        _osm_no_fema = master[
            master["source_provenance"].fillna("").str.startswith("osm")
            & master["tmp_fema_id"].isna()
        ]
        if len(_osm_no_fema) > 0:
            _issues.append(
                f"ℹ️ **{len(_osm_no_fema):,} OSM-sourced records** have no FEMA taxonomy match "
                f"(unrecognized OSM tags)."
            )

    _check_md = "\n".join(_issues) if _issues else "✅ No consistency issues found."
    mo.vstack([
        mo.md("### Data Integrity Checks"),
        mo.callout(mo.md(_check_md), kind="warn" if any("⚠️" in i for i in _issues) else "success"),
    ])
    return


@app.cell
def _(master, mo):
    if master is None:
        mo.stop(True)

    _tracked_cols = [c for c in [
        "display_name", "h3_index", "osm_category",
        "tmp_fema_id", "tmp_lifeline_component",
        "source_provenance",
        "epa_registry_id", "naics_codes",
    ] if c in master.columns]

    _rows = []
    for _layer in sorted(master["tmp_osm_layer"].dropna().unique()):
        _subset = master[master["tmp_osm_layer"] == _layer]
        _n = len(_subset)
        for _col in _tracked_cols:
            _null_n = int(_subset[_col].isna().sum())
            _rows.append({"layer": _layer, "field": _col, "total": _n,
                          "null_count": _null_n, "null_pct": round(100 * _null_n / _n, 1) if _n else 0})

    import pandas as _pd_local
    _coverage = _pd_local.DataFrame(_rows)

    mo.vstack([
        mo.md("### Field Completeness by Layer"),
        mo.md("Expected nulls: EPA columns are `null` for OSM-only records; `osm_category` is `null` for EPA-minted POIs."),
        mo.ui.table(_coverage),
    ])
    return


@app.cell
def _(master, mo):
    if master is None:
        mo.stop(True)

    _low = master[master["confidence_tier"] == "low"].copy()
    _display_cols = [c for c in [
        "lifeline_id", "tmp_osm_layer", "display_name",
        "confidence_score", "source_provenance",
        "tmp_fema_id", "h3_index",
    ] if c in _low.columns]

    mo.vstack([
        mo.md(f"### Low-Confidence Records ({len(_low):,} total)"),
        mo.md("Records with no authoritative match and sparse OSM attributes."),
        mo.ui.table(_low[_display_cols].head(200)),
    ])
    return


@app.cell
def _(master, mo):
    if master is None:
        mo.stop(True)

    _geom_stats = (
        master.copy()
        .assign(geom_type=master.geometry.geom_type)
        .groupby(["tmp_osm_layer", "geom_type"])
        .size()
        .reset_index(name="count")
        .rename(columns={"tmp_osm_layer": "layer"})
    )
    mo.vstack([
        mo.md("### Geometry Type Breakdown"),
        mo.ui.table(_geom_stats),
    ])
    return


@app.cell
def _(Path, cfg, flow_params, gpd, mo, pd):
    _gold_path = Path(cfg.storage.gold_path)
    _hifld_files = sorted(_gold_path.glob("hifld_*.parquet")) if _gold_path.exists() else []

    # Build OSM-layer crosswalk from config
    _osm_layer_map: dict[str, str] = {}
    if cfg.hifld.enabled:
        for _lname, _ldef in cfg.hifld.layers.items():
            _osm_layer_map[_lname] = getattr(_ldef, "osm_layer", "")

    _hifld_rows = []
    _hifld_gdfs: dict[str, gpd.GeoDataFrame] = {}
    for _f in _hifld_files:
        _lname = _f.stem.replace("hifld_", "", 1)
        # Skip if layer_filter is active and this HIFLD layer maps to a different OSM layer
        if flow_params.layer_filter:
            _mapped_osm = _osm_layer_map.get(_lname, "")
            if _mapped_osm and _mapped_osm != flow_params.layer_filter:
                continue
        try:
            _gdf = gpd.read_parquet(_f)
            _hifld_gdfs[_lname] = _gdf
            _n = len(_gdf)
            _osm_matched = int(_gdf["osm_lifeline_id"].notna().sum()) if "osm_lifeline_id" in _gdf.columns else None
            _match_pct = round(100 * _osm_matched / _n, 1) if _n > 0 and _osm_matched is not None else None
            _hifld_rows.append({
                "hifld_layer": _lname,
                "osm_layer": _osm_layer_map.get(_lname, "—"),
                "row_count": _n,
                "osm_matched": _osm_matched,
                "osm_match_pct": _match_pct,
            })
        except Exception as _e:
            _hifld_rows.append({"hifld_layer": _lname, "osm_layer": _osm_layer_map.get(_lname, "—"),
                                 "row_count": None, "osm_matched": None, "osm_match_pct": None})

    if not _hifld_rows:
        hifld_gdfs = {}
        _display = mo.callout(
            mo.md("⏭ No HIFLD gold files found. Run `flows/04_gold_production.py` first."), kind="neutral"
        )
    else:
        hifld_gdfs = _hifld_gdfs
        _inv_df = pd.DataFrame(_hifld_rows)
        _display = mo.vstack([
            mo.md("### HIFLD Gold Layer Inventory"),
            mo.ui.table(_inv_df),
        ])

    _display
    return (hifld_gdfs,)


@app.cell
def _(Path, cfg, hifld_gdfs, mo, pd):
    """Compare HIFLD bronze source vs gold output to QA coverage and OSM match rates."""
    import uuid as _uuid

    _bronze_hifld = Path(cfg.storage.bronze_path) / "hifld"

    if not _bronze_hifld.exists() or not hifld_gdfs:
        _snap_display = mo.vstack([
            mo.md("### HIFLD Source vs Gold Comparison"),
            mo.callout(
                mo.md("Bronze HIFLD not found or no gold layers loaded. Run Flow 01 and Flow 04 first."),
                kind="neutral",
            ),
        ])
    else:
        _HIFLD_NS = _uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")

        _cov_rows = []
        _dropped_tables: dict[str, pd.DataFrame] = {}
        _osm_new_tables: dict[str, pd.DataFrame] = {}

        for _lname, _gold_gdf in hifld_gdfs.items():
            _bronze_file = _bronze_hifld / f"{_lname}.parquet"
            _layer_def = cfg.hifld.layers.get(_lname)
            if not _bronze_file.exists() or _layer_def is None:
                _cov_rows.append({"hifld_layer": _lname, "bronze_count": None,
                                   "gold_count": len(_gold_gdf), "dropped": None,
                                   "osm_matched": None, "osm_match_pct": None})
                continue

            try:
                _bronze_df = pd.read_parquet(_bronze_file)
            except Exception:
                _cov_rows.append({"hifld_layer": _lname, "bronze_count": "error",
                                   "gold_count": len(_gold_gdf), "dropped": None,
                                   "osm_matched": None, "osm_match_pct": None})
                continue

            _bronze_n = len(_bronze_df)
            _gold_n = len(_gold_gdf)
            _dropped_n = _bronze_n - _gold_n  # records lost due to invalid coords

            _osm_matched = int(_gold_gdf["osm_lifeline_id"].notna().sum()) if "osm_lifeline_id" in _gold_gdf.columns else 0
            _match_pct = round(100 * _osm_matched / _gold_n, 1) if _gold_n > 0 else 0.0

            # Identify which bronze records were dropped (present in bronze, missing from gold)
            _id_field = _layer_def.id_field
            if _id_field in _bronze_df.columns and "lifeline_id" in _gold_gdf.columns:
                _expected_ids = {
                    str(_uuid.uuid5(_HIFLD_NS, f"hifld/{_lname}/{v}"))
                    for v in _bronze_df[_id_field].dropna().astype(str)
                }
                _gold_ids = set(_gold_gdf["lifeline_id"].dropna())
                _missing_ids = _expected_ids - _gold_ids
                if _missing_ids:
                    # Map back to bronze rows (approximate: match via re-deriving id)
                    _bronze_df["_expected_lifeline_id"] = _bronze_df[_id_field].apply(
                        lambda v: str(_uuid.uuid5(_HIFLD_NS, f"hifld/{_lname}/{v}")) if pd.notna(v) else None
                    )
                    _dropped_rows = _bronze_df[_bronze_df["_expected_lifeline_id"].isin(_missing_ids)]
                    _show_cols = [
                        c for c in [_id_field] + [
                            col for col in _bronze_df.columns
                            if col not in ("geometry", "_expected_lifeline_id", "bbox", "bpd_metadata", "type", "properties")
                        ][:6]
                        if c in _bronze_df.columns
                    ]
                    _dropped_tables[_lname] = _dropped_rows[_show_cols].head(100).reset_index(drop=True)

            # OSM-matched gold records (new matches since last look at source)
            if "osm_lifeline_id" in _gold_gdf.columns:
                _osm_new = _gold_gdf[_gold_gdf["osm_lifeline_id"].notna()][
                    [c for c in ["lifeline_id", "display_name", "osm_lifeline_id", "source_provenance", "confidence_score"]
                     if c in _gold_gdf.columns]
                ].head(100).reset_index(drop=True)
                if len(_osm_new) > 0:
                    _osm_new_tables[_lname] = _osm_new

            _cov_rows.append({
                "hifld_layer": _lname,
                "bronze_count": _bronze_n,
                "gold_count": _gold_n,
                "dropped_invalid_coords": _dropped_n,
                "osm_matched": _osm_matched,
                "osm_match_pct": _match_pct,
            })

        _cov_df = pd.DataFrame(_cov_rows)
        _detail_widgets = [
            mo.md("### HIFLD Source vs Gold Comparison"),
            mo.md(
                "Compares raw bronze HIFLD source (`bronze/hifld/`) against generated gold layers (`gold/hifld_*.parquet`).  \n"
                "**Dropped** = bronze records excluded from gold due to missing/invalid coordinates.  \n"
                "**OSM matched** = gold records linked to an OSM silver point within the proximity threshold."
            ),
            mo.ui.table(_cov_df),
        ]

        for _lname in sorted(_dropped_tables):
            _detail_widgets += [
                mo.md(f"#### `{_lname}` — Dropped Records (invalid coordinates, first 100)"),
                mo.ui.table(_dropped_tables[_lname]),
            ]

        for _lname in sorted(_osm_new_tables):
            _detail_widgets += [
                mo.md(f"#### `{_lname}` — OSM-Matched HIFLD Records (first 100)"),
                mo.ui.table(_osm_new_tables[_lname]),
            ]

        _snap_display = mo.vstack(_detail_widgets)

    _snap_display
    return


if __name__ == "__main__":
    app.run()
