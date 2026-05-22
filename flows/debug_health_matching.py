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
    # Debug · Health Matching
    ## CMS Bed Count & ACS Trauma Level

    Diagnostic notebook for tuning CMS hospital bed-count matching and ACS trauma-level
    matching against silver health POIs. Adjust spatial distances and fuzzy thresholds,
    visualize all three point sets, and inspect near-misses to diagnose what's not working.

    **Read-only — no files are modified.**
    """)
    return


@app.cell
def _():
    import sys
    from pathlib import Path

    import altair as alt
    import geopandas as gpd
    import numpy as np
    import pandas as pd

    sys.path.insert(0, str(Path(".").resolve()))

    from lib.acs_trauma_enrich import _normalize_name as acs_normalize_name
    from lib.acs_trauma_enrich import build_attr_health_acs_trauma, load_acs_trauma
    from lib.cms_health_enrich import _enrich_health_addr
    from lib.cms_health_enrich import _normalize_name as cms_normalize_name
    from lib.cms_health_enrich import build_attr_health_cms, load_cms_providers
    from src.lifelinepoi.config import LifelineConfig

    return (
        LifelineConfig,
        Path,
        _enrich_health_addr,
        acs_normalize_name,
        alt,
        build_attr_health_acs_trauma,
        build_attr_health_cms,
        cms_normalize_name,
        gpd,
        load_acs_trauma,
        load_cms_providers,
        np,
        pd,
    )


@app.cell
def _(mo):
    config_form = (
        mo.md("## Configuration\n\nConfig path: {config_path}")
        .batch(config_path=mo.ui.text(value="config.lifeline.yaml", label="Config path"))
        .form(submit_button_label="▶ Load Data")
    )
    config_form
    return (config_form,)


@app.cell
def _(LifelineConfig, Path, config_form, mo):
    mo.stop(
        config_form.value is None,
        mo.callout(
            mo.md("Fill in the config path above and click **▶ Load Data**."),
            kind="info",
        ),
    )
    cfg = LifelineConfig.from_yaml(config_form.value["config_path"])
    silver_path = Path(cfg.storage.silver_path)
    bronze_path = Path(cfg.storage.bronze_path)
    seed_file = Path("data") / "seed" / "acs_trauma_level.parquet"
    return bronze_path, seed_file, silver_path


@app.cell
def _(gpd, mo, silver_path):
    master_path = silver_path / "lifeline_points.parquet"
    mo.stop(
        not master_path.exists(),
        mo.callout(
            mo.md(f"⚠ `{master_path}` not found — run Flow 02 first."), kind="warn"
        ),
    )
    master_gdf = gpd.read_parquet(master_path)
    health_gdf = (
        master_gdf[master_gdf["tmp_osm_layer"] == "health"].copy().reset_index(drop=True)
    )
    return (health_gdf,)


@app.cell
def _(bronze_path, load_cms_providers, mo):
    cms_bronze_file = bronze_path / "cms" / "cms_hospital_providers.parquet"
    mo.stop(
        not cms_bronze_file.exists(),
        mo.callout(
            mo.md(
                f"⚠ `{cms_bronze_file}` not found — run Flow 01 CMS download first."
            ),
            kind="warn",
        ),
    )
    cms_df = load_cms_providers(bronze_path)
    return (cms_df,)


@app.cell
def _(bronze_path, mo, pd):
    _raw = pd.read_parquet(bronze_path / "cms" / "cms_hospital_providers.parquet")
    _by_cat = (
        _raw.groupby("PRVDR_CTGRY_CD")
        .size()
        .reset_index(name="count")
        .sort_values("count", ascending=False)
        if "PRVDR_CTGRY_CD" in _raw.columns
        else pd.DataFrame(columns=["PRVDR_CTGRY_CD", "count"])
    )
    _total = len(_raw)
    mo.vstack([
        mo.callout(
            mo.md(
                f"**CMS raw file:** {_total:,} total records across all PRVDR_CTGRY_CD values — "
                f"all are now eligible for matching (no category filter)."
            ),
            kind="info",
        ),
        mo.md("**PRVDR_CTGRY_CD breakdown:**"),
        mo.ui.table(_by_cat),
    ])
    return


@app.cell
def _(load_acs_trauma, seed_file):
    acs_df = load_acs_trauma(seed_file)
    return (acs_df,)


@app.cell
def _(acs_df, cms_df, health_gdf, mo, pd, silver_path):
    def _bbox(lons, lats):
        lons_f = pd.to_numeric(lons, errors="coerce").dropna()
        lats_f = pd.to_numeric(lats, errors="coerce").dropna()
        if len(lons_f) == 0:
            return "no coordinates"
        return (
            f"lon [{float(lons_f.min()):.2f}, {float(lons_f.max()):.2f}]  "
            f"lat [{float(lats_f.min()):.2f}, {float(lats_f.max()):.2f}]"
        )

    h_bbox = _bbox(health_gdf.geometry.centroid.x, health_gdf.geometry.centroid.y)
    cms_geo_count = cms_df[cms_df["geocoded_lat"].notna() & cms_df["geocoded_lon"].notna()]
    c_bbox = (
        _bbox(cms_geo_count["geocoded_lon"], cms_geo_count["geocoded_lat"])
        if len(cms_geo_count) > 0
        else "no geocoded records"
    )
    acs_geo_count = (
        acs_df[acs_df["latitude"].notna() & acs_df["longitude"].notna()]
        if "latitude" in acs_df.columns
        else pd.DataFrame()
    )
    a_bbox = (
        _bbox(acs_geo_count["longitude"], acs_geo_count["latitude"])
        if len(acs_geo_count) > 0
        else "no coordinates"
    )

    # Addr enrichment stats — show how many silver health POIs get addr fields from attr_health.parquet
    _h_tmp = health_gdf.copy()
    for _col in ("addr:state", "addr:postcode", "addr:city", "addr:housenumber", "name", "display_name"):
        if _col not in _h_tmp.columns:
            _h_tmp[_col] = ""
        else:
            _h_tmp[_col] = _h_tmp[_col].fillna("").astype(str).str.strip()
    _h_enriched = _enrich_health_addr(_h_tmp, silver_path)
    _zip_n = (_h_enriched["addr:postcode"] != "").sum()
    _state_n = (_h_enriched["addr:state"] != "").sum()
    _hn_n = (_h_enriched["addr:housenumber"] != "").sum()

    mo.callout(
        mo.md(
            f"""
    ### Data Overview

    | Dataset | Total | Geocoded | Geographic Extent |
    |---------|-------|----------|-------------------|
    | **Silver health POIs** | {len(health_gdf):,} | {len(health_gdf):,} | `{h_bbox}` |
    | **CMS providers** | {len(cms_df):,} | {len(cms_geo_count):,} | `{c_bbox}` |
    | **ACS trauma seed** | {len(acs_df):,} | {len(acs_geo_count):,} | `{a_bbox}` |

    **Silver addr enrichment** (from `silver/attr_health.parquet` join):
    addr:postcode → **{_zip_n:,}** POIs &nbsp;·&nbsp;
    addr:state → **{_state_n:,}** &nbsp;·&nbsp;
    addr:housenumber → **{_hn_n:,}**

    > ⚠ If the geographic extents don't overlap, matching will produce zero results.
    > This is a likely root cause when you see 0 matches.
    """
        ),
        kind="info",
    )
    return


@app.cell
def _(acs_df, cms_df, gpd, health_gdf, mo, pd, silver_path):
    mo.md("---\n## QGIS Export")
    _out_dir = silver_path / "debug_qgis"
    _out_dir.mkdir(exist_ok=True)

    # Silver health POIs — already a GeoDataFrame; reproject to WGS84 for QGIS
    _health_out = _out_dir / "silver_health_pois.parquet"
    health_gdf.to_crs("EPSG:4326").to_parquet(_health_out)

    # CMS providers — build GeoDataFrame from lat/lon columns
    _cms_geo = (
        cms_df[cms_df["geocoded_lat"].notna() & cms_df["geocoded_lon"].notna()]
        .copy()
        .reset_index(drop=True)
    )
    _cms_gdf = gpd.GeoDataFrame(
        _cms_geo,
        geometry=gpd.points_from_xy(_cms_geo["geocoded_lon"], _cms_geo["geocoded_lat"]),
        crs="EPSG:4326",
    )
    _cms_out = _out_dir / "cms_hospital_providers.parquet"
    _cms_gdf.to_parquet(_cms_out)

    # ACS trauma seed — build GeoDataFrame from lat/lon columns
    _acs_geo = acs_df.dropna(subset=["latitude", "longitude"]).copy().reset_index(drop=True)
    _acs_gdf = gpd.GeoDataFrame(
        _acs_geo,
        geometry=gpd.points_from_xy(
            pd.to_numeric(_acs_geo["longitude"], errors="coerce"),
            pd.to_numeric(_acs_geo["latitude"], errors="coerce"),
        ),
        crs="EPSG:4326",
    )
    _acs_out = _out_dir / "acs_trauma_centers.parquet"
    _acs_gdf.to_parquet(_acs_out)

    mo.callout(
        mo.md(
            f"""### GeoPackages written for QGIS

    | Layer | File | Features |
    |-------|------|----------|
    | Silver health POIs | `{_health_out}` | {len(health_gdf):,} |
    | CMS hospital providers | `{_cms_out}` | {len(_cms_gdf):,} |
    | ACS trauma centers | `{_acs_out}` | {len(_acs_gdf):,} |

    Load in QGIS: **Layer → Add Layer → Add Vector Layer** → browse to each `.parquet`.
    The silver POIs may be polygons/multipolygons from OSM buildings, not points.
    """
        ),
        kind="success",
    )



    hdf = health_gdf.copy()
    hdf["lon"] = health_gdf.geometry.centroid.x
    hdf["lat"] = health_gdf.geometry.centroid.y
    hdf["poi_name"] = (
        hdf["display_name"].fillna("").astype(str)
        if "display_name" in hdf.columns
        else pd.Series("", index=hdf.index)
    )
    hdf["source"] = "Silver Health POI"
    map_health_df = hdf[["lifeline_id", "lon", "lat", "poi_name", "source"]].dropna(
        subset=["lon", "lat"]
    )

    cdf = cms_df[
        cms_df["geocoded_lat"].notna() & cms_df["geocoded_lon"].notna()
    ].copy()
    cdf["lon"] = cdf["geocoded_lon"]
    cdf["lat"] = cdf["geocoded_lat"]
    cdf["poi_name"] = (
        cdf["FAC_NAME"].fillna("").astype(str) if "FAC_NAME" in cdf.columns else ""
    )
    cdf["lifeline_id"] = (
        cdf["PRVDR_NUM"].fillna("").astype(str) if "PRVDR_NUM" in cdf.columns else ""
    )
    cdf["source"] = "CMS Provider"
    map_cms_df = cdf[["lifeline_id", "lon", "lat", "poi_name", "source"]].reset_index(
        drop=True
    )

    adf = acs_df.copy()
    adf["lon"] = pd.to_numeric(
        adf.get("longitude", pd.Series(dtype=float)), errors="coerce"
    )
    adf["lat"] = pd.to_numeric(
        adf.get("latitude", pd.Series(dtype=float)), errors="coerce"
    )
    adf["poi_name"] = (
        adf.get(
            "institution_name",
            adf.get("program_name", pd.Series("", index=adf.index)),
        )
        .fillna("")
        .astype(str)
    )
    adf["lifeline_id"] = adf.index.astype(str)
    adf["source"] = "ACS Trauma"
    map_acs_df = adf[["lifeline_id", "lon", "lat", "poi_name", "source"]].dropna(
        subset=["lon", "lat"]
    ).reset_index(drop=True)
    return map_acs_df, map_cms_df, map_health_df


@app.cell
def _(map_acs_df, map_cms_df, map_health_df, mo):
    mo.md("## Raw Data Grids\n\nSample records from each dataset. Check that lon/lat values are in the expected geographic region before tuning thresholds.")
    _h_cols = ["lifeline_id", "poi_name", "lon", "lat"]
    _c_cols = ["lifeline_id", "poi_name", "lon", "lat"]
    _a_cols = ["lifeline_id", "poi_name", "lon", "lat"]
    mo.vstack([
        mo.md("**Silver Health POIs** (first 100)"),
        mo.ui.table(map_health_df[_h_cols].head(100)),
        mo.md("**CMS Providers** (first 100)"),
        mo.ui.table(map_cms_df[_c_cols].head(100)),
        mo.md("**ACS Trauma Centers** (all)"),
        mo.ui.table(map_acs_df[_a_cols]),
    ])
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ---
    ## CMS Hospital Matching
    ### Parameters
    """)
    return


