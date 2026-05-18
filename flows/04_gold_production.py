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
def _(mo):
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
    if is_script_mode and "help" in mo.cli_args():
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
        layer_master = master[master["tmp_osm_layer"] == layer].copy()
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
        # Merge supplemental CMS attribute table if present (e.g. attr_health_cms.parquet)
        cms_attr_path = silver_path / f"attr_{layer}_cms.parquet"
        if cms_attr_path.exists():
            cms_attr = _load_parquet(cms_attr_path)
            if cms_attr is not None and "lifeline_id" in cms_attr.columns:
                cms_no_geom = cms_attr.drop(columns=["geometry"], errors="ignore")
                existing = set(wide.columns) - {"lifeline_id"}
                cms_cols = ["lifeline_id"] + [c for c in cms_no_geom.columns if c not in existing and c != "lifeline_id"]
                wide = wide.merge(cms_no_geom[cms_cols], on="lifeline_id", how="left")
        # Merge supplemental HIFLD hospital attr table if present (e.g. attr_health_hifld_attrs.parquet)
        hifld_attr_path = silver_path / f"attr_{layer}_hifld_attrs.parquet"
        if hifld_attr_path.exists():
            hifld_attr = _load_parquet(hifld_attr_path)
            if hifld_attr is not None and "lifeline_id" in hifld_attr.columns:
                hifld_no_geom = hifld_attr.drop(columns=["geometry"], errors="ignore")
                existing = set(wide.columns) - {"lifeline_id"}
                hifld_cols = ["lifeline_id"] + [c for c in hifld_no_geom.columns if c not in existing and c != "lifeline_id"]
                wide = wide.merge(hifld_no_geom[hifld_cols], on="lifeline_id", how="left")
        # Merge supplemental ACS trauma attr table if present (e.g. attr_health_acs_trauma.parquet)
        acs_attr_path = silver_path / f"attr_{layer}_acs_trauma.parquet"
        if acs_attr_path.exists():
            acs_attr = _load_parquet(acs_attr_path)
            if acs_attr is not None and "lifeline_id" in acs_attr.columns:
                acs_no_geom = acs_attr.drop(columns=["geometry"], errors="ignore")
                existing = set(wide.columns) - {"lifeline_id"}
                acs_cols = ["lifeline_id"] + [c for c in acs_no_geom.columns if c not in existing and c != "lifeline_id"]
                wide = wide.merge(acs_no_geom[acs_cols], on="lifeline_id", how="left")
        # Drop all tmp_ cols (tmp_osm_layer and any others)
        _tmp_cols = [c for c in wide.columns if c.startswith("tmp_")]
        wide = wide.drop(columns=_tmp_cols, errors="ignore")
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
def _(Path, cfg, gpd, mo):
    # Produce two focused hospital gold outputs from the pipeline-processed health gold layer:
    #
    #   wide_hospitals.parquet   — OSM-focused wide format with all silver/enrichment attributes.
    #                              Used for internal QAQC and attribute analysis.
    #   hifld_hospitals.parquet  — HIFLD schema–only output.  Contains ONLY the HIFLD field
    #                              names (NAME, ADDRESS, CITY, …) mapped from silver data.
    #                              Used for downstream QAQC against the HIFLD standard and as
    #                              a drop-in replacement for HIFLD bronze in QA tools.
    _gold_path = Path(cfg.storage.gold_path)
    _wide_health = _gold_path / "wide_health.parquet"
    _wide_out = _gold_path / "wide_hospitals.parquet"
    _out = _gold_path / "hifld_hospitals.parquet"

    if not _wide_health.exists():
        hifld_hospitals_result = mo.callout(
            mo.md("⏭ `gold/wide_health.parquet` not found — skipping `wide_hospitals.parquet` and `hifld_hospitals.parquet`."),
            kind="neutral",
        )
    else:
        try:
            _gdf = gpd.read_parquet(_wide_health)

            # Keep only the "hospitals" primary lifeline (not specialty, outpatient, etc.)
            if "fema_lifeline" in _gdf.columns:
                _before_cat = len(_gdf)
                _gdf = _gdf[_gdf["fema_lifeline"].apply(
                    lambda x: isinstance(x, dict) and x.get("primary") == "hospitals"
                )].copy()
                print(f"  fema_lifeline filter: {_before_cat:,} → {len(_gdf):,} (hospitals only)")

            # Keep only point geometries — polygons are campus footprints, not usable POIs
            _before_geom = len(_gdf)
            _gdf = _gdf[_gdf.geometry.geom_type == "Point"].copy()
            print(f"  geometry filter: {_before_geom:,} → {len(_gdf):,} (points only)")

            # Exclude records whose sole source is epa_frs — unreliable without OSM corroboration
            if "source_provenance" in _gdf.columns:
                _before_epa = len(_gdf)
                _gdf = _gdf[_gdf["source_provenance"] != "epa_frs"].copy()
                print(f"  epa_frs-only filter: {_before_epa:,} → {len(_gdf):,} (removed sole-epa_frs records)")

            # Add osm_lifeline_id = lifeline_id so QA can display linkage info
            if "lifeline_id" in _gdf.columns and "osm_lifeline_id" not in _gdf.columns:
                _gdf["osm_lifeline_id"] = _gdf["lifeline_id"]

            # --- CMS bed count coalesce ---
            # If wide_health already has cms_bed_cnt (from _produce_gold_layer supplemental merge),
            # use it; otherwise try loading attr_health_cms directly.
            if "cms_bed_cnt" not in _gdf.columns:
                _cms_attr_path = Path(cfg.storage.silver_path) / "attr_health_cms.parquet"
                if _cms_attr_path.exists():
                    import pandas as _pd
                    _cms_attr = _pd.read_parquet(_cms_attr_path)
                    _gdf = _gdf.merge(
                        _cms_attr[["lifeline_id", "cms_bed_cnt", "cms_certified_bed_cnt",
                                   "cms_operating_rooms", "cms_match_score",
                                   "cms_provider_num"]].rename(columns={}),
                        on="lifeline_id", how="left",
                    )

            # Coalesce: CMS bed count wins over OSM beds when available
            if "cms_bed_cnt" in _gdf.columns:
                import pandas as _pd
                _osm_beds = _pd.to_numeric(_gdf.get("beds", _pd.Series(dtype="float")), errors="coerce")
                _cms_beds = _pd.to_numeric(_gdf["cms_bed_cnt"], errors="coerce")
                # Use CMS value where positive; fall back to OSM beds
                _beds_coalesced = _cms_beds.where(_cms_beds > 0, _osm_beds)
                _gdf["beds"] = _beds_coalesced  # update OSM column in-place

            # --- Step 1: Write wide_hospitals.parquet (OSM-focused, all silver/enrichment attrs) ---
            # This is the full attribute view BEFORE any HIFLD schema columns are introduced.
            _gdf.to_parquet(_wide_out, index=False)
            print(f"  wide_hospitals.parquet — {len(_gdf):,} rows × {len(_gdf.columns)} cols")

            # --- Step 2: Build HIFLD schema–only output ---
            # All column values are mapped from the silver/wide data above.
            # The resulting GeoDataFrame contains ONLY the HIFLD field names + geometry.
            import pandas as _pd

            def _geom_coord(gdf, attr):
                try:
                    return getattr(gdf.geometry, attr)
                except Exception:
                    return _pd.Series(dtype="float64", index=gdf.index)

            _lon = _geom_coord(_gdf, "x")
            _lat = _geom_coord(_gdf, "y")

            # Combine house number + street into a single ADDRESS field
            _hn = _gdf.get("addr:housenumber", _pd.Series("", index=_gdf.index)).fillna("").astype(str)
            _st = _gdf.get("addr:street", _pd.Series("", index=_gdf.index)).fillna("").astype(str)
            _address = (_hn + " " + _st).str.strip()

            _beds_int = _pd.to_numeric(_gdf.get("beds"), errors="coerce").astype("Int64")

            # TRAUMA: ACS (most authoritative) coalesced with HIFLD
            def _coalesce_str(*series):
                """Return first non-null, non-empty value across multiple series."""
                result = _pd.Series(_pd.NA, index=_gdf.index, dtype="object")
                for s in series:
                    if s is None:
                        continue
                    mask = result.isna() & s.notna() & (s.astype(str).str.strip() != "")
                    result = result.where(~mask, s)
                return result

            _acs_trauma = _gdf.get("acs_trauma_level")
            _hifld_trauma = _gdf.get("hifld_trauma")
            _trauma_col = _coalesce_str(
                _acs_trauma if _acs_trauma is not None else None,
                _hifld_trauma if _hifld_trauma is not None else None,
            )

            _hifld_cols = {
                "NAME":       _gdf.get("name", _gdf.get("display_name", _pd.Series("", index=_gdf.index))).fillna(""),
                "ADDRESS":    _address,
                "CITY":       _gdf.get("addr:city", _pd.Series("", index=_gdf.index)).fillna(""),
                "STATE":      _gdf.get("addr:state", _pd.Series("", index=_gdf.index)).fillna(""),
                "ZIP":        _gdf.get("addr:postcode", _pd.Series("", index=_gdf.index)).fillna("").str[:5],
                "ZIP4":       _pd.Series(_pd.NA, index=_gdf.index, dtype="object"),
                "BEDS":       _beds_int,
                "TELEPHONE":  _gdf.get("phone", _pd.Series("", index=_gdf.index)).fillna(""),
                "WEBSITE":    _gdf.get("website", _pd.Series("", index=_gdf.index)).fillna(""),
                "LATITUDE":   _lat,
                "LONGITUDE":  _lon,
                "NAICS_CODE": _gdf.get("naics_codes", _pd.Series("", index=_gdf.index)).fillna(""),
                "NAICS_DESC": _pd.Series(_pd.NA, index=_gdf.index, dtype="object"),
                "SOURCE":     _gdf.get("source_provenance", _pd.Series("", index=_gdf.index)).fillna(""),
                "COUNTRY":    _pd.Series("USA", index=_gdf.index),
                "ID":         _gdf.get("lifeline_id", _pd.Series("", index=_gdf.index)).fillna(""),
                # Populated from HIFLD hospital attrs + ACS
                "TRAUMA":     _trauma_col,
                "HELIPAD":    _gdf.get("hifld_helipad", _pd.Series(_pd.NA, index=_gdf.index, dtype="object")),
                "OWNER":      _gdf.get("hifld_owner", _pd.Series(_pd.NA, index=_gdf.index, dtype="object")),
                "TYPE":       _gdf.get("hifld_hospital_type", _pd.Series(_pd.NA, index=_gdf.index, dtype="object")),
                # Deferred null stubs
                "ALT_NAME":   _pd.Series(_pd.NA, index=_gdf.index, dtype="object"),
                "STATUS":     _pd.Series(_pd.NA, index=_gdf.index, dtype="object"),
                "TTL_STAFF":  _pd.Series(_pd.NA, index=_gdf.index, dtype="Int64"),
                "POPULATION": _pd.Series(_pd.NA, index=_gdf.index, dtype="Int64"),
                "COUNTY":     _pd.Series(_pd.NA, index=_gdf.index, dtype="object"),
                "COUNTYFIPS": _pd.Series(_pd.NA, index=_gdf.index, dtype="object"),
                "ST_FIPS":    _pd.Series(_pd.NA, index=_gdf.index, dtype="object"),
                "STATE_ID":   _pd.Series(_pd.NA, index=_gdf.index, dtype="object"),
                "OBJECTID":   _pd.Series(_pd.NA, index=_gdf.index, dtype="Int64"),
                "SOURCEDATE": _pd.Series(_pd.NA, index=_gdf.index, dtype="object"),
                "VAL_METHOD": _pd.Series(_pd.NA, index=_gdf.index, dtype="object"),
                "VAL_DATE":   _pd.Series(_pd.NA, index=_gdf.index, dtype="object"),
            }

            # Build a new GeoDataFrame with ONLY HIFLD schema fields + geometry
            _hifld_df = _pd.DataFrame({col: series.values for col, series in _hifld_cols.items()})
            _hifld_gdf = gpd.GeoDataFrame(_hifld_df, geometry=_gdf.geometry.values, crs="EPSG:4326")
            _hifld_gdf.to_parquet(_out, index=False)

            _n = len(_gdf)
            _hifld_n = int((_gdf["source_provenance"].str.contains("hifld", na=False)).sum()) if "source_provenance" in _gdf.columns else 0
            _cms_n = int((_gdf["cms_bed_cnt"] > 0).sum()) if "cms_bed_cnt" in _gdf.columns else 0
            _beds_n = int(_hifld_gdf["BEDS"].notna().sum())
            _trauma_n = int(_hifld_gdf["TRAUMA"].notna().sum())
            print(f"  hifld_hospitals.parquet — {_n:,} rows × {len(_hifld_gdf.columns)} cols (HIFLD schema only; {_hifld_n} HIFLD-boosted, {_cms_n} CMS bed counts, {_beds_n} with BEDS, {_trauma_n} with TRAUMA)")
            hifld_hospitals_result = mo.callout(
                mo.md(
                    f"✅ **Hospital gold outputs written** — {_n:,} rows "
                    f"(Hospitals only · points only · epa_frs-only excluded)  \n"
                    f"• `wide_hospitals.parquet` — {len(_gdf.columns)} cols (OSM-focused, all attrs)  \n"
                    f"• `hifld_hospitals.parquet` — {len(_hifld_gdf.columns)} cols (HIFLD schema only; "
                    f"{_hifld_n} HIFLD-confirmed; {_cms_n} CMS bed counts; "
                    f"{_beds_n} with BEDS; {_trauma_n} with TRAUMA level)"
                ),
                kind="success",
            )
        except Exception as _exc:
            print(f"  ERROR producing hospital gold outputs: {_exc}")
            hifld_hospitals_result = mo.callout(
                mo.md(f"⚠️ Hospital gold outputs failed: {_exc}"), kind="warn"
            )

    hifld_hospitals_result
    return (hifld_hospitals_result,)


