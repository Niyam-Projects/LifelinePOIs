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
    if is_script_mode and "help" in mo.cli_args():
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
            has_fema=master["fema_lifeline"].apply(
                lambda x: isinstance(x, dict) and x.get("primary") is not None
            ),
        )
        .groupby(["tmp_osm_layer", "has_fema"])
        .agg(count=("lifeline_id", "count"))
        .reset_index()
        .rename(columns={"tmp_osm_layer": "layer"})
    )
    _fema_df["label"] = _fema_df["has_fema"].map({True: "FEMA lifeline assigned", False: "Unclassified"})

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
                scale=alt.Scale(domain=["FEMA lifeline assigned", "Unclassified"], range=["#2ca02c", "#d62728"]),
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
            "fema_lifeline",
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

    # Records with no FEMA lifeline but OSM source (expected to have tag matches)
    if "fema_lifeline" in master.columns:
        _osm_no_fema = master[
            master["source_provenance"].fillna("").str.startswith("osm")
            & ~master["fema_lifeline"].apply(
                lambda x: isinstance(x, dict) and x.get("primary") is not None
            )
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
        "fema_lifeline",
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
        "fema_lifeline", "h3_index",
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
            mo.md("### HIFLD Gold Layer QA"),
            mo.callout(
                mo.md("Bronze HIFLD not found or no gold layers loaded. Run Flow 01 and Flow 04 first."),
                kind="neutral",
            ),
        ])
    else:
        _HIFLD_NS = _uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")

        _cov_rows = []
        _dropped_tables: dict[str, pd.DataFrame] = {}
        _osm_detail_tables: dict[str, pd.DataFrame] = {}

        for _lname, _gold_gdf in hifld_gdfs.items():
            _bronze_file = _bronze_hifld / f"{_lname}.parquet"
            _layer_def = cfg.hifld.layers.get(_lname)
            _gold_n = len(_gold_gdf)

            # Detect OSM-sourced gold layers (produced by pipeline, not raw HIFLD dump)
            _is_osm_sourced = (
                "source_provenance" in _gold_gdf.columns
                and _gold_gdf["source_provenance"].str.startswith("osm", na=False).any()
            )

            if _is_osm_sourced:
                # OSM-sourced layer: show pipeline stats instead of bronze-vs-gold ID comparison
                _hifld_boosted = int(_gold_gdf["source_provenance"].str.contains("hifld", na=False).sum())
                _boost_pct = round(100 * _hifld_boosted / _gold_n, 1) if _gold_n > 0 else 0.0
                _bronze_n = len(pd.read_parquet(_bronze_file)) if _bronze_file.exists() else None
                _cov_rows.append({
                    "hifld_layer": _lname,
                    "source": "OSM pipeline (campus collapse + HIFLD boost)",
                    "bronze_count": _bronze_n,
                    "gold_count": _gold_n,
                    "hifld_boosted": _hifld_boosted,
                    "hifld_boost_pct": _boost_pct,
                })
                # Show sample of HIFLD-boosted records
                if "source_provenance" in _gold_gdf.columns:
                    _boosted_rows = _gold_gdf[_gold_gdf["source_provenance"].str.contains("hifld", na=False)][
                        [c for c in ["lifeline_id", "display_name", "source_provenance", "confidence_score", "confidence_tier"]
                         if c in _gold_gdf.columns]
                    ].head(100).reset_index(drop=True)
                    if len(_boosted_rows) > 0:
                        _osm_detail_tables[_lname] = _boosted_rows
                continue

            # Raw-HIFLD-sourced gold layer: original bronze-vs-gold comparison
            if not _bronze_file.exists() or _layer_def is None:
                _cov_rows.append({"hifld_layer": _lname, "source": "HIFLD bronze", "bronze_count": None,
                                   "gold_count": _gold_n, "hifld_boosted": None, "hifld_boost_pct": None})
                continue

            try:
                _bronze_df = pd.read_parquet(_bronze_file)
            except Exception:
                _cov_rows.append({"hifld_layer": _lname, "source": "HIFLD bronze", "bronze_count": "error",
                                   "gold_count": _gold_n, "hifld_boosted": None, "hifld_boost_pct": None})
                continue

            _bronze_n = len(_bronze_df)
            _osm_matched = int(_gold_gdf["osm_lifeline_id"].notna().sum()) if "osm_lifeline_id" in _gold_gdf.columns else 0
            _match_pct = round(100 * _osm_matched / _gold_n, 1) if _gold_n > 0 else 0.0

            # Identify which bronze records were dropped (invalid coords)
            _id_field = _layer_def.id_field
            if _id_field in _bronze_df.columns and "lifeline_id" in _gold_gdf.columns:
                _expected_ids = {
                    str(_uuid.uuid5(_HIFLD_NS, f"hifld/{_lname}/{v}"))
                    for v in _bronze_df[_id_field].dropna().astype(str)
                }
                _gold_ids = set(_gold_gdf["lifeline_id"].dropna())
                _missing_ids = _expected_ids - _gold_ids
                if _missing_ids:
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

            if "osm_lifeline_id" in _gold_gdf.columns:
                _osm_new = _gold_gdf[_gold_gdf["osm_lifeline_id"].notna()][
                    [c for c in ["lifeline_id", "display_name", "osm_lifeline_id", "source_provenance", "confidence_score"]
                     if c in _gold_gdf.columns]
                ].head(100).reset_index(drop=True)
                if len(_osm_new) > 0:
                    _osm_detail_tables[_lname] = _osm_new

            _cov_rows.append({
                "hifld_layer": _lname,
                "source": "HIFLD bronze",
                "bronze_count": _bronze_n,
                "gold_count": _gold_n,
                "hifld_boosted": _osm_matched,
                "hifld_boost_pct": _match_pct,
            })

        _cov_df = pd.DataFrame(_cov_rows)
        _detail_widgets = [
            mo.md("### HIFLD Gold Layer QA"),
            mo.md(
                "**OSM pipeline layers** (e.g. `hospitals`): gold is derived from OSM+campus collapse+HIFLD boost — "
                "`hifld_boost_pct` = % of OSM records confirmed by a nearby HIFLD point.  \n"
                "**HIFLD bronze layers** (e.g. `cellular`, `microwave`): gold is derived from raw HIFLD bronze — "
                "`hifld_boost_pct` = % of HIFLD records matched to an OSM silver point."
            ),
            mo.ui.table(_cov_df),
        ]

        for _lname in sorted(_dropped_tables):
            _detail_widgets += [
                mo.md(f"#### `{_lname}` — Dropped HIFLD Bronze Records (invalid coordinates, first 100)"),
                mo.ui.table(_dropped_tables[_lname]),
            ]

        for _lname in sorted(_osm_detail_tables):
            _detail_widgets += [
                mo.md(f"#### `{_lname}` — HIFLD-Confirmed Records (first 100)"),
                mo.ui.table(_osm_detail_tables[_lname]),
            ]

        _snap_display = mo.vstack(_detail_widgets)

    _snap_display
    return


