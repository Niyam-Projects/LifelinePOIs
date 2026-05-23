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
    # Flow 00 · Setup — Overture Maps Address Download

    Downloads the Overture Maps address dataset from public S3 into a Bronze GeoParquet file.
    Overture provides global address data; this defaults to US-only to keep file sizes manageable.

    **Interactive:** fill in the form below and click **▶ Run Setup**.
    **Script:** `marimo run flows/00_setup.py -- --help`
    """)
    return


@app.cell
def _():
    from pathlib import Path
    from pydantic import BaseModel, Field

    import sys as _sys
    _sys.path.insert(0, str(Path(".").resolve()))
    from src.lifelinepoi.config import LifelineConfig

    return BaseModel, Field, LifelineConfig


@app.cell
def _(BaseModel, Field):
    class FlowParams(BaseModel):
        config_path: str = Field(default="config.lifeline.yaml", description="Path to config YAML file")
        region: str = Field(default="US", description="Country filter: 'US' includes all territories (PR, VI, GU, MP, AS, UM), any ISO-3166 code (e.g. DE, GB), or 'all' for global")
        release: str = Field(default="latest", description="Overture Maps release version (e.g. 2026-04-15.0) or 'latest' to auto-discover")
        output_dir: str = Field(default="", description="Output directory for hive-partitioned parquet (blank = auto from config bronze_path)")
        run_overture_places: bool = Field(default=True, description="Download Overture Places snapshot (hospitals + configured taxonomies) for US + territories")

    return (FlowParams,)


@app.cell
def _(mo):
    params_form = (
        mo.md("""
        ## Parameters

        Config file: {config_path}

        Region / country code (or `all` for global): {region}

        Overture release (`latest` to auto-discover): {release}

        Output directory (blank = `{{bronze}}/overture/addresses/`): {output_dir}

        Steps to run:
        - {run_overture_places} Overture Places download (hospitals + configured taxonomies, US + territories)
        """)
        .batch(
            config_path=mo.ui.text(value="config.lifeline.yaml", label="Config path"),
            region=mo.ui.text(value="US", label=""),
            release=mo.ui.text(value="latest", label=""),
            output_dir=mo.ui.text(value="", placeholder="leave blank for default", label=""),
            run_overture_places=mo.ui.checkbox(value=True, label=""),
        )
        .form(submit_button_label="▶ Run Setup")
    )
    params_form
    return (params_form,)


@app.cell
def _(FlowParams, mo):
    import sys as _sys
    is_script_mode = mo.app_meta().mode == "script"
    if is_script_mode and "help" in mo.cli_args():
        print("Usage: marimo run flows/00_setup.py -- [options]\n")
        for _name, _field in FlowParams.model_fields.items():
            _default = f"(default: {_field.default!r})" if _field.default is not None else "(required)"
            print(f"  --{_name.replace('_', '-'):<24} {_field.description} {_default}")
        _sys.exit(0)
    return (is_script_mode,)


@app.cell
def _(FlowParams, is_script_mode, mo, params_form):
    mo.stop(
        not is_script_mode and params_form.value is None,
        mo.callout(mo.md("**Fill in the parameters above and click _Run Setup_ to start.**"), kind="info"),
    )
    if is_script_mode:
        flow_params = FlowParams(**{k.replace("-", "_"): v for k, v in mo.cli_args().items()})
    else:
        flow_params = FlowParams(**params_form.value)
    return (flow_params,)


@app.cell
def _(LifelineConfig, flow_params, mo):
    cfg = LifelineConfig.from_yaml(flow_params.config_path)
    mo.md(f"**Config loaded.** Storage root: `{cfg.storage.root}`  |  Bronze: `{cfg.storage.bronze_path}`")
    return (cfg,)


@app.cell
def _(cfg, flow_params, mo):
    import duckdb
    import subprocess as _sp
    import time as _time
    from pathlib import Path as _P

    def _resolve_release(release: str, con: duckdb.DuckDBPyConnection) -> str:
        """Return the versioned release string, auto-discovering if 'latest'."""
        if release.strip().lower() != "latest":
            return release.strip()
        # List S3 prefixes and pick the lexicographically last dated release
        result = _sp.run(
            ["aws", "s3", "ls", "--no-sign-request",
             "s3://overturemaps-us-west-2/release/"],
            capture_output=True, text=True, timeout=30,
        )
        releases = [
            line.split()[-1].rstrip("/")
            for line in result.stdout.splitlines()
            if line.strip() and line.split()[-1].rstrip("/")[0].isdigit()
        ]
        if not releases:
            raise RuntimeError("Could not list Overture releases from S3")
        latest = sorted(releases)[-1]
        print(f"  Auto-discovered latest release: {latest}")
        return latest

    def _download_overture_addresses():
        # Resolve output directory (hive-partitioned tree, not a single file)
        if flow_params.output_dir:
            out = _P(flow_params.output_dir)
        else:
            out = _P(cfg.storage.bronze_path) / "overture" / "addresses"
        out.mkdir(parents=True, exist_ok=True)

        sentinel = out / ".downloaded"
        if sentinel.exists():
            print(f"  Already exists → {out}  (delete .downloaded sentinel to re-download)")
            return out

        region = flow_params.region.strip().upper()

        # US includes all territories (ISO 3166-1 alpha-2 codes used by Overture/OSM)
        US_COUNTRY_CODES = ("'US'", "'PR'", "'VI'", "'GU'", "'MP'", "'AS'", "'UM'")
        # All US + territory postal codes are 5-digit numeric (PR 006xx, GU 969xx, etc.)
        # Exclude NULLs, empty strings, and non-numeric/wrong-length codes that Overture
        # sometimes carries from OSM — they'd land in __HIVE_DEFAULT_PARTITION__ otherwise.
        VALID_POSTCODE = "regexp_matches(postcode, '^\\d{5}$')"
        if region == "ALL":
            where_clause = ""
            region_label = "global"
        elif region == "US":
            where_clause = f"WHERE country IN ({', '.join(US_COUNTRY_CODES)}) AND {VALID_POSTCODE}"
            region_label = "US + territories (PR, VI, GU, MP, AS, UM)"
        else:
            where_clause = f"WHERE country = '{region}' AND {VALID_POSTCODE}"
            region_label = region

        out_str = str(out).replace("\\", "/")

        con = duckdb.connect()
        try:
            con.execute("INSTALL spatial; LOAD spatial; INSTALL httpfs; LOAD httpfs;")
            con.execute("SET s3_region = 'us-west-2';")

            release = _resolve_release(flow_params.release, con)
            s3_path = f"s3://overturemaps-us-west-2/release/{release}/theme=addresses/type=address/*"

            # Hive-partition by country → state_code only. Data is sorted by postcode
            # within each partition so DuckDB row-group statistics let it skip irrelevant
            # row groups on postcode predicates — same effective seek without ~42K folders.
            # Native Overture schema (2026-04-15+) column names are used directly.
            # address_levels[1].value = 2-letter state/province abbreviation (e.g. "AZ")
            # address_levels[2].value = municipality (local admin boundary, may be NULL)
            # Bad/missing postcodes are excluded by the WHERE clause above.
            query = f"""
                COPY (
                    SELECT
                        id,
                        number,
                        street,
                        unit,
                        postal_city,
                        address_levels[1].value AS state_code,
                        address_levels[2].value AS municipality,
                        postcode,
                        country,
                        geometry
                    FROM read_parquet('{s3_path}', hive_partitioning=false)
                    {where_clause}
                ) TO '{out_str}' (
                    FORMAT PARQUET,
                    PARTITION_BY (country, state_code),
                    OVERWRITE_OR_IGNORE,
                    COMPRESSION 'ZSTD'
                )
            """

            print(f"  Overture release: {release}")
            print(f"  Overture region: {region_label}")
            print(f"  Output directory: {out}")
            print("  Connecting to Overture S3 (public bucket, no credentials needed)…")

            t0 = _time.perf_counter()
            con.execute(query)
            elapsed = _time.perf_counter() - t0
            # Count written files as a quick sanity check
            parquet_files = list(out.rglob("*.parquet"))
            sentinel.touch()
            print(f"  Done in {elapsed:.1f}s — {len(parquet_files):,} partition files written")
            print(f"  Layout: {out}/country=*/state_code=*/")
        finally:
            con.close()
        return out

    print("[Overture] Starting address download")
    try:
        _result_path = _download_overture_addresses()
        setup_result = mo.callout(
            mo.md(f"✅ **Overture addresses downloaded.** → `{_result_path}`"),
            kind="success",
        )
    except Exception as _e:
        setup_result = mo.callout(mo.md(f"❌ **Download failed:** {_e}"), kind="danger")
        print(f"  ERROR: {_e}")
    setup_result
    return (setup_result,)


@app.cell
def _(cfg, flow_params, mo):
    """Download Overture Places (hospitals + configured taxonomy) for US + territories."""
    from pathlib import Path as _P

    if not flow_params.run_overture_places:
        overture_places_result = mo.callout(mo.md("⏭ Overture Places download skipped."), kind="neutral")
    else:
        import sys as _sys
        _sys.path.insert(0, str(_P(".").resolve()))
        from lib.overture import download_overture_snapshot, get_latest_release_date
        from lib.boundary import get_us_pr_boundary
        import time as _time

        _overture_cfg = cfg.overture
        _bronze_path = _P(cfg.storage.bronze_path)

        # Resolve output path
        if _overture_cfg.places_path:
            _output_path = _P(_overture_cfg.places_path)
        else:
            _output_path = _bronze_path / "overture" / "places" / "us_territories.parquet"

        _CENSUS_BOUNDARY_URL = (
            "https://www2.census.gov/geo/tiger/GENZ2023/shp/cb_2023_us_state_5m.zip"
        )
        _boundary_cache = _P(".").resolve() / "data" / "reference" / "census_boundary"

        if _output_path.exists():
            overture_places_result = mo.callout(
                mo.md(
                    f"ℹ️ **Overture Places already downloaded.** → `{_output_path}`  \n"
                    f"Delete the file to re-download."
                ),
                kind="neutral",
            )
        else:
            try:
                t0 = _time.perf_counter()

                print("[Overture Places] Downloading Census boundary for US + territories…")
                _boundary_gdf, _coarse_bboxes = get_us_pr_boundary(
                    source_url=_CENSUS_BOUNDARY_URL,
                    cache_dir=_boundary_cache,
                )
                print(f"  Boundary loaded. Coarse bboxes: {len(_coarse_bboxes)}")

                _release = _overture_cfg.release
                if _release.strip().lower() == "latest":
                    _release = get_latest_release_date(bucket=_overture_cfg.bucket)
                    print(f"  Auto-discovered Overture release: {_release}")

                taxonomy_label = ", ".join(
                    f"{t[0]}/{t[1]}" if len(t) > 1 and t[1] else t[0]
                    for t in _overture_cfg.places_taxonomy
                )
                print(f"[Overture Places] Taxonomy: {taxonomy_label}")
                print(f"[Overture Places] Output:   {_output_path}")

                _result = download_overture_snapshot(
                    output_path=_output_path,
                    taxonomy_allowlist=_overture_cfg.places_taxonomy,
                    boundary_gdf=_boundary_gdf,
                    coarse_bboxes=_coarse_bboxes,
                    bucket=_overture_cfg.bucket,
                    s3_region=_overture_cfg.s3_region,
                    release_date=_release,
                    source_label="overture",
                    duckdb_memory_limit=_overture_cfg.duckdb_memory_limit,
                    duckdb_threads=_overture_cfg.duckdb_threads,
                    workers=_overture_cfg.workers,
                )
                _elapsed = _time.perf_counter() - t0
                _size_mb = _result.stat().st_size / 1_048_576
                print(f"  Done in {_elapsed:.1f}s — {_size_mb:.1f} MB → {_result}")
                overture_places_result = mo.callout(
                    mo.md(
                        f"✅ **Overture Places downloaded.** `{taxonomy_label}` · "
                        f"`{_size_mb:.0f} MB` → `{_result}`"
                    ),
                    kind="success",
                )
            except Exception as _e:
                overture_places_result = mo.callout(
                    mo.md(f"❌ **Overture Places download failed:** {_e}"), kind="danger"
                )
                print(f"  ERROR: {_e}")

    overture_places_result
    return (overture_places_result,)


@app.cell
def _(is_script_mode, mo, setup_result, overture_places_result):
    if is_script_mode:
        print("Setup Summary")
        print("Flow 00 complete. Run flows/01_ingest.py next to ingest OSM + federal data sources.")
    else:
        mo.vstack([
            mo.md("## Setup Summary"),
            setup_result,
            overture_places_result,
            mo.callout(
                mo.md("✅ **Flow 00 complete.** ➡ Run `flows/01_ingest.py` next to ingest OSM + federal data sources."),
                kind="success",
            ),
        ])
    return


if __name__ == "__main__":
    app.run()