@app.cell
def _(Path, cfg, gpd, mo, pd):
    # Campus buildings secondary gold layer
    _silver_path = Path(cfg.storage.silver_path)
    _gold_path = Path(cfg.storage.gold_path)
    _buildings_src = _silver_path / "campus_buildings.parquet"
    _polygons_src = _silver_path / "campus_polygons.parquet"

    _campus_results: list[tuple[str, int]] = []

    if not hasattr(cfg, "campus_collapse") or not cfg.campus_collapse.enabled:
        campus_buildings_result = mo.callout(
            mo.md("⏭ Campus collapse disabled — campus buildings gold skipped."), kind="neutral"
        )
    elif not _buildings_src.exists():
        campus_buildings_result = mo.callout(
            mo.md("⏭ `silver/campus_buildings.parquet` not found — run Flow 02b first."), kind="neutral"
        )
    else:
        try:
            _buildings = gpd.read_parquet(_buildings_src)
            _gold_path.mkdir(parents=True, exist_ok=True)
            _out = _gold_path / "campus_buildings.parquet"
            _buildings.to_parquet(_out, index=False)
            _campus_results.append(("campus_buildings", len(_buildings)))
            print(f"  gold/campus_buildings.parquet — {len(_buildings):,} rows")
        except Exception as _exc:
            print(f"  ERROR writing campus_buildings gold: {_exc}")

        if _polygons_src.exists():
            try:
                _polys = gpd.read_parquet(_polygons_src)
                _out_poly = _gold_path / "campus_polygons.parquet"
                _polys.to_parquet(_out_poly, index=False)
                _campus_results.append(("campus_polygons", len(_polys)))
                print(f"  gold/campus_polygons.parquet — {len(_polys):,} rows")
            except Exception as _exc:
                print(f"  ERROR writing campus_polygons gold: {_exc}")

        campus_buildings_result = mo.callout(
            mo.md(
                "✅ **Campus buildings gold complete.**  "
                + "  ".join(f"`{nm}` ({n:,} rows)" for nm, n in _campus_results)
            ),
            kind="success",
        )

    campus_buildings_result
    return (campus_buildings_result,)


@app.cell
def _(campus_buildings_result, gold_result, hifld_hospitals_result, is_script_mode, mo):
    if is_script_mode:
        print("Gold Production Summary")
        print("Flow 04 complete. Run flows/05_generate_tiles.py next.")
    else:
        mo.vstack([
            mo.md("## Gold Production Summary"),
            gold_result,
            hifld_hospitals_result,
            campus_buildings_result,
            mo.callout(mo.md("✅ **Flow 04 complete.** ➡ Run `flows/05_generate_tiles.py` next."), kind="success"),
        ])
    return


if __name__ == "__main__":
    app.run()