@app.cell
def _(mo):
    cms_spatial_dist = mo.ui.slider(
        start=50, stop=5000, step=50, value=200, label="Spatial distance (m)"
    )
    cms_spatial_name = mo.ui.slider(
        start=0.10,
        stop=1.00,
        step=0.05,
        value=0.55,
        label="Spatial name min score",
    )
    cms_fuzzy_thresh = mo.ui.slider(
        start=0.40, stop=1.00, step=0.05, value=0.80, label="Zip+fuzzy min score"
    )
    cms_addr_name = mo.ui.slider(
        start=0.10, stop=1.00, step=0.05, value=0.50, label="Addr+zip name min score (Tier 3)"
    )
    cms_name_zip = mo.ui.slider(
        start=0.50, stop=1.00, step=0.05, value=0.75, label="Name+zip set-ratio min score (Tier 4)"
    )
    cms_run_btn = mo.ui.run_button(label="▶ Run CMS Matching")
    mo.vstack(
        [mo.hstack([cms_spatial_dist, cms_spatial_name, cms_fuzzy_thresh, cms_addr_name, cms_name_zip]), cms_run_btn]
    )
    return (
        cms_addr_name,
        cms_fuzzy_thresh,
        cms_name_zip,
        cms_run_btn,
        cms_spatial_dist,
        cms_spatial_name,
    )


