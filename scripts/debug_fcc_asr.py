"""
Debug / standalone script for FCC ASR cell tower download and GeoParquet conversion.

Run with:
    uv run python scripts/debug_fcc_asr.py

Findings from schema investigation (r_tower.zip):
  - CO.dat  : coordinates in DMS + total-arc-seconds (field[10]=lat_arcsec, field[15]=lon_arcsec)
  - EN.dat  : entity/owner address (street, city, state, zip) — join key = uls_file_num (field[2])
  - RA.dat  : registration details — no coords, no address

Coordinate formula:
  lat_decimal = arc_seconds_lat / 3600.0 * (-1 if S else 1)
  lon_decimal = arc_seconds_lon / 3600.0 * (-1 if W else 1)

Geocoding fallback:
  Records without coordinates are geocoded using lib/geocoder.py against the
  Overture hive-partitioned parquet (requires flows/00_setup.py to have run first).
"""

import sys
import zipfile
import time
from pathlib import Path

# Allow running from repo root or scripts/ subfolder
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import httpx
import duckdb
from lifelinepoi.config import LifelineConfig
from lib.geocoder import geocode

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
CFG_PATH = ROOT / "config.lifeline.yaml"
cfg = LifelineConfig.from_yaml(str(CFG_PATH))

BRONZE_FCC   = Path(cfg.storage.bronze_path) / "fcc"
ZIP_PATH     = BRONZE_FCC / "r_tower.zip"
EXTRACT_DIR  = BRONZE_FCC / "asr"
PARQUET_OUT  = BRONZE_FCC / "asr_towers.geoparquet"

# Correct URL (the old /download/asr/asr_full.zip returns 404)
FCC_ASR_URL = "https://data.fcc.gov/download/pub/uls/complete/r_tower.zip"

BRONZE_FCC.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Step 1 — Download
# ---------------------------------------------------------------------------
def download():
    if ZIP_PATH.exists():
        print(f"[1] ZIP already exists ({ZIP_PATH.stat().st_size / 1_048_576:.1f} MB) — skipping download")
        return
    print(f"[1] Downloading {FCC_ASR_URL} ...")
    t0 = time.perf_counter()
    with httpx.stream("GET", FCC_ASR_URL, follow_redirects=True, timeout=600) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        with open(ZIP_PATH, "wb") as f:
            done = 0
            for chunk in r.iter_bytes(65536):
                f.write(chunk)
                done += len(chunk)
                if total:
                    pct = done / total * 100
                    print(f"\r    {pct:5.1f}%  {done/1_048_576:.1f}/{total/1_048_576:.1f} MB", end="")
    print()
    elapsed = time.perf_counter() - t0
    print(f"    Done in {elapsed:.1f}s — {ZIP_PATH.stat().st_size / 1_048_576:.1f} MB")


# ---------------------------------------------------------------------------
# Step 2 — Extract
# ---------------------------------------------------------------------------
def extract():
    sentinel = EXTRACT_DIR / ".extracted"
    if sentinel.exists():
        print(f"[2] Already extracted -> {EXTRACT_DIR}")
        return
    EXTRACT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[2] Extracting {ZIP_PATH.name} -> {EXTRACT_DIR} ...")
    with zipfile.ZipFile(ZIP_PATH) as z:
        names = z.namelist()
        print(f"    Files in ZIP: {names}")
        z.extractall(EXTRACT_DIR)
    sentinel.touch()
    print("    Extraction complete.")


# ---------------------------------------------------------------------------
# Step 3 — Inspect schema
# ---------------------------------------------------------------------------
def inspect_schema():
    """Print first 3 rows of each .dat file so we can verify field positions."""
    print("[3] Schema inspection:")
    for dat in sorted(EXTRACT_DIR.glob("*.dat")):
        with open(dat, encoding="utf-8", errors="replace") as f:
            lines = [f.readline().rstrip() for _ in range(3)]
        print(f"\n  --- {dat.name} (first 3 rows) ---")
        for ln in lines:
            fields = ln.split("|")
            print(f"    [{len(fields)} fields] {ln[:120]}")


