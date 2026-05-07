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
    # Flow 01 · Ingestion

    Extracts OSM infrastructure layers from a local PBF file into Bronze GeoParquet,
    then downloads EIA Form 860/923 and EPA FRS data.

    **Interactive:** fill in the form below and click **Run Ingestion**.
    **Script:** `marimo run flows/01_ingest.py -- --help` (or `-- --config ... --run-osm true`)
    """)
    return


@app.cell
def _():
    import time
    import zipfile
    from pathlib import Path

    import httpx
    from tqdm import tqdm
    from pydantic import BaseModel, Field

    import sys as _sys
    _sys.path.insert(0, str(Path(".").resolve()))
    from src.lifelinepoi.config import LifelineConfig
    from lib.duckdb_utils import get_connection, run_layer_sql

    return (
        BaseModel,
        Field,
        LifelineConfig,
        get_connection,
        httpx,
        run_layer_sql,
        tqdm,
        zipfile,
    )


@app.cell
def _(BaseModel, Field):
    class FlowParams(BaseModel):
        config_path: str = Field(default="config.lifeline.yaml", description="Path to config YAML file")
        run_osm: bool = Field(default=True, description="Run OSM PBF extraction")
        run_eia: bool = Field(default=True, description="Download EIA Form 860/923")
        run_epa: bool = Field(default=True, description="Download EPA FRS data")
        layer: str = Field(default="", description="Single OSM layer (empty = all layers)")

    return (FlowParams,)


@app.cell
def _(mo):
    params_form = (
        mo.md("""
        ## Parameters

        Config file: {config_path}

        Steps to run:
        - {run_osm} OSM PBF extraction
        - {run_eia} EIA Form 860/923 download
        - {run_epa} EPA FRS download

        Single layer override (blank = all layers): {layer}
        """)
        .batch(
            config_path=mo.ui.text(value="config.lifeline.yaml", label="Config path"),
            run_osm=mo.ui.checkbox(value=True, label=""),
            run_eia=mo.ui.checkbox(value=True, label=""),
            run_epa=mo.ui.checkbox(value=True, label=""),
            layer=mo.ui.text(value="", placeholder="e.g. power", label=""),
        )
        .form(submit_button_label="▶ Run Ingestion")
    )
    params_form
    return (params_form,)


@app.cell
def _(FlowParams, mo):
    import sys as _sys
    is_script_mode = mo.app_meta().mode == "script"
    if is_script_mode and (not mo.cli_args() or "help" in mo.cli_args()):
        print("Usage: marimo run flows/01_ingest.py -- [options]\n")
        for _name, _field in FlowParams.model_fields.items():
            _default = f"(default: {_field.default})" if _field.default is not None else "(required)"
            print(f"  --{_name.replace('_', '-'):<28} {_field.description} {_default}")
        _sys.exit(0)
    return (is_script_mode,)


@app.cell
def _(FlowParams, is_script_mode, mo, params_form):
    mo.stop(
        not is_script_mode and params_form.value is None,
        mo.callout(mo.md("**Fill in the parameters above and click _Run Ingestion_ to start.**"), kind="info"),
    )
    if is_script_mode:
        flow_params = FlowParams(**{k.replace("-", "_"): v for k, v in mo.cli_args().items()})
    else:
        flow_params = FlowParams(**params_form.value)
    return (flow_params,)


@app.cell
def _(LifelineConfig, flow_params, mo):
    cfg = LifelineConfig.from_yaml(flow_params.config_path)
    mo.md(f"**Config loaded.** Layers: `{', '.join(cfg.osm.layers)}`  |  PBF: `{cfg.osm.pbf_path}`")
    return (cfg,)


@app.cell
def _(cfg, flow_params, get_connection, mo, run_layer_sql):
    def _ingest_osm(layers=None):
        from pathlib import Path as _P
        import time as _t
        pbf_path = _P(cfg.osm.pbf_path)
        if not pbf_path.exists():
            raise FileNotFoundError(
                f"OSM PBF file not found: {pbf_path}\n"
                "Download a regional extract from https://download.geofabrik.de/ "
                "and set osm.pbf_path in config.lifeline.yaml"
            )
        sql_dir = _P(".").resolve() / "sql"
        bronze_osm = _P(cfg.storage.bronze_path) / "osm"
        bronze_osm.mkdir(parents=True, exist_ok=True)
        target_layers = layers or cfg.osm.layers
        print(f"OSM PBF: {pbf_path}")
        print(f"Layers to extract: {target_layers}")
        conn = get_connection(cfg.duckdb.memory_limit)
        try:
            for _lyr in target_layers:
                output_path = bronze_osm / f"{_lyr}.parquet"
                print(f"  Extracting layer: {_lyr} -> {output_path}")
                t0 = _t.perf_counter()
                run_layer_sql(
                    conn=conn, sql_dir=sql_dir, layer=_lyr,
                    pbf_path=str(pbf_path), output_path=str(output_path),
                    osmium_index_type=cfg.osm.osmium_index_type,
                )
                elapsed = _t.perf_counter() - t0
                size_mb = output_path.stat().st_size / 1_048_576
                print(f"    Done in {elapsed:.1f}s — {size_mb:.1f} MB")
        finally:
            conn.close()

    if flow_params.run_osm:
        print("[OSM] Starting PBF extraction")
        _ingest_osm(layers=[flow_params.layer] if flow_params.layer else None)
        osm_result = mo.callout(mo.md("✅ **OSM extraction complete.**"), kind="success")
    else:
        osm_result = mo.callout(mo.md("⏭ OSM extraction skipped."), kind="neutral")
    osm_result
    return (osm_result,)


@app.cell
def _(cfg, flow_params, httpx, mo, tqdm, zipfile):
    def _download_file(url, dest, label):
        from pathlib import Path as _P
        dest.parent.mkdir(parents=True, exist_ok=True)
        print(f"  Downloading {label} from {url}")
        with httpx.stream("GET", url, follow_redirects=True, timeout=120) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length", 0))
            with open(dest, "wb") as f, tqdm(
                total=total, unit="B", unit_scale=True, desc=label, leave=False
            ) as bar:
                for chunk in r.iter_bytes(chunk_size=65536):
                    f.write(chunk)
                    bar.update(len(chunk))
        return dest

    def _extract_zip(zip_path, extract_dir):
        """Extract zip to extract_dir, skip if sentinel file already present."""
        from pathlib import Path as _P
        sentinel = extract_dir / ".extracted"
        if sentinel.exists():
            print(f"    Already extracted → {extract_dir}")
            return
        extract_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path) as z:
            members = z.namelist()
            print(f"    Extracting {len(members)} files → {extract_dir}")
            z.extractall(extract_dir)
        sentinel.touch()
        print(f"    Extraction complete.")

    def _ingest_eia():
        from pathlib import Path as _P
        bronze_eia = _P(cfg.storage.bronze_path) / "eia"
        bronze_eia.mkdir(parents=True, exist_ok=True)
        products = [
            (cfg.eia.form860_zip_url, "eia860_2023.zip", bronze_eia / "eia860"),
            (cfg.eia.form923_zip_url, "eia923_2023.zip", bronze_eia / "eia923"),
        ]
        for url, name, extract_dir in products:
            dest = bronze_eia / name
            # Download if not already on disk
            if not dest.exists():
                try:
                    _download_file(url, dest, name)
                    print(f"  EIA {name}: downloaded ({dest.stat().st_size / 1_048_576:.1f} MB)")
                except zipfile.BadZipFile:
                    dest.unlink(missing_ok=True)
                    print(f"  ERROR: {name} is not a valid zip — check eia.*_zip_url in config.")
                    continue
                except Exception as e:
                    print(f"  WARNING: Failed to download {name}: {e}")
                    continue
            else:
                print(f"  EIA {name}: already downloaded, skipping download")
            # Extract
            try:
                _extract_zip(dest, extract_dir)
            except zipfile.BadZipFile:
                print(f"  ERROR: {name} on disk is corrupt — delete it and re-run to re-download.")

    if flow_params.run_eia:
        print("[EIA] Starting Form 860/923 download + extraction")
        _ingest_eia()
        eia_result = mo.callout(mo.md("✅ **EIA download + extraction complete.**"), kind="success")
    else:
        eia_result = mo.callout(mo.md("⏭ EIA download skipped."), kind="neutral")
    eia_result
    return (eia_result,)


@app.cell
def _(cfg, flow_params, httpx, mo, tqdm, zipfile):
    def _ingest_epa():
        import duckdb as _duckdb
        from pathlib import Path as _P
        bronze_epa = _P(cfg.storage.bronze_path) / "epa"
        bronze_epa.mkdir(parents=True, exist_ok=True)
        dest = bronze_epa / "frs_national_single.zip"
        extract_dir = bronze_epa / "frs_national"
        extract_sentinel = extract_dir / ".extracted"
        parquet_out = bronze_epa / "frs_national.parquet"
        url = cfg.epa.frs_zip_url

        # --- Download ---
        if not dest.exists():
            try:
                print(f"  Downloading EPA FRS national_single from {url}")
                with httpx.stream("GET", url, follow_redirects=True, timeout=300) as r:
                    r.raise_for_status()
                    total = int(r.headers.get("content-length", 0))
                    with open(dest, "wb") as f, tqdm(
                        total=total, unit="B", unit_scale=True, desc="frs_national_single.zip", leave=False
                    ) as bar:
                        for chunk in r.iter_bytes(chunk_size=65536):
                            f.write(chunk)
                            bar.update(len(chunk))
                print(f"  EPA FRS: downloaded ({dest.stat().st_size / 1_048_576:.1f} MB)")
            except httpx.HTTPStatusError as e:
                dest.unlink(missing_ok=True)
                print(f"  ERROR: EPA FRS download failed (HTTP {e.response.status_code}). "
                      f"Update epa.frs_zip_url in config.lifeline.yaml.")
                return
            except Exception as e:
                dest.unlink(missing_ok=True)
                print(f"  WARNING: Failed to download EPA FRS: {e}")
                return
        else:
            print("  EPA FRS: already downloaded, skipping download")

        # --- Extract CSV ---
        if not extract_sentinel.exists():
            try:
                extract_dir.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(dest) as z:
                    members = z.namelist()
                    print(f"  Extracting {len(members)} files → {extract_dir}")
                    z.extractall(extract_dir)
                extract_sentinel.touch()
                print("  EPA FRS extraction complete.")
            except zipfile.BadZipFile:
                dest.unlink(missing_ok=True)
                print("  ERROR: frs_national_single.zip is corrupt — deleted, re-run to re-download.")
                return
        else:
            print(f"  EPA FRS: already extracted → {extract_dir}")

        # --- Convert to GeoParquet ---
        if parquet_out.exists():
            print(f"  EPA FRS GeoParquet: already exists → {parquet_out}")
            return
        csv_files = sorted(extract_dir.glob("*.csv")) + sorted(extract_dir.glob("*.CSV"))
        if not csv_files:
            print("  WARNING: No CSV files found in extract dir, skipping GeoParquet conversion.")
            return
        csv_list = "[" + ", ".join(f"'{str(p).replace(chr(92), '/')}'" for p in csv_files) + "]"
        print(f"  Converting {len(csv_files)} CSV(s) to GeoParquet → {parquet_out}")
        conn = _duckdb.connect()
        try:
            conn.execute("INSTALL spatial; LOAD spatial;")
            conn.execute(f"""
                COPY (
                    SELECT
                        * EXCLUDE (LATITUDE83, LONGITUDE83),
                        CASE
                            WHEN LATITUDE83 IS NOT NULL AND LONGITUDE83 IS NOT NULL
                            THEN ST_Transform(
                                ST_Point(LONGITUDE83, LATITUDE83),
                                'EPSG:4269', 'EPSG:4326'
                            )
                            ELSE NULL
                        END AS geometry
                    FROM read_csv(
                        {csv_list},
                        header=True,
                        union_by_name=True,
                        dateformat='%d-%b-%y',
                        ignore_errors=True
                    )
                ) TO '{str(parquet_out).replace(chr(92), '/')}' (FORMAT PARQUET)
            """)
            row_count = conn.execute(
                f"SELECT COUNT(*) FROM '{str(parquet_out).replace(chr(92), '/')}'"
            ).fetchone()[0]
            print(f"  EPA FRS GeoParquet: {row_count:,} rows written ({parquet_out.stat().st_size / 1_048_576:.1f} MB)")
        finally:
            conn.close()

    if flow_params.run_epa:
        print("[EPA] Starting FRS download + extraction + GeoParquet conversion")
        _ingest_epa()
        epa_result = mo.callout(mo.md("✅ **EPA FRS → GeoParquet complete.**"), kind="success")
    else:
        epa_result = mo.callout(mo.md("⏭ EPA download skipped."), kind="neutral")
    epa_result
    return (epa_result,)


@app.cell
def _(eia_result, epa_result, mo, osm_result):
    mo.vstack([
        mo.md("## Ingestion Summary"),
        osm_result,
        eia_result,
        epa_result,
        mo.callout(mo.md("✅ **Flow 01 complete.** ➡ Run `flows/02_silver_conflation.py` next."), kind="success"),
    ])
    return


if __name__ == "__main__":
    app.run()