@app.cell
def _(
    bronze_path,
    build_attr_health_cms,
    cms_addr_name,
    cms_fuzzy_thresh,
    cms_name_zip,
    cms_run_btn,
    cms_spatial_dist,
    cms_spatial_name,
    mo,
    silver_path,
):
    mo.stop(not cms_run_btn.value)
    cms_attr = build_attr_health_cms(
        silver_path=silver_path,
        bronze_path=bronze_path,
        threshold=cms_fuzzy_thresh.value,
        spatial_distance_m=float(cms_spatial_dist.value),
        spatial_name_threshold=cms_spatial_name.value,
        addr_name_threshold=cms_addr_name.value,
        name_zip_threshold=cms_name_zip.value,
    )
    return (cms_attr,)


@app.cell
def _(alt, cms_attr, health_gdf, mo):
    n_cms_total = len(health_gdf)
    n_cms_matched = len(cms_attr)
    n_cms_spatial = (
        int((cms_attr["cms_match_method"] == "spatial").sum())
        if "cms_match_method" in cms_attr.columns
        else 0
    )
    n_cms_zipfuzz = (
        int((cms_attr["cms_match_method"] == "zip_fuzzy").sum())
        if "cms_match_method" in cms_attr.columns
        else 0
    )
    n_cms_addzip = (
        int((cms_attr["cms_match_method"] == "addr_zip").sum())
        if "cms_match_method" in cms_attr.columns
        else 0
    )
    n_cms_namezip = (
        int((cms_attr["cms_match_method"] == "name_zip").sum())
        if "cms_match_method" in cms_attr.columns
        else 0
    )
    n_cms_beds = (
        int((cms_attr["cms_bed_cnt"] > 0).sum())
        if "cms_bed_cnt" in cms_attr.columns
        else 0
    )

    cms_score_chart = (
        alt.Chart(cms_attr)
        .mark_bar(opacity=0.8)
        .encode(
            x=alt.X(
                "cms_match_score:Q", bin=alt.Bin(step=0.05), title="Match Score"
            ),
            y=alt.Y("count():Q", title="Count"),
            color=alt.Color("cms_match_method:N", title="Method"),
            tooltip=["cms_match_method:N", "count():Q"],
        )
        .properties(title="CMS Score Distribution", width=420, height=240)
    )

    cms_dist_data = cms_attr[cms_attr["cms_match_distance_m"].notna()].copy()
    cms_dist_chart = (
        alt.Chart(cms_dist_data)
        .mark_bar(color="#4477cc", opacity=0.8)
        .encode(
            x=alt.X(
                "cms_match_distance_m:Q",
                bin=alt.Bin(maxbins=30),
                title="Distance (m)",
            ),
            y=alt.Y("count():Q", title="Count"),
            tooltip=["count():Q"],
        )
        .properties(title="Spatial Match Distance", width=350, height=240)
    )

    mo.vstack(
        [
            mo.callout(
                mo.md(
                    f"**CMS matched:** {n_cms_matched:,} / {n_cms_total:,} "
                    f"({n_cms_matched / n_cms_total * 100:.1f}%)  &nbsp;&nbsp; "
                    f"spatial: **{n_cms_spatial:,}**  zip+fuzzy: **{n_cms_zipfuzz:,}**  "
                    f"addr+zip: **{n_cms_addzip:,}**  name+zip: **{n_cms_namezip:,}**  beds filled: **{n_cms_beds:,}**"
                ),
                kind="success" if n_cms_matched > 0 else "warn",
            ),
            mo.hstack([cms_score_chart, cms_dist_chart]),
        ]
    )
    return


