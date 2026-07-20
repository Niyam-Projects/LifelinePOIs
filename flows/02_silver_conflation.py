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

    return (
        BaseModel,
        ConfidenceTier,
        Field,
        LifelineConfig,
        Path,
        add_h3_index,
        clip_to_bbox,
        compute_confidence,
        gpd,
        pd,
        uuid,
    )


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
        .form(submit_button_label="▶ Run Conflation")
    )
    params_form
    return (params_form,)


@app.cell
def _(FlowParams, mo):
    import sys as _sys
    is_script_mode = mo.app_meta().mode == "script"
    if is_script_mode and "help" in mo.cli_args():
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
def _(
    ConfidenceTier,
    Path,
    add_h3_index,
    cfg,
    clip_to_bbox,
    compute_confidence,
    gpd,
    mo,
    pd,
    uuid,
):
    # ---------------------------------------------------------------------------
    # FEMA lifeline tag matcher — parses taxonomy + OSM crosswalk CSVs once
    # ---------------------------------------------------------------------------
    def _build_fema_ll_matcher(seed_dir: Path):
        """
        Parse fema_lifelines_taxonomy.csv + fema_lifeline_osm.csv into a list of
        (struct_dict, rule_groups) tuples.

        osm_tags syntax:
          ","  = OR between rules  (any one rule can fire)
          "&"  = AND within a rule (all key=val pairs must match)

        Returns a function match_ll(row_dict) → struct dict | None.
        """
        import csv as _csv2

        taxonomy: dict[str, dict] = {}
        with open(seed_dir / "fema_lifelines_taxonomy.csv", newline="", encoding="utf-8") as fh:
            for row in _csv2.DictReader(fh):
                key = row["key"].strip()
                taxonomy[key] = {
                    "primary": key,
                    "hierarchy": [
                        row["lifeline_key"].strip(),
                        row["lifeline_component_key"].strip(),
                        key,
                    ],
                    "alternates": [],
                }

        rules = []
        with open(seed_dir / "fema_lifeline_osm.csv", newline="", encoding="utf-8") as fh:
            for row in _csv2.DictReader(fh):
                lifeline_key = row["lifeline_key"].strip()
                struct = taxonomy.get(lifeline_key)
                if not struct:
                    continue
                rule_groups = []
                for token in row["osm_tags"].split(","):
                    token = token.strip()
                    if not token:
                        continue
                    and_pairs = []
                    for part in token.split("&"):
                        part = part.strip()
                        if "=" in part:
                            k, v = part.split("=", 1)
                            and_pairs.append((k.strip(), v.strip()))
                    if and_pairs:
                        rule_groups.append(and_pairs)
                if rule_groups:
                    rules.append((struct, rule_groups))

        def match_ll(row_dict: dict):
            for struct, rule_groups in rules:
                for and_pairs in rule_groups:  # OR between groups
                    if all(                     # AND within group
                        row_dict.get(k) is not None and str(row_dict.get(k)) == v
                        for k, v in and_pairs
                    ):
                        return struct
            return None

        return match_ll

    _seed_dir = Path(".").resolve() / "data" / "seed"
    _match_ll = _build_fema_ll_matcher(_seed_dir)

    _LAYER_KEY_FIELDS = {
        "power": ["power", "voltage", "operator", "name"],
        "water_infrastructure": ["man_made", "operator", "name", "capacity"],
        "telecom": ["telecom", "man_made", "operator", "name"],
        "fuel": ["industrial", "man_made", "operator", "name"],
        "safety": ["amenity", "emergency", "operator", "name"],
        "health": ["amenity", "healthcare", "operator", "name", "beds"],
        "education": ["amenity", "operator", "name", "isced:level"],
        "transportation": ["railway", "aeroway", "amenity", "operator", "name", "iata"],
    }

    def _load_bronze_osm(bronze_path, layer):
        import glob as _glob
        # New layout: bronze/osm/{area_name}/{layer}.parquet
        area_paths = sorted(_glob.glob(str(bronze_path / "osm" / "*" / f"{layer}.parquet")))
        # Fallback: old flat layout bronze/osm/{layer}.parquet
        flat_path = bronze_path / "osm" / f"{layer}.parquet"
        paths_to_load = area_paths if area_paths else ([str(flat_path)] if flat_path.exists() else [])
        if not paths_to_load:
            print(f"    WARNING: Bronze OSM layer not found for layer '{layer}' — skipping")
            return None
        gdfs = []
        for p in paths_to_load:
            gdf = gpd.read_parquet(p)
            if gdf.crs is None:
                gdf = gdf.set_crs("EPSG:4326")
            elif gdf.crs.to_epsg() != 4326:
                gdf = gdf.to_crs("EPSG:4326")
            gdfs.append(gdf)
        if len(gdfs) == 1:
            return gdfs[0]
        merged = gpd.GeoDataFrame(pd.concat(gdfs, ignore_index=True), crs="EPSG:4326")
        return merged

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
        gdf = _assign_uuids(gdf)
        gdf = add_h3_index(gdf, column="h3_index")
        if "name" in gdf.columns:
            gdf["display_name"] = gdf["name"]
        else:
            gdf["display_name"] = (
                gdf.get("type", pd.Series("unknown", index=gdf.index)).astype(str)
                + "/" + gdf.get("id", pd.Series(range(len(gdf)), index=gdf.index)).astype(str)
            )
        primary_tag = {
            "power": "power", "water_infrastructure": "man_made",
            "telecom": "telecom", "fuel": "industrial",
            "safety": "amenity", "health": "amenity", "education": "amenity",
        }.get(layer, "name")
        if layer == "transportation":
            gdf["osm_category"] = (
                gdf.get("railway", pd.Series(None, index=gdf.index))
                .fillna(gdf.get("aeroway", pd.Series(None, index=gdf.index)))
                .fillna(gdf.get("amenity", pd.Series(None, index=gdf.index)))
                .fillna(gdf.get("highway", pd.Series(None, index=gdf.index)))
            )
        else:
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
        # Assign fema_lifeline struct from tag matching
        gdf["fema_lifeline"] = gdf.apply(lambda r: _match_ll(r.to_dict()), axis=1)

        core_cols = [c for c in [
            "lifeline_id", "display_name", "osm_category", "h3_index",
            "confidence_score", "confidence_tier",
            "fema_lifeline",
            "geometry",
        ] if c in gdf.columns]
        core = gdf[core_cols].copy()
        core["tmp_osm_layer"]    = layer
        core["source_provenance"] = "osm"
        return core

    def _build_attr_table(gdf, layer, lifeline_ids):
        gdf = gdf.copy()
        gdf["lifeline_id"] = lifeline_ids.values
        drop_cols = {"geometry", "type", "id", "bbox", "names", "lifeline_id"}
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
        master_gdf = gpd.GeoDataFrame(master, geometry="geometry").set_crs("EPSG:4326", allow_override=True)
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
def _(cfg, mo):
    # Hospital building-centroid deduplication
    # Merges amenity=hospital nodes with nearby building=hospital centroid duplicates,
    # keeping the building centroid geometry (more accurate) and coalescing attributes.
    from pathlib import Path as _P
    import geopandas as _gpd
    import pandas as _pd
    from lib.health_dedup import dedup_hospital_building_centroids

    _silver_pts = _P(cfg.storage.silver_path) / "lifeline_points.parquet"

    if not _silver_pts.exists():
        health_dedup_result = mo.callout(
            mo.md("⏭ Health dedup skipped — `silver/lifeline_points.parquet` not found."), kind="neutral"
        )
    else:
        _master = _gpd.read_parquet(_silver_pts)
        _health_mask = _master["tmp_osm_layer"] == "health"
        _health_rows = _master[_health_mask].copy()
        _other_rows = _master[~_health_mask].copy()

        # Load attr_health for precise building/amenity tag identification
        _attr_path = _P(cfg.storage.silver_path) / "attr_health.parquet"
        _attr_health = _pd.read_parquet(_attr_path) if _attr_path.exists() else None

        _deduped, _n_merged = dedup_hospital_building_centroids(
            _health_rows,
            attr_gdf=_attr_health,
            name_threshold=cfg.conflation.name_similarity_threshold,
            proximity_m=cfg.conflation.spatial_proximity_meters,
        )

        if _n_merged > 0:
            _updated = _gpd.GeoDataFrame(
                _pd.concat([_other_rows, _deduped], ignore_index=True),
                geometry="geometry",
                crs="EPSG:4326",
            )
            _updated.to_parquet(_silver_pts, index=False)
            print(f"  Health dedup: {_n_merged} amenity-node duplicates merged into building centroids")
            health_dedup_result = mo.callout(
                mo.md(
                    f"✅ **Hospital building-centroid dedup complete.** "
                    f"`{_n_merged}` amenity-node duplicates merged into building centroids "
                    f"(geometry kept at building centroid)."
                ),
                kind="success",
            )
        else:
            health_dedup_result = mo.callout(
                mo.md("ℹ️ Health dedup: no amenity-node / building-centroid pairs found within threshold."),
                kind="neutral",
            )

    health_dedup_result
    return (health_dedup_result,)