@app.cell
def _(Path, cfg, mo, pd):
    mo.stop(cfg is None, mo.callout(mo.md("Config not loaded."), kind="warn"))

    _gold_path = Path(cfg.storage.gold_path)
    _hifld_file = _gold_path / "hifld_hospitals.parquet"
    _wide_file = _gold_path / "wide_hospitals.parquet"

    def _first_col(_df, _names):
        for _name in _names:
            if _name in _df.columns:
                return _df[_name]
        return pd.Series(pd.NA, index=_df.index, dtype="object")

    def _filled(_series):
        _text = _series.astype("string").str.strip()
        return _series.notna() & _text.ne("") & _text.str.upper().ne("NOT AVAILABLE")

    if not _hifld_file.exists() or not _wide_file.exists():
        _missing = [
            str(_path.relative_to(_gold_path.parent))
            for _path in (_hifld_file, _wide_file)
            if not _path.exists()
        ]
        _hosp_summary_display = mo.vstack([
            mo.md("## Hospital BEDS/TRAUMA Attribute QA"),
            mo.callout(
                mo.md(
                    "Missing hospital gold outputs: "
                    + ", ".join(f"`{_path}`" for _path in _missing)
                    + ". Run `flows/04_gold_production.py` first."
                ),
                kind="warn",
            ),
        ])
    else:
        _ = pd.read_parquet(_hifld_file)
        _wide = pd.read_parquet(_wide_file)

        _beds = _first_col(_wide, ["BEDS", "beds", "cms_bed_cnt"])
        _trauma = _first_col(_wide, ["TRAUMA", "acs_trauma_level", "hifld_trauma"])
        _source = _first_col(_wide, ["source_provenance", "SOURCE"]).fillna("unknown")
        _source = _source.astype("string").str.strip().replace("", "unknown")

        _beds_filled = _filled(_beds)
        _trauma_filled = _filled(_trauma)
        _both_filled = _beds_filled & _trauma_filled

        _source_summary = (
            pd.DataFrame(
                {
                    "source_provenance": _source,
                    "beds_filled": _beds_filled,
                    "trauma_filled": _trauma_filled,
                    "both_filled": _both_filled,
                }
            )
            .groupby("source_provenance", dropna=False)
            .agg(
                hospitals=("beds_filled", "size"),
                beds_filled=("beds_filled", "sum"),
                trauma_filled=("trauma_filled", "sum"),
                both_filled=("both_filled", "sum"),
            )
            .reset_index()
        )
        _source_summary["beds_fill_rate_pct"] = (
            (100 * _source_summary["beds_filled"] / _source_summary["hospitals"]).round(1)
        )
        _source_summary["trauma_fill_rate_pct"] = (
            (100 * _source_summary["trauma_filled"] / _source_summary["hospitals"]).round(1)
        )

        _stats = mo.hstack([
            mo.stat(f"{len(_wide):,}", label="Total hospitals", bordered=True),
            mo.stat(f"{int(_beds_filled.sum()):,}", label="BEDS filled", bordered=True),
            mo.stat(f"{int(_trauma_filled.sum()):,}", label="TRAUMA filled", bordered=True),
            mo.stat(f"{int(_both_filled.sum()):,}", label="Both filled", bordered=True),
        ])

        _hosp_summary_display = mo.vstack([
            mo.md("## Hospital BEDS/TRAUMA Attribute QA"),
            _stats,
            mo.ui.table(_source_summary, label="Fill rates by source provenance"),
        ])

    _hosp_summary_display
    return