@app.cell
def _(mo, pd, silver_path):
    mo.md("### CMS Match State (QAQC)")
    _state_file = silver_path / "cms_match_state.parquet"
    if not _state_file.exists():
        _display = mo.callout(mo.md("No `cms_match_state.parquet` found — run CMS matching first."), kind="neutral")
    else:
        _state = pd.read_parquet(_state_file)
        _matched = _state[_state["match_status"] == "matched"]
        _unmatched = _state[_state["match_status"] == "unmatched"]
        _tier_counts = _matched.groupby("match_tier").size().reset_index(name="count")
        _tier_md = " · ".join(f"**{row['match_tier']}**: {row['count']:,}" for _, row in _tier_counts.iterrows())

        _display = mo.vstack([
            mo.callout(
                mo.md(
                    f"CMS state: **{len(_matched):,} matched** · **{len(_unmatched):,} unmatched** "
                    f"(of {len(_state):,} total)  \n{_tier_md}"
                ),
                kind="info",
            ),
            mo.md("**Unmatched CMS providers** (candidates for manual review or threshold tuning):"),
            mo.ui.table(
                _unmatched[
                    [c for c in ["PRVDR_NUM", "FAC_NAME", "ST_ADR", "ZIP_CD", "CITY_NAME", "STATE_CD",
                                 "PRVDR_CTGRY_CD", "geocoded_lat", "geocoded_lon"] if c in _unmatched.columns]
                ].reset_index(drop=True)
            ),
        ])
    _display
    return