@app.cell
def _(cfg, mo):
    """Overture Places conflation — match hospitals to OSM silver; prefer Overture names."""
    from pathlib import Path as _P
    import geopandas as _gpd
    import pandas as _pd
    import numpy as _np
    import uuid as _uuid

    _bronze_path = _P(cfg.storage.bronze_path)
    _silver_path = _P(cfg.storage.silver_path)
    _overture_places = _bronze_path / "overture" / "places" / "us_territories.parquet"
    _silver_pts_path = _silver_path / "lifeline_points.parquet"
    _attr_out = _silver_path / "attr_health_overture.parquet"

    if not _overture_places.exists():
        overture_conflation_result = mo.callout(
            mo.md(
                "⏭ Overture conflation skipped — "
                "`bronze/overture/places/us_territories.parquet` not found.  \n"
                "Run Flow 00 with **Overture Places download** enabled first."
            ),
            kind="neutral",
        )
    elif not _silver_pts_path.exists():
        overture_conflation_result = mo.callout(
            mo.md("⏭ Overture conflation skipped — `silver/lifeline_points.parquet` not found."), kind="neutral"
        )
    else:
        try:
            from sklearn.neighbors import BallTree as _BallTree

            # ----------------------------------------------------------------
            # 1. Load Overture places filtered to first configured taxonomy
            # ----------------------------------------------------------------
            _taxonomy_allowlist = cfg.overture.places_taxonomy   # e.g. [["health_care", "hospital"]]
            _primary_taxonomy = _taxonomy_allowlist[0]           # ["health_care", "hospital"]
            _l0 = _primary_taxonomy[0]
            _l1 = _primary_taxonomy[1] if len(_primary_taxonomy) > 1 else None

            _ov = _gpd.read_parquet(_overture_places)
            _mask = _ov["taxonomy_l0"] == _l0
            if _l1:
                _mask &= _ov["taxonomy_l1"] == _l1
            _ov_hospitals = _ov[_mask].copy().reset_index(drop=True)

            # ----------------------------------------------------------------
            # 2. Clip to union of all configured area bboxes
            # ----------------------------------------------------------------
            _area_bboxes = [a.bbox for a in cfg.areas if a.bbox is not None]
            if _area_bboxes:
                def _in_any_bbox(geom, bboxes):
                    x, y = geom.x, geom.y
                    return any(
                        mnx <= x <= mxx and mny <= y <= mxy
                        for mnx, mny, mxx, mxy in bboxes
                    )
                _in_area = _ov_hospitals.geometry.apply(lambda g: _in_any_bbox(g, _area_bboxes))
                _ov_area = _ov_hospitals[_in_area].copy().reset_index(drop=True)
            else:
                _ov_area = _ov_hospitals.copy()

            print(f"  Overture hospitals in area: {len(_ov_area):,}")

            # ----------------------------------------------------------------
            # 3. Load silver points (health layer only for BallTree matching)
            # ----------------------------------------------------------------
            _master = _gpd.read_parquet(_silver_pts_path)
            _health_mask = _master["tmp_osm_layer"] == "health"
            _health_rows = _master[_health_mask].copy()

            # ----------------------------------------------------------------
            # 4. BallTree nearest-neighbour match
            # ----------------------------------------------------------------
            _proximity_m = cfg.conflation.spatial_proximity_meters
            _EARTH_R = 6_371_000.0

            def _to_rad(gdf):
                geom = gdf.geometry
                # Reduce non-Point geometries (Polygon, MultiPolygon, etc.) to centroids
                mask = geom.geom_type != "Point"
                if mask.any():
                    geom = geom.copy()
                    geom[mask] = geom[mask].centroid
                return _np.radians(
                    _np.column_stack([geom.y.values, geom.x.values])
                )

            _matched_ids = set()
            _attr_rows = []
            _new_pois = []

            if len(_health_rows) > 0 and len(_ov_area) > 0:
                _tree = _BallTree(_to_rad(_health_rows), metric="haversine")
                _ov_rad = _to_rad(_ov_area)
                _dists, _idxs = _tree.query(_ov_rad, k=1)
                _dist_m = _dists[:, 0] * _EARTH_R

                for _i, (_ov_row, _d, _osm_idx) in enumerate(
                    zip(_ov_area.itertuples(), _dist_m, _idxs[:, 0])
                ):
                    _ov_name = getattr(_ov_row, "overture_name", None)
                    _ov_id = getattr(_ov_row, "overture_id", None)

                    if _d <= _proximity_m:
                        # Matched — update OSM point with Overture name + boost confidence
                        _iloc = _health_rows.index[_osm_idx]
                        _matched_ids.add(_iloc)
                        _old_name = _master.at[_iloc, "display_name"]
                        if _ov_name and str(_ov_name).strip():
                            _master.at[_iloc, "display_name"] = str(_ov_name).strip()
                        # Boost confidence (capped at 1.0)
                        _master.at[_iloc, "confidence_score"] = min(
                            1.0,
                            float(_master.at[_iloc, "confidence_score"]) + 0.15,
                        )
                        _master.at[_iloc, "source_provenance"] = "osm+overture"
                        _attr_rows.append({
                            "lifeline_id": str(_master.at[_iloc, "lifeline_id"]),
                            "overture_id": _ov_id,
                            "overture_name": _ov_name,
                            "overture_taxonomy_l0": _l0,
                            "overture_taxonomy_l1": _l1,
                            "match_dist_m": round(float(_d), 1),
                            "match_type": "osm+overture",
                        })
                    else:
                        # Unmatched — mint new Overture-only silver POI
                        _raw_geom = _ov_row.geometry
                        _geom = _raw_geom if _raw_geom.geom_type == "Point" else _raw_geom.centroid
                        _new_id = str(
                            _uuid.uuid5(_uuid.NAMESPACE_URL, f"overture:{_ov_id}")
                        )
                        _new_row = {
                            "lifeline_id": _new_id,
                            "display_name": str(_ov_name).strip() if _ov_name else "Unknown Hospital",
                            "geometry": _geom,
                            "confidence_score": min(1.0, float(getattr(_ov_row, "confidence", 0.5)) + 0.15),
                            "source_provenance": "overture",
                            "tmp_osm_layer": "health",
                            "lifeline_key": "hospitals",
                            "lifeline_component_key": "medical_care",
                        }
                        _new_pois.append(_new_row)
                        _attr_rows.append({
                            "lifeline_id": _new_id,
                            "overture_id": _ov_id,
                            "overture_name": _ov_name,
                            "overture_taxonomy_l0": _l0,
                            "overture_taxonomy_l1": _l1,
                            "match_dist_m": None,
                            "match_type": "overture_only",
                        })

            # ----------------------------------------------------------------
            # 5. Merge minted POIs back into silver
            # ----------------------------------------------------------------
            _n_matched = sum(1 for r in _attr_rows if r["match_type"] == "osm+overture")
            _n_minted = sum(1 for r in _attr_rows if r["match_type"] == "overture_only")

            if _new_pois:
                _minted_gdf = _gpd.GeoDataFrame(_new_pois, geometry="geometry", crs="EPSG:4326")
                _updated_master = _gpd.GeoDataFrame(
                    _pd.concat([_master, _minted_gdf], ignore_index=True),
                    geometry="geometry",
                    crs="EPSG:4326",
                )
            else:
                _updated_master = _master

            _updated_master.to_parquet(_silver_pts_path, index=False)

            # ----------------------------------------------------------------
            # 6. Write Overture attribute table
            # ----------------------------------------------------------------
            _attr_df = _pd.DataFrame(_attr_rows)
            _attr_df.to_parquet(_attr_out, index=False)

            print(
                f"  Overture conflation: {_n_matched} matched (name updated), "
                f"{_n_minted} minted → silver/attr_health_overture.parquet"
            )
            overture_conflation_result = mo.callout(
                mo.md(
                    f"✅ **Overture conflation complete.**  \n"
                    f"`{_n_matched}` OSM hospitals updated with Overture names · "
                    f"`{_n_minted}` Overture-only hospitals minted → "
                    f"`silver/attr_health_overture.parquet`"
                ),
                kind="success",
            )
        except Exception as _e:
            import traceback as _tb
            print(f"  ERROR: Overture conflation failed: {_e}")
            print(_tb.format_exc())
            overture_conflation_result = mo.callout(
                mo.md(f"⚠️ Overture conflation failed: {_e}"), kind="warn"
            )

    overture_conflation_result
    return (overture_conflation_result,)