@app.cell
def _(Path, cfg, mo, pd):
    mo.stop(cfg is None, mo.callout(mo.md("Config not loaded."), kind="warn"))

    _bronze_file = Path(cfg.storage.bronze_path) / "hifld" / "hospitals.parquet"
    _silver_file = Path(cfg.storage.silver_path) / "attr_health_hifld_attrs.parquet"
    _wide_file = Path(cfg.storage.gold_path) / "wide_hospitals.parquet"

    def _first_col(_df, _names):
        for _name in _names:
            if _name in _df.columns:
                return _df[_name]
        return pd.Series(pd.NA, index=_df.index, dtype="object")

    def _filled(_series):
        _text = _series.astype("string").str.strip()
        return _series.notna() & _text.ne("") & _text.str.upper().ne("NOT AVAILABLE")

    def _coalesce_text(_df, _names):
        _result = pd.Series(pd.NA, index=_df.index, dtype="object")
        for _name in _names:
            if _name not in _df.columns:
                continue
            _series = _df[_name]
            _mask = _result.isna() & _filled(_series)
            _result = _result.where(~_mask, _series)
        return _result

    _missing = [
        str(_path)
        for _path in (_bronze_file, _silver_file, _wide_file)
        if not _path.exists()
    ]
    if _missing:
        _bronze_gold_display = mo.vstack([
            mo.md("### Bronze vs Gold BEDS/TRAUMA Comparison"),
            mo.callout(
                mo.md(
                    "Missing inputs: "
                    + ", ".join(f"`{_path}`" for _path in _missing)
                    + ". Run Flows 01, 02, and 04 first."
                ),
                kind="warn",
            ),
        ])
    else:
        from lib.hifld_hospital_attrs import _normalize_name, load_hifld_hospitals

        try:
            from rapidfuzz import fuzz
        except ImportError:
            fuzz = None

        _bronze = pd.read_parquet(_bronze_file)
        _silver = pd.read_parquet(_silver_file)
        _wide = pd.read_parquet(_wide_file)

        _final_beds = _coalesce_text(_wide, ["BEDS", "beds"])
        if "cms_bed_cnt" in _wide.columns:
            _cms_beds = pd.to_numeric(_wide["cms_bed_cnt"], errors="coerce")
            _final_beds = _final_beds.where(_filled(_final_beds), _cms_beds)
        _final_trauma = _coalesce_text(_wide, ["TRAUMA", "acs_trauma_level", "hifld_trauma"])

        _comparison = pd.DataFrame(
            [
                {"metric": "rows_loaded", "bronze_hifld": len(_bronze), "silver_hifld_attrs": len(_silver), "gold_wide_hospitals": len(_wide)},
                {"metric": "beds_available", "bronze_hifld": int(_filled(_bronze.get("BEDS", pd.Series(pd.NA, index=_bronze.index))).sum()), "silver_hifld_attrs": pd.NA, "gold_wide_hospitals": int(_filled(_final_beds).sum())},
                {"metric": "trauma_available", "bronze_hifld": int(_filled(_bronze.get("TRAUMA", pd.Series(pd.NA, index=_bronze.index))).sum()), "silver_hifld_attrs": int(_filled(_silver.get("hifld_trauma", pd.Series(pd.NA, index=_silver.index))).sum()), "gold_wide_hospitals": int(_filled(_final_trauma).sum())},
                {"metric": "cms_bed_cnt_gt0", "bronze_hifld": pd.NA, "silver_hifld_attrs": pd.NA, "gold_wide_hospitals": int((pd.to_numeric(_wide.get("cms_bed_cnt", pd.Series(dtype="float64")), errors="coerce") > 0).sum())},
                {"metric": "hifld_trauma_filled", "bronze_hifld": pd.NA, "silver_hifld_attrs": int(_filled(_silver.get("hifld_trauma", pd.Series(pd.NA, index=_silver.index))).sum()), "gold_wide_hospitals": int(_filled(_wide.get("hifld_trauma", pd.Series(pd.NA, index=_wide.index))).sum())},
                {"metric": "acs_trauma_level_filled", "bronze_hifld": pd.NA, "silver_hifld_attrs": pd.NA, "gold_wide_hospitals": int(_filled(_wide.get("acs_trauma_level", pd.Series(pd.NA, index=_wide.index))).sum())},
            ]
        )

        _wide_detail = _wide.copy()
        _wide_detail["final_BEDS"] = _final_beds
        _wide_detail["final_TRAUMA"] = _final_trauma

        _silver_detail = _silver.rename(columns={"hifld_trauma": "silver_hifld_trauma"})
        _trauma_join = _silver_detail.merge(_wide_detail, on="lifeline_id", how="inner")
        _trauma_missing = _trauma_join[
            _filled(_trauma_join.get("silver_hifld_trauma", pd.Series(pd.NA, index=_trauma_join.index)))
            & ~_filled(_trauma_join["final_TRAUMA"])
        ].copy()
        _trauma_cols = [
            _col
            for _col in [
                "lifeline_id",
                "name",
                "display_name",
                "addr:state",
                "addr:postcode",
                "silver_hifld_trauma",
                "hifld_trauma",
                "acs_trauma_level",
                "final_TRAUMA",
                "source_provenance",
            ]
            if _col in _trauma_missing.columns
        ]
        _trauma_missing = _trauma_missing[_trauma_cols].reset_index(drop=True)

        _beds_missing = pd.DataFrame(columns=[
            "lifeline_id",
            "bronze_NAME",
            "bronze_BEDS",
            "name",
            "display_name",
            "addr:state",
            "addr:postcode",
            "final_BEDS",
            "source_provenance",
            "match_score",
        ])
        _detail_notes = []

        if fuzz is None:
            _detail_notes.append(
                mo.callout(
                    mo.md("`rapidfuzz` is unavailable, so bronze-to-gold BEDS detail matching was skipped."),
                    kind="warn",
                )
            )
        elif "lifeline_id" not in _wide.columns:
            _detail_notes.append(
                mo.callout(mo.md("`lifeline_id` is missing from `gold/wide_hospitals.parquet`."), kind="warn")
            )
        else:
            _bronze_match = load_hifld_hospitals(Path(cfg.storage.bronze_path))
            _wide_match = _wide.copy()
            for _col in ("addr:state", "addr:postcode", "addr:city", "name", "display_name"):
                if _col not in _wide_match.columns:
                    _wide_match[_col] = ""
                else:
                    _wide_match[_col] = _wide_match[_col].fillna("").astype(str).str.strip()
            _wide_match["_state"] = _wide_match["addr:state"].str.upper().str[:2]
            _wide_match["_zip5"] = _wide_match["addr:postcode"].str[:5]
            _wide_match["_city"] = _wide_match["addr:city"].str.upper()
            _wide_match["_name_norm"] = _wide_match["name"].apply(_normalize_name)
            _empty_name = _wide_match["_name_norm"] == ""
            _wide_match.loc[_empty_name, "_name_norm"] = _wide_match.loc[_empty_name, "display_name"].apply(_normalize_name)
            _wide_match = _wide_match[_wide_match["_name_norm"] != ""].copy()

            _match_rows = []
            for _, _row in _bronze_match.iterrows():
                _state = str(_row.get("_state", "") or "").strip()
                _zip5 = str(_row.get("_zip5", "") or "").strip()
                _city = str(_row.get("_city_norm", "") or "").strip()
                _name = str(_row.get("_name_norm", "") or "").strip()
                if not _state or not _name:
                    continue

                _candidates = _wide_match[_wide_match["_state"] == _state]
                if len(_candidates) == 0:
                    continue
                if _zip5:
                    _zip_candidates = _candidates[_candidates["_zip5"] == _zip5]
                    if len(_zip_candidates) > 0:
                        _candidates = _zip_candidates
                    elif _city:
                        _city_candidates = _candidates[_candidates["_city"] == _city]
                        if len(_city_candidates) > 0:
                            _candidates = _city_candidates
                elif _city:
                    _city_candidates = _candidates[_candidates["_city"] == _city]
                    if len(_city_candidates) > 0:
                        _candidates = _city_candidates

                if len(_candidates) == 0:
                    continue

                _scores = _candidates["_name_norm"].map(
                    lambda _candidate_name: fuzz.token_sort_ratio(_name, _candidate_name) / 100.0
                )
                _best_idx = _scores.astype(float).idxmax()
                _best_score = float(_scores.loc[_best_idx])
                if _best_score < 0.80:
                    continue

                _match_rows.append(
                    {
                        "lifeline_id": _wide_match.at[_best_idx, "lifeline_id"],
                        "bronze_NAME": _row.get("NAME"),
                        "bronze_BEDS": _row.get("BEDS"),
                        "bronze_TRAUMA": _row.get("TRAUMA"),
                        "match_score": round(_best_score, 3),
                    }
                )

            if _match_rows:
                _matched = pd.DataFrame(_match_rows)
                _matched = (
                    _matched.sort_values("match_score", ascending=False)
                    .drop_duplicates("lifeline_id")
                    .reset_index(drop=True)
                )
                _beds_join = _matched.merge(_wide_detail, on="lifeline_id", how="inner")
                _beds_missing = _beds_join[
                    _filled(_beds_join.get("bronze_BEDS", pd.Series(pd.NA, index=_beds_join.index)))
                    & ~_filled(_beds_join["final_BEDS"])
                ].copy()
                _beds_cols = [
                    _col
                    for _col in [
                        "lifeline_id",
                        "bronze_NAME",
                        "bronze_BEDS",
                        "name",
                        "display_name",
                        "addr:state",
                        "addr:postcode",
                        "final_BEDS",
                        "source_provenance",
                        "match_score",
                    ]
                    if _col in _beds_missing.columns
                ]
                _beds_missing = _beds_missing[_beds_cols].reset_index(drop=True)

        _bronze_gold_display = mo.vstack([
            mo.md("### Bronze vs Gold BEDS/TRAUMA Comparison"),
            mo.ui.table(_comparison, label="Aggregate comparison counts"),
            *_detail_notes,
            mo.md("#### Matched records with HIFLD trauma present but final TRAUMA null"),
            mo.ui.table(_trauma_missing, label="TRAUMA gaps"),
            mo.md("#### Matched bronze hospitals with BEDS present but final BEDS null"),
            mo.ui.table(_beds_missing, label="BEDS gaps"),
        ])

    _bronze_gold_display
    return