@app.cell
def _(cms_attr, cms_df, cms_spatial_dist, gpd, health_gdf, mo, np, pd):
    mo.md("### CMS Near-Miss Analysis")
    from rapidfuzz import fuzz as _rfuzz
    from sklearn.neighbors import BallTree as _BallTree
    import re as _re

    def _norm(name):
        if not name or not isinstance(name, str):
            return ""
        name = name.lower()
        name = _re.sub(r"[^a-z0-9 ]", " ", name)
        name = _re.sub(r"\s+", " ", name).strip()
        abbr = {"ctr": "center", "med": "medical", "univ": "university"}
        words = [abbr.get(w, w) for w in name.split()]
        name = " ".join(words)
        if name.startswith("hospital "):
            name = name[len("hospital "):]
        if name.endswith(" hospital"):
            name = name[: -len(" hospital")]
        return name.strip()

    cms_matched_lids = set(cms_attr["lifeline_id"].tolist())
    cms_unmatched = health_gdf[
        ~health_gdf["lifeline_id"].isin(cms_matched_lids)
    ].copy()
    geo_cms = (
        cms_df[cms_df["geocoded_lat"].notna() & cms_df["geocoded_lon"].notna()]
        .copy()
        .reset_index(drop=True)
    )
    cms_unmatched_geo = cms_unmatched[cms_unmatched.geometry.notna()].copy()

    near_miss_rows = []
    if len(geo_cms) > 0 and len(cms_unmatched_geo) > 0:
        cms_gdf_proj = gpd.GeoDataFrame(
            geo_cms,
            geometry=gpd.points_from_xy(geo_cms["geocoded_lon"], geo_cms["geocoded_lat"]),
            crs="EPSG:4326",
        ).to_crs("EPSG:3857")
        health_proj = (
            cms_unmatched_geo.to_crs("EPSG:3857")
            if cms_unmatched_geo.crs is not None
            else gpd.GeoDataFrame(
                cms_unmatched_geo, geometry="geometry"
            ).set_crs("EPSG:4326").to_crs("EPSG:3857")
        )

        cms_coords = np.array([[g.x, g.y] for g in cms_gdf_proj.geometry])
        health_coords = np.array(
            [
                [
                    g.centroid.x if g.geom_type != "Point" else g.x,
                    g.centroid.y if g.geom_type != "Point" else g.y,
                ]
                for g in health_proj.geometry
            ]
        )

        search_radius = float(cms_spatial_dist.value) * 5.0
        tree = _BallTree(cms_coords, metric="euclidean")
        dists, idxs = tree.query(health_coords, k=min(3, len(geo_cms)))
        dists = dists.reshape(len(health_coords), -1)
        idxs = idxs.reshape(len(health_coords), -1)

        for _i in range(len(cms_unmatched_geo)):
            _poi_row = cms_unmatched_geo.iloc[_i]
            _poi_name_raw = str(
                _poi_row.get("name") or _poi_row.get("display_name") or ""
            )
            _poi_norm = _norm(_poi_name_raw)
            for _j in range(dists.shape[1]):
                _dist_m = float(dists[_i, _j])
                if _dist_m > search_radius:
                    continue
                _cms_row = geo_cms.iloc[int(idxs[_i, _j])]
                _cms_norm = str(_cms_row.get("_name_norm", "") or "")
                _score = (
                    _rfuzz.token_sort_ratio(_poi_norm, _cms_norm) / 100.0
                    if _poi_norm and _cms_norm
                    else 0.0
                )
                near_miss_rows.append(
                    {
                        "lifeline_id": _poi_row["lifeline_id"],
                        "poi_name": _poi_name_raw,
                        "poi_name_norm": _poi_norm,
                        "cms_name": str(_cms_row.get("FAC_NAME", "") or ""),
                        "cms_name_norm": _cms_norm,
                        "cms_provider_num": str(_cms_row.get("PRVDR_NUM", "") or ""),
                        "distance_m": round(_dist_m, 1),
                        "name_score": round(_score, 3),
                        "miss_reason": (
                            "name_score_low"
                            if _dist_m <= float(cms_spatial_dist.value)
                            else "too_far"
                        ),
                    }
                )

    cms_near_miss_df = (
        pd.DataFrame(near_miss_rows).sort_values("distance_m")
        if near_miss_rows
        else pd.DataFrame(
            columns=[
                "lifeline_id",
                "poi_name",
                "cms_name",
                "distance_m",
                "name_score",
                "miss_reason",
            ]
        )
    )
    return cms_matched_lids, cms_near_miss_df


@app.cell
def _(alt, cms_near_miss_df, mo):
    if len(cms_near_miss_df) == 0:
        _display = mo.callout(
            mo.md("No CMS near-miss candidates within 5× spatial distance."),
            kind="neutral",
        )
    else:
        _scatter = (
            alt.Chart(cms_near_miss_df)
            .mark_circle(opacity=0.6, size=50)
            .encode(
                x=alt.X("distance_m:Q", title="Distance to nearest CMS point (m)"),
                y=alt.Y("name_score:Q", title="Fuzzy Name Score (0–1)", scale=alt.Scale(domain=[0, 1])),
                color=alt.Color(
                    "miss_reason:N",
                    title="Miss Reason",
                    scale=alt.Scale(
                        domain=["name_score_low", "too_far"],
                        range=["#dd6622", "#224488"],
                    ),
                ),
                tooltip=[
                    "poi_name:N",
                    "poi_name_norm:N",
                    "cms_name:N",
                    "cms_name_norm:N",
                    "distance_m:Q",
                    "name_score:Q",
                    "miss_reason:N",
                ],
            )
            .properties(
                title="CMS Near-Miss: Distance vs Name Score (each dot = closest CMS candidate for unmatched POI)",
                width=600,
                height=340,
            )
            .interactive()
        )

        _top_misses = cms_near_miss_df.drop_duplicates("lifeline_id").head(40)[
            [
                "poi_name",
                "poi_name_norm",
                "cms_name",
                "cms_name_norm",
                "distance_m",
                "name_score",
                "miss_reason",
            ]
        ]

        _display = mo.vstack(
            [
                mo.md(
                    f"**{len(cms_near_miss_df):,} near-miss candidates** (within 5× spatial distance) "
                    f"for **{cms_near_miss_df['lifeline_id'].nunique():,}** unmatched POIs"
                ),
                _scatter,
                mo.md("**Top unmatched POIs — nearest CMS candidate:**"),
                mo.ui.table(_top_misses),
            ]
        )
    _display
    return