@app.cell
def _(cfg, mo):
    # HIFLD validation pass — boosts confidence for OSM points confirmed by HIFLD data
    from pathlib import Path as _P
    import geopandas as _gpd
    import pandas as _pd

    _bronze_path = _P(cfg.storage.bronze_path)
    _silver_path = _P(cfg.storage.silver_path)
    _hifld_dir = _bronze_path / "hifld"

    _hifld_enabled = cfg.hifld.enabled and _hifld_dir.exists()
    _silver_pts = _silver_path / "lifeline_points.parquet"

    if not _hifld_enabled or not _silver_pts.exists():
        hifld_validation_result = mo.callout(
            mo.md("⏭ HIFLD validation skipped (disabled or bronze not present)."), kind="neutral"
        )
    else:
        from lib.hifld_validation import load_hifld_layer, match_hifld, apply_hifld_boost, classify_telecom_tower_type

        _master = _gpd.read_parquet(_silver_pts)
        _bbox = cfg.aoi.bbox if hasattr(cfg, "aoi") and cfg.aoi else None
        _weights = cfg.conflation.confidence_weights
        _boosted = 0

        for _name, _layer_def in cfg.hifld.layers.items():
            _hifld_gdf = load_hifld_layer(_bronze_path, _name, _layer_def.lon_field, _layer_def.lat_field, _bbox)
            if _hifld_gdf is None:
                continue
            _osm_layer = _layer_def.osm_layer
            _layer_rows = _master[_master["tmp_osm_layer"] == _osm_layer]
            if len(_layer_rows) == 0:
                continue
            _matched = match_hifld(_layer_rows, _hifld_gdf, max_distance_m=cfg.conflation.spatial_proximity_meters)
            _boosted += int(_matched.sum())
            _master = apply_hifld_boost(_master, _osm_layer, _matched, _weights)
            print(f"  HIFLD [{_name}] → {_matched.sum()} OSM {_osm_layer} points boosted")

        # Telecom tower type classification
        _telecom_layers = {}
        for _tname in ["cellular", "microwave", "lm_commercial", "lm_private"]:
            _ldef = cfg.hifld.layers.get(_tname)
            if _ldef:
                _g = load_hifld_layer(_bronze_path, _tname, _ldef.lon_field, _ldef.lat_field, _bbox)
                if _g is not None:
                    _telecom_layers[_tname] = _g

        _attr_telecom_path = _silver_path / "attr_telecom.parquet"
        if _telecom_layers and _attr_telecom_path.exists():
            _telecom_rows = _master[_master["tmp_osm_layer"] == "telecom"]
            if len(_telecom_rows) > 0:
                _tower_types = classify_telecom_tower_type(_telecom_rows, _telecom_layers,
                                                           max_distance_m=cfg.conflation.spatial_proximity_meters)
                _attr_telecom = _pd.read_parquet(_attr_telecom_path)
                # Align tower_type_hifld by lifeline_id
                _type_map = dict(zip(_telecom_rows["lifeline_id"], _tower_types.values))
                _attr_telecom["tower_type_hifld"] = _attr_telecom["lifeline_id"].map(_type_map)
                _attr_telecom.to_parquet(_attr_telecom_path, index=False)
                _typed = _tower_types.notna().sum()
                print(f"  HIFLD telecom type classification: {_typed} towers typed")

        _master.to_parquet(_silver_pts, index=False)
        print(f"  HIFLD validation complete — {_boosted} total points boosted")
        hifld_validation_result = mo.callout(
            mo.md(f"✅ **HIFLD validation complete.** `{_boosted}` OSM points confidence-boosted."),
            kind="success",
        )

    hifld_validation_result
    return (hifld_validation_result,)


