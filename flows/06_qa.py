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
        hifld_snapshot_path: str = Field(default="", description="Previous HIFLD gold folder for change detection (empty = skip)")

    return (FlowParams,)


@app.cell
def _(mo):
    params_form = (
        mo.md("""
        ## Parameters

        Config file: {config_path}

        Layer filter (blank = all layers): {layer_filter}

        Previous HIFLD snapshot path (blank = skip comparison): {hifld_snapshot_path}
        """)
        .batch(
            config_path=mo.ui.text(value="config.lifeline.yaml", label="Config path"),
            layer_filter=mo.ui.text(value="", placeholder="e.g. power", label=""),
            hifld_snapshot_path=mo.ui.text(value="", placeholder="e.g. E:/lifelinepois/data/gold_snapshot", label=""),
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

    mo.hstack([mo.altair_chart(score_hist), mo.altair_chart(tier_bar)])
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
        mo.altair_chart(prov_bar),
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
        mo.altair_chart(fema_bar),
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
def _(Path, flow_params, gpd, hifld_gdfs, mo, pd):
    _snap_path_str = flow_params.hifld_snapshot_path.strip()
    if not _snap_path_str or not hifld_gdfs:
        _snap_display = mo.vstack([
            mo.md("### HIFLD Snapshot Comparison"),
            mo.callout(
                mo.md(
                    "No snapshot path provided. Fill in **Previous HIFLD snapshot path** above "
                    "to compare current gold against a prior run."
                ),
                kind="neutral",
            ),
        ])
    else:
        _snap_path = Path(_snap_path_str)
        if not _snap_path.exists():
            _snap_display = mo.vstack([
                mo.md("### HIFLD Snapshot Comparison"),
                mo.callout(mo.md(f"⚠️ Snapshot path not found: `{_snap_path}`"), kind="warn"),
            ])
        else:
            _NATIVE_COMPARE_FIELDS = ["display_name"]
            _OSM_LINK_FIELDS = ["osm_lifeline_id", "source_provenance", "confidence_score"]

            _diff_rows = []
            _add_tables: dict[str, pd.DataFrame] = {}
            _del_tables: dict[str, pd.DataFrame] = {}
            _upd_native_tables: dict[str, pd.DataFrame] = {}
            _upd_osm_tables: dict[str, pd.DataFrame] = {}

            for _lname, _curr_gdf in hifld_gdfs.items():
                _snap_file = _snap_path / f"hifld_{_lname}.parquet"
                if not _snap_file.exists():
                    _diff_rows.append({"hifld_layer": _lname, "status": "snapshot missing",
                                       "adds": None, "deletes": None,
                                       "native_updates": None, "osm_link_changes": None})
                    continue

                try:
                    _prev_gdf = gpd.read_parquet(_snap_file)
                except Exception as _e:
                    _diff_rows.append({"hifld_layer": _lname, "status": f"error: {_e}",
                                       "adds": None, "deletes": None,
                                       "native_updates": None, "osm_link_changes": None})
                    continue

                _curr_ids = set(_curr_gdf["lifeline_id"].dropna())
                _prev_ids = set(_prev_gdf["lifeline_id"].dropna())
                _adds = _curr_ids - _prev_ids
                _deletes = _prev_ids - _curr_ids
                _common = _curr_ids & _prev_ids

                _curr_idx = _curr_gdf.set_index("lifeline_id")
                _prev_idx = _prev_gdf.set_index("lifeline_id")

                _native_changed = []
                _osm_changed = []
                for _lid in _common:
                    _c_row = _curr_idx.loc[_lid]
                    _p_row = _prev_idx.loc[_lid]
                    _nd = {}
                    for _field in _NATIVE_COMPARE_FIELDS:
                        if _field in _c_row.index and _field in _p_row.index:
                            if str(_c_row[_field]) != str(_p_row[_field]):
                                _nd[f"{_field}_prev"] = _p_row[_field]
                                _nd[f"{_field}_curr"] = _c_row[_field]
                    if _nd:
                        _nd["lifeline_id"] = _lid
                        _native_changed.append(_nd)

                    _od = {}
                    for _field in _OSM_LINK_FIELDS:
                        if _field in _c_row.index and _field in _p_row.index:
                            if str(_c_row[_field]) != str(_p_row[_field]):
                                _od[f"{_field}_prev"] = _p_row[_field]
                                _od[f"{_field}_curr"] = _c_row[_field]
                    if _od:
                        _od["lifeline_id"] = _lid
                        _osm_changed.append(_od)

                _geom_changed = 0
                if "geometry" in _curr_gdf.columns and "geometry" in _prev_gdf.columns:
                    for _lid in _common:
                        _cg = _curr_idx.loc[_lid, "geometry"]
                        _pg = _prev_idx.loc[_lid, "geometry"]
                        if _cg is not None and _pg is not None and not _cg.equals(_pg):
                            _geom_changed += 1

                if _native_changed:
                    _upd_native_tables[_lname] = pd.DataFrame(_native_changed)
                if _osm_changed:
                    _upd_osm_tables[_lname] = pd.DataFrame(_osm_changed)
                if _adds:
                    _add_tables[_lname] = _curr_gdf[_curr_gdf["lifeline_id"].isin(_adds)][
                        [c for c in ["lifeline_id", "display_name", "confidence_score", "source_provenance"] if c in _curr_gdf.columns]
                    ].reset_index(drop=True)
                if _deletes:
                    _del_tables[_lname] = _prev_gdf[_prev_gdf["lifeline_id"].isin(_deletes)][
                        [c for c in ["lifeline_id", "display_name", "confidence_score", "source_provenance"] if c in _prev_gdf.columns]
                    ].reset_index(drop=True)

                _diff_rows.append({
                    "hifld_layer": _lname, "status": "compared",
                    "current_rows": len(_curr_gdf), "snapshot_rows": len(_prev_gdf),
                    "adds": len(_adds), "deletes": len(_deletes),
                    "native_updates": len(_native_changed),
                    "geom_changes": _geom_changed,
                    "osm_link_changes": len(_osm_changed),
                })

            _diff_df = pd.DataFrame(_diff_rows)
            _detail_widgets = [
                mo.md("### HIFLD Snapshot Comparison"),
                mo.md(
                    "**Native updates** = authoritative field changes (display name, etc.)  \n"
                    "**OSM-link changes** = `osm_lifeline_id` / `source_provenance` / `confidence_score` "
                    "changes driven by OSM pipeline runs (not HIFLD source changes)."
                ),
                mo.ui.table(_diff_df),
            ]
            for _lname in sorted(set(list(_add_tables) + list(_del_tables) + list(_upd_native_tables) + list(_upd_osm_tables))):
                _detail_widgets.append(mo.md(f"#### `hifld_{_lname}` details"))
                if _lname in _add_tables:
                    _detail_widgets += [mo.md(f"**Adds ({len(_add_tables[_lname])})**"), mo.ui.table(_add_tables[_lname])]
                if _lname in _del_tables:
                    _detail_widgets += [mo.md(f"**Deletes ({len(_del_tables[_lname])})**"), mo.ui.table(_del_tables[_lname])]
                if _lname in _upd_native_tables:
                    _detail_widgets += [mo.md(f"**Native attribute updates ({len(_upd_native_tables[_lname])})**"), mo.ui.table(_upd_native_tables[_lname])]
                if _lname in _upd_osm_tables:
                    _detail_widgets += [mo.md(f"**OSM-link changes ({len(_upd_osm_tables[_lname])})**"), mo.ui.table(_upd_osm_tables[_lname])]

            _snap_display = mo.vstack(_detail_widgets)

    _snap_display
    return


if __name__ == "__main__":
    app.run()