@app.cell
def _(cms_attr, cms_matched_lids, map_health_df, mo):
    mo.md("### CMS Match Result Tables")
    _matched_df = map_health_df[map_health_df["lifeline_id"].isin(cms_matched_lids)].copy()
    _unmatched_df = map_health_df[~map_health_df["lifeline_id"].isin(cms_matched_lids)].copy()
    _matched_detail = cms_attr[["lifeline_id", "cms_fac_name", "cms_match_score", "cms_match_method", "cms_match_distance_m", "cms_bed_cnt"]].copy() if "cms_fac_name" in cms_attr.columns else cms_attr.head(0)
    mo.vstack([
        mo.md(f"**Matched** ({len(_matched_df):,} POIs)"),
        mo.ui.table(_matched_detail.head(200)) if len(_matched_detail) > 0 else mo.ui.table(_matched_df[["lifeline_id", "poi_name", "lon", "lat"]].head(200)),
        mo.md(f"**Unmatched** ({len(_unmatched_df):,} POIs — first 200)"),
        mo.ui.table(_unmatched_df[["lifeline_id", "poi_name", "lon", "lat"]].head(200)),
    ])
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ---
    ## ACS Trauma Center Matching
    ### Parameters
    """)
    return


@app.cell
def _(mo):
    acs_spatial_dist = mo.ui.slider(
        start=50, stop=5000, step=50, value=200, label="Spatial distance (m)"
    )
    acs_name_thresh = mo.ui.slider(
        start=0.10, stop=1.00, step=0.05, value=0.70, label="Name min score"
    )
    acs_run_btn = mo.ui.run_button(label="▶ Run ACS Matching")
    mo.vstack([mo.hstack([acs_spatial_dist, acs_name_thresh]), acs_run_btn])
    return acs_name_thresh, acs_run_btn, acs_spatial_dist


@app.cell
def _(
    acs_name_thresh,
    acs_run_btn,
    acs_spatial_dist,
    build_attr_health_acs_trauma,
    mo,
    seed_file,
    silver_path,
):
    mo.stop(not acs_run_btn.value)
    acs_attr = build_attr_health_acs_trauma(
        silver_path=silver_path,
        seed_path=seed_file,
        max_distance_m=float(acs_spatial_dist.value),
        name_threshold=acs_name_thresh.value,
    )
    return (acs_attr,)


@app.cell
def _(acs_attr, alt, health_gdf, mo):
    n_acs_total = len(health_gdf)
    n_acs_matched = len(acs_attr)
    n_acs_trauma = (
        int(acs_attr["acs_trauma_level"].notna().sum())
        if "acs_trauma_level" in acs_attr.columns
        else 0
    )

    acs_dist_chart = (
        alt.Chart(acs_attr)
        .mark_bar(color="#8833bb", opacity=0.8)
        .encode(
            x=alt.X(
                "acs_match_distance_m:Q",
                bin=alt.Bin(maxbins=30),
                title="Match Distance (m)",
            ),
            y=alt.Y("count():Q", title="Count"),
            tooltip=["count():Q"],
        )
        .properties(title="ACS Match Distance Distribution", width=400, height=240)
    )

    acs_trauma_data = (
        acs_attr[acs_attr["acs_trauma_level"].notna()]
        if "acs_trauma_level" in acs_attr.columns
        else acs_attr.head(0)
    )
    acs_trauma_chart = (
        alt.Chart(acs_trauma_data)
        .mark_bar(color="#8833bb", opacity=0.8)
        .encode(
            x=alt.X("acs_trauma_level:N", sort=None, title="Trauma Level"),
            y=alt.Y("count():Q", title="Count"),
            tooltip=["acs_trauma_level:N", "count():Q"],
        )
        .properties(title="Matched Trauma Levels", width=350, height=240)
    )

    mo.vstack(
        [
            mo.callout(
                mo.md(
                    f"**ACS matched:** {n_acs_matched:,} / {n_acs_total:,} "
                    f"({n_acs_matched / n_acs_total * 100:.1f}%)  &nbsp;&nbsp; "
                    f"trauma level assigned: **{n_acs_trauma:,}**"
                ),
                kind="success" if n_acs_matched > 0 else "warn",
            ),
            mo.hstack([acs_dist_chart, acs_trauma_chart]),
        ]
    )
    return


@app.cell
def _(acs_attr, acs_df, acs_spatial_dist, health_gdf, mo, np, pd):
    mo.md("### ACS Near-Miss Analysis")
    from rapidfuzz import fuzz as _rfuzz2
    from sklearn.neighbors import BallTree as _AcsBT
    import re as _re2

    def _acs_norm(name):
        if not name or not isinstance(name, str):
            return ""
        name = name.lower()
        name = _re2.sub(r"[^a-z0-9 ]", " ", name)
        return _re2.sub(r"\s+", " ", name).strip()

    acs_matched_lids = set(acs_attr["lifeline_id"].tolist())
    acs_unmatched = health_gdf[
        ~health_gdf["lifeline_id"].isin(acs_matched_lids)
    ].copy()
    acs_geo = (
        acs_df.dropna(subset=["latitude", "longitude"])
        .copy()
        .reset_index(drop=True)
        if "latitude" in acs_df.columns
        else pd.DataFrame()
    )

    acs_near_rows = []
    EARTH_R = 6_371_000.0
    if len(acs_geo) > 0 and len(acs_unmatched) > 0:
        acs_unmatched_geo = acs_unmatched[acs_unmatched.geometry.notna()].copy()
        if len(acs_unmatched_geo) > 0:
            acs_coords_rad = np.radians(acs_geo[["latitude", "longitude"]].values)
            acs_tree = _AcsBT(acs_coords_rad, metric="haversine")

            poi_lons = acs_unmatched_geo.geometry.x.values
            poi_lats = acs_unmatched_geo.geometry.y.values
            search_rad_r = float(acs_spatial_dist.value) * 10.0 / EARTH_R
            poi_rad = np.column_stack(
                [np.radians(poi_lats), np.radians(poi_lons)]
            )
            valid = np.isfinite(poi_lats) & np.isfinite(poi_lons)
            poi_rad = poi_rad[valid]
            acs_unmatched_valid = acs_unmatched_geo.iloc[
                np.where(valid)[0]
            ].reset_index(drop=True)

            indices_a, dists_a = acs_tree.query_radius(
                poi_rad, r=search_rad_r, return_distance=True, sort_results=True
            )

            for _i in range(len(acs_unmatched_valid)):
                if len(indices_a[_i]) == 0:
                    continue
                _poi_row = acs_unmatched_valid.iloc[_i]
                _poi_name_raw = str(
                    _poi_row.get("name") or _poi_row.get("display_name") or ""
                )
                _poi_norm = _acs_norm(_poi_name_raw)
                for _k in range(min(3, len(indices_a[_i]))):
                    _acs_idx = int(indices_a[_i][_k])
                    _dist_m = float(dists_a[_i][_k] * EARTH_R)
                    _acs_row = acs_geo.iloc[_acs_idx]
                    _acs_name_norm = str(_acs_row.get("_name_norm", "") or "")
                    _score = (
                        _rfuzz2.token_sort_ratio(_poi_norm, _acs_name_norm) / 100.0
                        if _poi_norm and _acs_name_norm
                        else 0.0
                    )
                    acs_near_rows.append(
                        {
                            "lifeline_id": _poi_row["lifeline_id"],
                            "poi_name": _poi_name_raw,
                            "poi_name_norm": _poi_norm,
                            "acs_name": str(
                                _acs_row.get(
                                    "institution_name",
                                    _acs_row.get("program_name", ""),
                                )
                                or ""
                            ),
                            "acs_name_norm": _acs_name_norm,
                            "trauma_level": str(
                                _acs_row.get("trauma_level", "") or ""
                            ),
                            "distance_m": round(_dist_m, 1),
                            "name_score": round(_score, 3),
                            "miss_reason": (
                                "name_score_low"
                                if _dist_m <= float(acs_spatial_dist.value)
                                else "too_far"
                            ),
                        }
                    )

    acs_near_miss_df = (
        pd.DataFrame(acs_near_rows).sort_values("distance_m")
        if acs_near_rows
        else pd.DataFrame(
            columns=[
                "lifeline_id",
                "poi_name",
                "acs_name",
                "distance_m",
                "name_score",
                "miss_reason",
            ]
        )
    )
    return acs_matched_lids, acs_near_miss_df


@app.cell
def _(acs_near_miss_df, alt, mo):
    if len(acs_near_miss_df) == 0:
        _display = mo.callout(
            mo.md("No ACS near-miss candidates within 10× spatial distance."),
            kind="neutral",
        )
    else:
        _acs_scatter = (
            alt.Chart(acs_near_miss_df)
            .mark_circle(opacity=0.6, size=50)
            .encode(
                x=alt.X("distance_m:Q", title="Distance to nearest ACS point (m)"),
                y=alt.Y("name_score:Q", title="Fuzzy Name Score (0–1)", scale=alt.Scale(domain=[0, 1])),
                color=alt.Color(
                    "miss_reason:N",
                    title="Miss Reason",
                    scale=alt.Scale(
                        domain=["name_score_low", "too_far"],
                        range=["#dd6622", "#224488"],
                    ),
                ),
                tooltip=[
                    "poi_name:N",
                    "poi_name_norm:N",
                    "acs_name:N",
                    "acs_name_norm:N",
                    "trauma_level:N",
                    "distance_m:Q",
                    "name_score:Q",
                    "miss_reason:N",
                ],
            )
            .properties(
                title="ACS Near-Miss: Distance vs Name Score",
                width=600,
                height=340,
            )
            .interactive()
        )

        _top_acs_misses = acs_near_miss_df.drop_duplicates("lifeline_id").head(40)[
            [
                "poi_name",
                "poi_name_norm",
                "acs_name",
                "acs_name_norm",
                "trauma_level",
                "distance_m",
                "name_score",
                "miss_reason",
            ]
        ]

        _display = mo.vstack(
            [
                mo.md(
                    f"**{len(acs_near_miss_df):,} near-miss candidates** (within 10× spatial distance) "
                    f"for **{acs_near_miss_df['lifeline_id'].nunique():,}** unmatched POIs"
                ),
                _acs_scatter,
                mo.md("**Top unmatched POIs — nearest ACS candidate:**"),
                mo.ui.table(_top_acs_misses),
            ]
        )
    _display
    return


@app.cell
def _(acs_attr, acs_matched_lids, map_acs_df, map_health_df, mo):
    mo.md("### ACS Match Result Tables")
    _acs_matched_df = map_health_df[map_health_df["lifeline_id"].isin(acs_matched_lids)].copy()
    _acs_unmatched_df = map_health_df[~map_health_df["lifeline_id"].isin(acs_matched_lids)].copy()
    _acs_detail_cols = [c for c in ["lifeline_id", "acs_institution_name", "acs_trauma_level", "acs_match_score", "acs_match_distance_m"] if c in acs_attr.columns]
    mo.vstack([
        mo.md("**ACS Trauma Centers**"),
        mo.ui.table(map_acs_df[["lifeline_id", "poi_name", "lon", "lat"]]),
        mo.md(f"**Matched POIs** ({len(_acs_matched_df):,})"),
        mo.ui.table(acs_attr[_acs_detail_cols].head(200)) if _acs_detail_cols else mo.ui.table(_acs_matched_df[["lifeline_id", "poi_name", "lon", "lat"]].head(200)),
        mo.md(f"**Unmatched POIs** ({len(_acs_unmatched_df):,} — first 200)"),
        mo.ui.table(_acs_unmatched_df[["lifeline_id", "poi_name", "lon", "lat"]].head(200)),
    ])
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ---
    ## Name Normalization Inspector

    Type any hospital name to see how each normalizer transforms it and find the top
    fuzzy matches in the CMS and ACS datasets. Use this to diagnose why a specific
    hospital isn't matching.
    """)
    return