@app.cell
def hifld_attributes(cfg, mo):
    # HIFLD hospital attribute extraction — produces silver/attr_health_hifld_attrs.parquet
    # Carries TRAUMA, HELIPAD, OWNER, TYPE from HIFLD bronze into the silver attr layer.
    from pathlib import Path as _P

    _silver_path = _P(cfg.storage.silver_path)
    _bronze_path = _P(cfg.storage.bronze_path)
    _hifld_hospitals_bronze = _bronze_path / "hifld" / "hospitals.parquet"
    _silver_pts = _silver_path / "lifeline_points.parquet"
    _attr_out = _silver_path / "attr_health_hifld_attrs.parquet"

    if not cfg.hifld.enabled:
        hifld_attrs_result = mo.callout(
            mo.md("⏭ HIFLD disabled — skipping `attr_health_hifld_attrs.parquet`."), kind="neutral"
        )
    elif not _hifld_hospitals_bronze.exists():
        hifld_attrs_result = mo.callout(
            mo.md("⏭ `bronze/hifld/hospitals.parquet` not found — run Flow 01 HIFLD download first."), kind="neutral"
        )
    elif not _silver_pts.exists():
        hifld_attrs_result = mo.callout(
            mo.md("⏭ `silver/lifeline_points.parquet` not found — run conflation first."), kind="neutral"
        )
    else:
        try:
            from lib.hifld_hospital_attrs import build_attr_health_hifld

            with mo.status.spinner(title="Matching HIFLD hospital attributes..."):
                _attr = build_attr_health_hifld(
                    silver_path=_silver_path,
                    bronze_path=_bronze_path,
                    threshold=cfg.conflation.name_similarity_threshold,
                )

            _attr.to_parquet(_attr_out, index=False)
            _matched = len(_attr)
            _trauma_fill = int(_attr["hifld_trauma"].notna().sum())
            print(
                f"  HIFLD hospital attrs: {_matched:,} matched "
                f"({_trauma_fill:,} with trauma level) → attr_health_hifld_attrs.parquet"
            )
            hifld_attrs_result = mo.callout(
                mo.md(
                    f"✅ **HIFLD hospital attrs complete.** "
                    f"`{_matched:,}` hospitals matched "
                    f"(`{_trauma_fill:,}` with trauma level) → `silver/attr_health_hifld_attrs.parquet`."
                ),
                kind="success",
            )
        except Exception as _e:
            print(f"  ERROR: HIFLD hospital attrs failed: {_e}")
            hifld_attrs_result = mo.callout(
                mo.md(f"⚠️ HIFLD hospital attrs failed: {_e}"), kind="warn"
            )

    hifld_attrs_result
    return (hifld_attrs_result,)