# ---------------------------------------------------------------------------
# Step 4 — Build GeoParquet via DuckDB
# ---------------------------------------------------------------------------
def build_geoparquet():
    if PARQUET_OUT.exists():
        print(f"[4] GeoParquet already exists -> {PARQUET_OUT}")
        p = str(PARQUET_OUT).replace("\\", "/")
        conn = duckdb.connect()
        total = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{p}')").fetchone()[0]
        with_geom = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{p}') WHERE geometry IS NOT NULL").fetchone()[0]
        conn.close()
        print(f"    {total:,} total  |  {with_geom:,} with geometry  |  {total-with_geom:,} without")
        return total - with_geom

    co_path  = str(EXTRACT_DIR / "CO.dat").replace("\\", "/")
    en_path  = str(EXTRACT_DIR / "EN.dat").replace("\\", "/")
    out_path = str(PARQUET_OUT).replace("\\", "/")

    print("[4] Building GeoParquet from CO.dat + EN.dat ...")

    conn = duckdb.connect()
    conn.execute("INSTALL spatial; LOAD spatial;")

    # Confirm CO.dat field positions with a real coord row
    with open(EXTRACT_DIR / "CO.dat", encoding="utf-8", errors="replace") as f:
        for line in f:
            fields = line.strip().split("|")
            if len(fields) >= 16 and fields[10] not in ("", "0.0", "0"):
                lat = float(fields[10]) / 3600.0 * (-1 if fields[9] == "S" else 1)
                lon = float(fields[15]) / 3600.0 * (-1 if fields[14] == "W" else 1)
                print(f"    CO.dat sample: uls={fields[2]!r}  lat_arcsec={fields[10]}  lon_arcsec={fields[15]}")
                print(f"    -> decimal lat={lat:.6f}  lon={lon:.6f}")
                break

    # Peek EN.dat for address field positions
    with open(EXTRACT_DIR / "EN.dat", encoding="utf-8", errors="replace") as f:
        for line in f:
            fields = line.strip().split("|")
            if len(fields) >= 23 and fields[17].strip():
                print(f"    EN.dat sample: entity={fields[9]!r}  addr={fields[17]!r}  "
                      f"city={fields[20]!r}  state={fields[21]!r}  zip={fields[22]!r}")
                break

    # -----------------------------------------------------------------------
    # CO.dat field layout (no header, pipe-delimited):
    #   0  record_type, 1  data_type, 2  uls_file_num (join key), 3  ebf_num,
    #   4  call_sign,   5  coord_type,
    #   6  lat_deg,  7  lat_min,  8  lat_sec,  9  lat_dir (N/S),
    #  10  lat_arcsec   (= deg*3600 + min*60 + sec)
    #  11  lon_deg, 12  lon_min, 13  lon_sec, 14  lon_dir (E/W),
    #  15  lon_arcsec
    #
    # EN.dat field layout (no header, pipe-delimited):
    #   0  record_type, 1  data_type, 2  uls_file_num (join key), 3  ebf_num,
    #   4  call_sign,   5  entity_type,
    #   6-7  internal IDs,  8  entity_name,
    #   9-15 phone/fax/email/...,
    #  16  street_address, 17-18  addr2/pobox, 19  city, 20  state, 21  zip
    # -----------------------------------------------------------------------

    # Build column spec helpers
    def col_spec(n):
        return ", ".join(f"'column{i:02d}': 'VARCHAR'" for i in range(n))

    query = f"""
        COPY (
            WITH co AS (
                SELECT
                    column02  AS uls_file_num,
                    column04  AS call_sign,
                    column09  AS lat_dir,
                    column10  AS lat_arcsec,
                    column14  AS lon_dir,
                    column15  AS lon_arcsec
                FROM read_csv(
                    '{co_path}', delim='|', header=false,
                    all_varchar=true, ignore_errors=true,
                    columns={{{col_spec(18)}}}
                )
                WHERE column00 = 'CO'
            ),
            en AS (
                SELECT
                    column02  AS uls_file_num,
                    column09  AS entity_name,
                    column17  AS street_address,
                    column20  AS city,
                    column21  AS state,
                    column22  AS zip
                FROM read_csv(
                    '{en_path}', delim='|', header=false,
                    all_varchar=true, ignore_errors=true, null_padding=true,
                    columns={{{col_spec(25)}}}
                )
                WHERE column00 = 'EN'
            ),
            joined AS (
                SELECT
                    co.uls_file_num,
                    co.call_sign,
                    en.entity_name,
                    en.street_address,
                    en.city,
                    en.state,
                    en.zip,
                    CASE
                        WHEN TRY_CAST(co.lat_arcsec AS DOUBLE) > 0
                        THEN TRY_CAST(co.lat_arcsec AS DOUBLE) / 3600.0
                             * CASE WHEN co.lat_dir = 'S' THEN -1.0 ELSE 1.0 END
                    END AS latitude,
                    CASE
                        WHEN TRY_CAST(co.lon_arcsec AS DOUBLE) > 0
                        THEN TRY_CAST(co.lon_arcsec AS DOUBLE) / 3600.0
                             * CASE WHEN co.lon_dir = 'W' THEN -1.0 ELSE 1.0 END
                    END AS longitude
                FROM co
                LEFT JOIN en USING (uls_file_num)
            )
            SELECT
                uls_file_num,
                call_sign,
                entity_name,
                street_address,
                city,
                state,
                zip,
                latitude,
                longitude,
                CASE
                    WHEN latitude IS NOT NULL AND longitude IS NOT NULL
                    THEN ST_Point(longitude, latitude)
                END AS geometry
            FROM joined
        ) TO '{out_path}' (FORMAT PARQUET, COMPRESSION 'ZSTD')
    """

    print("    Running DuckDB JOIN + COPY ...")
    t0 = time.perf_counter()
    conn.execute(query)
    elapsed = time.perf_counter() - t0

    total = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{out_path}')").fetchone()[0]
    with_geom = conn.execute(
        f"SELECT COUNT(*) FROM read_parquet('{out_path}') WHERE geometry IS NOT NULL"
    ).fetchone()[0]
    no_geom = total - with_geom
    mb = PARQUET_OUT.stat().st_size / 1_048_576

    print(f"    Done in {elapsed:.1f}s")
    print(f"    Total records : {total:,}")
    print(f"    With geometry : {with_geom:,}  ({with_geom/total*100:.1f}%)")
    print(f"    No geometry   : {no_geom:,}  -- candidates for geocoding")
    print(f"    Output        : {PARQUET_OUT}  ({mb:.1f} MB)")
    conn.close()
    return no_geom