@app.cell
def _(mo):
    name_query = mo.ui.text(
        value="",
        placeholder="e.g. St. Mary's Medical Center",
        label="Hospital name to inspect",
        full_width=True,
    )
    name_query
    return (name_query,)


@app.cell
def _(
    acs_df,
    acs_normalize_name,
    cms_df,
    cms_normalize_name,
    mo,
    name_query,
    pd,
):
    from rapidfuzz import fuzz as _rfuzz3

    query_raw = name_query.value.strip()
    mo.stop(not query_raw)

    query_norm_cms = cms_normalize_name(query_raw)
    query_norm_acs = acs_normalize_name(query_raw)

    cms_names_list = (
        cms_df["_name_norm"].fillna("").tolist() if "_name_norm" in cms_df.columns else []
    )
    cms_fac_list = (
        cms_df["FAC_NAME"].fillna("").tolist() if "FAC_NAME" in cms_df.columns else []
    )
    cms_states = (
        cms_df["STATE_CD"].fillna("").tolist() if "STATE_CD" in cms_df.columns else [""] * len(cms_names_list)
    )
    cms_cities = (
        cms_df["CITY_NAME"].fillna("").tolist() if "CITY_NAME" in cms_df.columns else [""] * len(cms_names_list)
    )
    cms_scores_list = [
        _rfuzz3.token_sort_ratio(query_norm_cms, n) / 100.0 for n in cms_names_list
    ]
    cms_top_idx = sorted(
        range(len(cms_scores_list)), key=lambda i: cms_scores_list[i], reverse=True
    )[:10]
    cms_top_df = pd.DataFrame(
        [
            {
                "original_name": cms_fac_list[i] if i < len(cms_fac_list) else "",
                "normalized": cms_names_list[i],
                "score": round(cms_scores_list[i], 3),
                "state": cms_states[i],
                "city": cms_cities[i],
            }
            for i in cms_top_idx
        ]
    )

    acs_names_list = (
        acs_df["_name_norm"].fillna("").tolist() if "_name_norm" in acs_df.columns else []
    )
    acs_inst_list = (
        acs_df.get(
            "institution_name", acs_df.get("program_name", pd.Series())
        )
        .fillna("")
        .tolist()
    )
    acs_state_list = (
        acs_df["state"].fillna("").tolist() if "state" in acs_df.columns else [""] * len(acs_names_list)
    )
    acs_scores_list = [
        _rfuzz3.token_sort_ratio(query_norm_acs, n) / 100.0 for n in acs_names_list
    ]
    acs_top_idx = sorted(
        range(len(acs_scores_list)), key=lambda i: acs_scores_list[i], reverse=True
    )[:10]
    acs_top_df = pd.DataFrame(
        [
            {
                "original_name": acs_inst_list[i] if i < len(acs_inst_list) else "",
                "normalized": acs_names_list[i],
                "score": round(acs_scores_list[i], 3),
                "state": acs_state_list[i],
            }
            for i in acs_top_idx
        ]
    )

    mo.vstack(
        [
            mo.md(
                f"**Input:** `{query_raw}`  \n"
                f"**CMS normalized:** `{query_norm_cms}`  \n"
                f"**ACS normalized:** `{query_norm_acs}`"
            ),
            mo.hstack(
                [
                    mo.vstack(
                        [mo.md("#### Top 10 CMS matches (token_sort_ratio)"), mo.ui.table(cms_top_df)]
                    ),
                    mo.vstack(
                        [mo.md("#### Top 10 ACS matches (token_sort_ratio)"), mo.ui.table(acs_top_df)]
                    ),
                ]
            ),
        ]
    )
    return


if __name__ == "__main__":
    app.run()