@app.cell
def _(cfg, mo):
    # EPA NAICS confidence boost — enriches silver/lifeline_points.parquet
    from pathlib import Path as _P
    import geopandas as _gpd

    _naics_cfg = cfg.epa_naics
    _silver_pts = _P(cfg.storage.silver_path) / "lifeline_points.parquet"
    _bronze_path = _P(cfg.storage.bronze_path)

    if not _naics_cfg.enabled or not _silver_pts.exists():
        epa_naics_result = mo.callout(
            mo.md("⏭ EPA NAICS boost skipped (disabled or silver not present)."), kind="neutral"
        )
    else:
        from lib.epa_naics_boost import run_epa_naics_pipeline

        _master = _gpd.read_parquet(_silver_pts)
        _bbox = cfg.aoi.bbox if hasattr(cfg, "aoi") and cfg.aoi else None
        _state_codes = cfg.aoi.state_codes if hasattr(cfg, "aoi") and cfg.aoi else []
        _weights = cfg.conflation.confidence_weights

        _master, _stats = run_epa_naics_pipeline(
            silver_gdf=_master,
            bronze_path=_bronze_path,
            naics_cfg=_naics_cfg,
            conflation_weights=_weights,
            bbox=_bbox,
            state_codes=_state_codes,
        )
        _master.to_parquet(_silver_pts, index=False)

        epa_naics_result = mo.callout(
            mo.md(
                f"✅ **EPA NAICS complete.**  "
                f"Pass 1: `{_stats['pass1_boosted']:,}` boosted · "
                f"Pass 2: `{_stats['pass2_boosted']:,}` boosted · "
                f"New POIs: `{_stats['total_minted']:,}` minted "
                f"(`{_stats['minted_geocoded']:,}` geocoded, "
                f"`{_stats['minted_large_displacement'] + _stats['minted_frs_only']:,}` FRS-coords)"
            ),
            kind="success",
        )

    epa_naics_result
    return (epa_naics_result,)