# ---------------------------------------------------------------------------
# Step 5 — Geocode records missing coordinates
# ---------------------------------------------------------------------------

def geocode_missing(overture_base: str | None = None):
    """
    Geocode FCC ASR records that have address info but no lat/lon.

    Reads the GeoParquet written in step 4, geocodes the no-geometry rows,
    writes updated records to a separate geocoded parquet, then merges into
    the final output.

    Requires:
        overture_base — path to the hive-partitioned Overture addresses dir.
                        Defaults to {bronze}/overture/addresses.
    """
    if overture_base is None:
        overture_base = str(Path(cfg.storage.bronze_path) / "overture" / "addresses")

    overture_path = Path(overture_base)
    if not overture_path.exists():
        print(f"\n[5] Overture base not found at {overture_base}")
        print("    Run flows/00_setup.py first to download the hive-partitioned address data.")
        print("    Skipping geocoding step.")
        return

    from lib.geocoder import geocode

    out_path = str(PARQUET_OUT).replace("\\", "/")
    geocoded_out = PARQUET_OUT.parent / "asr_towers_geocoded.parquet"

    conn = duckdb.connect()
    conn.execute("INSTALL spatial; LOAD spatial;")

    # Pull records without geometry that have enough address data to geocode
    rows = conn.execute(f"""
        SELECT uls_file_num, call_sign, entity_name,
               street_address, city, state, zip
        FROM read_parquet('{out_path}')
        WHERE geometry IS NULL
          AND zip IS NOT NULL AND trim(zip) != ''
          AND street_address IS NOT NULL AND trim(street_address) != ''
          AND state IS NOT NULL AND trim(state) != ''
        LIMIT 1000
    """).fetchall()
    conn.close()

    if not rows:
        print("\n[5] No geocodable records — all have geometry or missing address fields.")
        return

    keys = ["uls_file_num", "call_sign", "entity_name", "street_address", "city", "state", "zip"]
    records = [dict(zip(keys, row)) for row in rows]
    print(f"\n[5] Geocoding {len(records):,} records missing geometry ...")
    print(f"    Overture base: {overture_base}")

    # Geocode each record; map 2-letter state to full name for Overture partition lookup
    geocoded = []
    hits = 0
    geo_conn = duckdb.connect()
    geo_conn.execute("INSTALL spatial; LOAD spatial;")

    for rec in records:
        abbrev = rec["state"].strip().upper()
        # Parse housenumber vs street using usaddress-backed parse_street_address
        parts = rec["street_address"].strip().split(None, 1)
        if len(parts) == 2 and parts[0].rstrip(".").isdigit():
            housenumber, street = parts[0], parts[1]
        else:
            housenumber, street = "", rec["street_address"]

        result = None
        if housenumber and rec["zip"].strip():
            hits_list = geocode(
                street=street,
                housenumber=housenumber,
                postcode=rec["zip"].strip().split("-")[0],  # drop ZIP+4 suffix
                state=abbrev,  # Overture 2026-04-15+: partition uses 2-letter abbrev
                country="US",
                base_path=overture_base,
                limit=1,
                conn=geo_conn,
            )
            if hits_list:
                result = hits_list[0]
                hits += 1

        geocoded.append({**rec, "geocode_result": result})

    geo_conn.close()

    # Report sample results
    matched = [g for g in geocoded if g["geocode_result"]]
    print(f"    Geocode hits  : {hits:,} / {len(records):,} ({hits/len(records)*100:.1f}%)")
    if matched:
        print("    Sample geocoded results:")
        for g in matched[:3]:
            r = g["geocode_result"]
            print(f"      {g['uls_file_num']}  score={r['score']:.3f}  "
                  f"wkt={r['wkt'][:40]}  matched={r['street']}")
    else:
        print("    No matches found. Is the Overture data downloaded for the right states?")

    # Save geocoded subset to a separate parquet for inspection
    import json
    rows_out = []
    for g in geocoded:
        r = g["geocode_result"]
        rows_out.append((
            g["uls_file_num"], g["call_sign"], g["entity_name"],
            g["street_address"], g["city"], g["state"], g["zip"],
            r["wkt"] if r else None,
            r["score"] if r else None,
        ))

    gconn = duckdb.connect()
    gconn.execute("INSTALL spatial; LOAD spatial;")
    gconn.execute("""
        CREATE TABLE geocoded (
            uls_file_num VARCHAR, call_sign VARCHAR, entity_name VARCHAR,
            street_address VARCHAR, city VARCHAR, state VARCHAR, zip VARCHAR,
            geocoded_wkt VARCHAR, geocode_score DOUBLE
        )
    """)
    gconn.executemany("INSERT INTO geocoded VALUES (?,?,?,?,?,?,?,?,?)", rows_out)
    gcout = str(geocoded_out).replace("\\", "/")
    gconn.execute(f"COPY geocoded TO '{gcout}' (FORMAT PARQUET, COMPRESSION 'ZSTD')")
    gconn.close()

    print(f"    Geocoded results saved to: {geocoded_out}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=" * 64)
    print("FCC ASR Debug Script")
    print(f"  ZIP      : {ZIP_PATH}")
    print(f"  Extract  : {EXTRACT_DIR}")
    print(f"  Output   : {PARQUET_OUT}")
    print(f"  URL      : {FCC_ASR_URL}")
    print("=" * 64)

    download()
    extract()
    inspect_schema()
    no_geom = build_geoparquet()
    if no_geom:
        geocode_missing()

    print("\nDone.")