@app.cell
def _(mo):
    hosp_reason_dd = mo.ui.dropdown(
        options=[
            "All",
            "missing_address",
            "no_silver_in_state",
            "no_zip_city_overlap",
            "name_below_threshold",
            "no_name",
        ],
        value="All",
        label="Match failure reason",
        full_width=True,
    )
    return (hosp_reason_dd,)


@app.cell
def _(Path, cfg, hosp_reason_dd, mo, pd):
    mo.stop(cfg is None, mo.callout(mo.md("Config not loaded."), kind="warn"))

    from lib.hifld_hospital_attrs import _normalize_name, load_hifld_hospitals

    _silver_file = Path(cfg.storage.silver_path) / "lifeline_points.parquet"
    _bronze_file = Path(cfg.storage.bronze_path) / "hifld" / "hospitals.parquet"

    if not _silver_file.exists() or not _bronze_file.exists():
        _missing = [
            str(_path)
            for _path in (_silver_file, _bronze_file)
            if not _path.exists()
        ]
        _match_diag_display = mo.vstack([
            mo.md("### Match Failure Diagnostics"),
            mo.callout(
                mo.md(
                    "Missing inputs: "
                    + ", ".join(f"`{_path}`" for _path in _missing)
                    + ". Run Flows 01 and 02 first."
                ),
                kind="warn",
            ),
        ])
    else:
        try:
            from rapidfuzz import fuzz
        except ImportError:
            fuzz = None

        if fuzz is None:
            _match_diag_display = mo.vstack([
                mo.md("### Match Failure Diagnostics"),
                mo.callout(
                    mo.md("`rapidfuzz` is required for match diagnostics."),
                    kind="warn",
                ),
            ])
        else:
            _silver = pd.read_parquet(_silver_file)
            if "tmp_osm_layer" in _silver.columns:
                _silver = _silver[_silver["tmp_osm_layer"] == "health"].copy()
            _bronze = load_hifld_hospitals(Path(cfg.storage.bronze_path))

            for _col in ("addr:state", "addr:postcode", "addr:city", "name", "display_name"):
                if _col not in _silver.columns:
                    _silver[_col] = ""
                else:
                    _silver[_col] = _silver[_col].fillna("").astype(str).str.strip()

            _silver["_state"] = _silver["addr:state"].str.upper().str[:2]
            _silver["_zip5"] = _silver["addr:postcode"].str[:5]
            _silver["_city"] = _silver["addr:city"].str.upper()
            _silver["_name_norm"] = _silver["name"].apply(_normalize_name)
            _silver_empty_name = _silver["_name_norm"] == ""
            _silver.loc[_silver_empty_name, "_name_norm"] = _silver.loc[_silver_empty_name, "display_name"].apply(_normalize_name)
            _silver = _silver[_silver["_name_norm"] != ""].copy()

            _reason_rows = []
            for _, _row in _bronze.iterrows():
                _name = str(_row.get("_name_norm", "") or "").strip()
                _state = str(_row.get("_state", "") or "").strip()
                _city = str(_row.get("_city_norm", "") or "").strip()
                _zip5 = str(_row.get("_zip5", "") or "").strip()

                if not _name:
                    _reason = "no_name"
                elif not _state or (not _zip5 and not _city):
                    _reason = "missing_address"
                else:
                    _state_matches = _silver[_silver["_state"] == _state]
                    if len(_state_matches) == 0:
                        _reason = "no_silver_in_state"
                    else:
                        _candidates = pd.DataFrame(columns=_state_matches.columns)
                        _zip_matches = pd.DataFrame(columns=_state_matches.columns)
                        _city_matches = pd.DataFrame(columns=_state_matches.columns)
                        if _zip5:
                            _zip_matches = _state_matches[_state_matches["_zip5"] == _zip5]
                            if len(_zip_matches) > 0:
                                _candidates = _zip_matches
                        if len(_candidates) == 0 and _city:
                            _city_matches = _state_matches[_state_matches["_city"] == _city]
                            if len(_city_matches) > 0:
                                _candidates = _city_matches
                        if len(_candidates) == 0 and not _zip5 and not _city:
                            _candidates = _state_matches

                        if len(_candidates) == 0:
                            _reason = "no_zip_city_overlap"
                        else:
                            _best_score = max(
                                fuzz.token_sort_ratio(_name, _candidate_name) / 100.0
                                for _candidate_name in _candidates["_name_norm"].fillna("")
                            )
                            _reason = "name_below_threshold" if _best_score < 0.80 else None

                if _reason is not None:
                    _reason_rows.append(
                        {
                            "NAME": _row.get("NAME"),
                            "STATE": _row.get("STATE"),
                            "CITY": _row.get("CITY"),
                            "ZIP": _row.get("ZIP"),
                            "BEDS": _row.get("BEDS"),
                            "TRAUMA": _row.get("TRAUMA"),
                            "reason": _reason,
                        }
                    )

            _diag_df = pd.DataFrame(
                _reason_rows,
                columns=["NAME", "STATE", "CITY", "ZIP", "BEDS", "TRAUMA", "reason"],
            )
            _reason_counts = (
                _diag_df.groupby("reason", dropna=False)
                .size()
                .rename("count")
                .reset_index()
                .sort_values(["count", "reason"], ascending=[False, True])
            )
            if hosp_reason_dd.value != "All":
                _filtered_diag = _diag_df[_diag_df["reason"] == hosp_reason_dd.value].reset_index(drop=True)
            else:
                _filtered_diag = _diag_df.reset_index(drop=True)

            _match_diag_display = mo.vstack([
                mo.md("### Match Failure Diagnostics"),
                hosp_reason_dd,
                mo.ui.table(_reason_counts, label="Failure counts by reason", page_size=10),
                mo.ui.table(_filtered_diag, label="Filtered unmatched bronze hospitals"),
            ])

    _match_diag_display
    return