@app.cell
def cms_enrichment(cfg, mo):
    # CMS Hospital Provider enrichment — produces silver/attr_health_cms.parquet
    # Two-tier matching: Tier 1 spatial (geocoded lat/lon + BallTree buffer),
    # Tier 2 ZIP + rapidfuzz name for unmatched POIs.
    from pathlib import Path as _P
    import pandas as _pd

    _silver_path = _P(cfg.storage.silver_path)
    _bronze_path = _P(cfg.storage.bronze_path)
    _cms_cfg = cfg.cms
    _silver_pts = _silver_path / "lifeline_points.parquet"
    _cms_bronze = _bronze_path / "cms" / "cms_hospital_providers.parquet"
    _attr_out = _silver_path / "attr_health_cms.parquet"

    if not _cms_cfg.enabled:
        cms_enrich_result = mo.callout(
            mo.md("⏭ CMS enrichment disabled in config — skipping `attr_health_cms.parquet`."), kind="neutral"
        )
    elif not _silver_pts.exists():
        cms_enrich_result = mo.callout(
            mo.md("⏭ `silver/lifeline_points.parquet` not found — run conflation first."), kind="neutral"
        )
    elif not _cms_bronze.exists():
        cms_enrich_result = mo.callout(
            mo.md("⏭ `bronze/cms/cms_hospital_providers.parquet` not found — run Flow 01 CMS download first."), kind="neutral"
        )
    else:
        try:
            from lib.cms_health_enrich import build_attr_health_cms

            _attr = build_attr_health_cms(
                silver_path=_silver_path,
                bronze_path=_bronze_path,
                threshold=_cms_cfg.name_similarity_threshold,
                spatial_distance_m=getattr(_cms_cfg, "spatial_match_distance_m", 200.0),
                spatial_name_threshold=getattr(_cms_cfg, "spatial_name_threshold", 0.55),
            )
            _attr.to_parquet(_attr_out, index=False)
            _matched = len(_attr)
            _n_spatial = int((_attr.get("cms_match_method", _pd.Series()) == "spatial").sum())
            _n_zip_fuzzy = _matched - _n_spatial
            _bed_fill = int((_attr["cms_bed_cnt"] > 0).sum()) if "cms_bed_cnt" in _attr.columns else 0

            # Fill rates for key staffing counts
            def _fill(col): return int((_attr[col].notna() & (_attr[col] != 0)).sum()) if col in _attr.columns else 0
            _physn = _fill("cms_physn_cnt")
            _rn    = _fill("cms_rn_cnt")
            _crna  = _fill("cms_crna_cnt")
            _dietn = _fill("cms_dietn_cnt")
            _lab   = _fill("cms_lab_tchncn_cnt")

            print(
                f"  CMS enrichment: {_matched:,} hospitals matched "
                f"(spatial={_n_spatial:,}, zip_fuzzy={_n_zip_fuzzy:,}) "
                f"| beds={_bed_fill:,} physn={_physn:,} rn={_rn:,} "
                f"crna={_crna:,} dietn={_dietn:,} lab_tech={_lab:,} "
                f"→ attr_health_cms.parquet"
            )
            cms_enrich_result = mo.callout(
                mo.md(
                    f"✅ **CMS enrichment complete.** "
                    f"`{_matched:,}` hospitals matched "
                    f"(spatial: `{_n_spatial:,}` · zip+fuzzy: `{_n_zip_fuzzy:,}`) · "
                    f"beds: `{_bed_fill:,}` · physicians: `{_physn:,}` · "
                    f"RNs: `{_rn:,}` · CRNAs: `{_crna:,}` "
                    f"→ `silver/attr_health_cms.parquet`."
                ),
                kind="success",
            )
        except Exception as _e:
            print(f"  ERROR: CMS enrichment failed: {_e}")
            import traceback as _tb
            _tb.print_exc()
            cms_enrich_result = mo.callout(
                mo.md(f"⚠️ CMS enrichment failed: {_e}"), kind="warn"
            )

    cms_enrich_result
    return (cms_enrich_result,)


@app.cell
def _(cfg, mo):
    # ACS Trauma Center enrichment — produces silver/attr_health_acs_trauma.parquet
    # Reads from committed seed file: data/seed/acs_trauma_level.parquet
    from pathlib import Path as _P

    _silver_path = _P(cfg.storage.silver_path)
    _silver_pts = _silver_path / "lifeline_points.parquet"
    _attr_out = _silver_path / "attr_health_acs_trauma.parquet"
    _seed_file = _P("data") / "seed" / "acs_trauma_level.parquet"

    if not _seed_file.exists():
        acs_trauma_result = mo.callout(
            mo.md("⏭ `data/seed/acs_trauma_level.parquet` not found — skipping ACS trauma enrichment."), kind="neutral"
        )
    elif not _silver_pts.exists():
        acs_trauma_result = mo.callout(
            mo.md("⏭ `silver/lifeline_points.parquet` not found — run conflation first."), kind="neutral"
        )
    else:
        try:
            from lib.acs_trauma_enrich import build_attr_health_acs_trauma

            _attr = build_attr_health_acs_trauma(
                silver_path=_silver_path,
                seed_path=_seed_file,
                max_distance_m=200.0,
                name_threshold=0.70,
            )
            _attr.to_parquet(_attr_out, index=False)
            _matched = len(_attr)
            _trauma_fill = int(_attr["acs_trauma_level"].notna().sum())
            print(
                f"  ACS trauma: {_matched:,} hospitals matched "
                f"({_trauma_fill:,} with trauma level) → attr_health_acs_trauma.parquet"
            )
            acs_trauma_result = mo.callout(
                mo.md(
                    f"✅ **ACS trauma enrichment complete.** "
                    f"`{_matched:,}` hospitals matched "
                    f"(`{_trauma_fill:,}` with trauma level) → `silver/attr_health_acs_trauma.parquet`."
                ),
                kind="success",
            )
        except Exception as _e:
            print(f"  ERROR: ACS trauma enrichment failed: {_e}")
            acs_trauma_result = mo.callout(
                mo.md(f"⚠️ ACS trauma enrichment failed: {_e}"), kind="warn"
            )

    acs_trauma_result
    return (acs_trauma_result,)


@app.cell
def _(
    acs_trauma_result,
    cms_enrich_result,
    conflation_result,
    epa_naics_result,
    hifld_attrs_result,
    hifld_validation_result,
    is_script_mode,
    mo,
):
    if is_script_mode:
        print("Conflation Summary")
        print("✅ Flow 02 complete. ➡ Run `flows/03_gersite_bridge.py` next.")
    else:
        mo.vstack([
            mo.md("## Conflation Summary"),
            conflation_result,
            hifld_validation_result,
            hifld_attrs_result,
            epa_naics_result,
            cms_enrich_result,
            acs_trauma_result,
            mo.callout(mo.md("✅ **Flow 02 complete.** ➡ Run `flows/03_gersite_bridge.py` next."), kind="success"),
        ])
    return


if __name__ == "__main__":
    app.run()