@app.cell
def _(mo):
    hosp_coverage_filter_dd = mo.ui.dropdown(
        options=["All", "Missing BEDS", "Missing TRAUMA", "Missing both"],
        value="All",
        label="Coverage filter",
        full_width=True,
    )
    return (hosp_coverage_filter_dd,)


@app.cell
def _(Path, cfg, hosp_coverage_filter_dd, mo, pd):
    mo.stop(cfg is None, mo.callout(mo.md("Config not loaded."), kind="warn"))

    _wide_file = Path(cfg.storage.gold_path) / "wide_hospitals.parquet"

    def _filled(_series):
        _text = _series.astype("string").str.strip()
        return _series.notna() & _text.ne("") & _text.str.upper().ne("NOT AVAILABLE")

    def _coalesce_text(_df, _names):
        _result = pd.Series(pd.NA, index=_df.index, dtype="object")
        for _name in _names:
            if _name not in _df.columns:
                continue
            _series = _df[_name]
            _mask = _result.isna() & _filled(_series)
            _result = _result.where(~_mask, _series)
        return _result

    if not _wide_file.exists():
        _coverage_browser_display = mo.vstack([
            mo.md("### Row-Level Attribute Coverage Browser"),
            mo.callout(
                mo.md("`gold/wide_hospitals.parquet` not found. Run `flows/04_gold_production.py` first."),
                kind="warn",
            ),
        ])
    else:
        _wide = pd.read_parquet(_wide_file)
        _display_cols = [
            _col
            for _col in [
                "NAME",
                "name",
                "display_name",
                "addr:state",
                "addr:postcode",
                "BEDS",
                "beds",
                "TRAUMA",
                "cms_bed_cnt",
                "hifld_trauma",
                "acs_trauma_level",
                "source_provenance",
            ]
            if _col in _wide.columns
        ]

        _final_beds = _coalesce_text(_wide, ["BEDS", "beds"])
        if "cms_bed_cnt" in _wide.columns:
            _cms_beds = pd.to_numeric(_wide["cms_bed_cnt"], errors="coerce")
            _final_beds = _final_beds.where(_filled(_final_beds), _cms_beds)
        _final_trauma = _coalesce_text(_wide, ["TRAUMA", "acs_trauma_level", "hifld_trauma"])

        _beds_missing = ~_filled(_final_beds)
        _trauma_missing = ~_filled(_final_trauma)

        if hosp_coverage_filter_dd.value == "Missing BEDS":
            _filtered = _wide[_beds_missing].copy()
        elif hosp_coverage_filter_dd.value == "Missing TRAUMA":
            _filtered = _wide[_trauma_missing].copy()
        elif hosp_coverage_filter_dd.value == "Missing both":
            _filtered = _wide[_beds_missing & _trauma_missing].copy()
        else:
            _filtered = _wide.copy()

        _coverage_browser_display = mo.vstack([
            mo.md("### Row-Level Attribute Coverage Browser"),
            hosp_coverage_filter_dd,
            mo.ui.table(_filtered[_display_cols], label="Hospital attribute coverage"),
        ])

    _coverage_browser_display
    return


if __name__ == "__main__":
    app.run()
