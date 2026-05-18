"""
One-time script: convert hospital_acs_trauma.json → data/seed/acs_trauma_level.parquet

Usage
-----
    python scripts/build_acs_trauma_seed.py
    python scripts/build_acs_trauma_seed.py --json-path /path/to/hospital_acs_trauma.json
    python scripts/build_acs_trauma_seed.py --json-path /path/to/json --out-path data/seed/acs_trauma_level.parquet

The JSON is the ACS Hospital Finder export
(https://www.facs.org/quality-programs/accreditation-and-verification/).
It has a ``results`` array of parent institutions, each with a
``childInstitutions`` array.  Each child has a ``programs`` array; trauma
designations live in the program entry where ``alias == "Trauma"`` and
``type == "QualityProgram"``, with level strings in the nested ``levels``
list (e.g. "Level I Trauma Center", "Level II Pediatric Trauma Center").

Output schema
-------------
institution_id, institution_name, parent_id, parent_name,
program_name, program_alias, program_type, trauma_level,
website, address_line1, address_line2, address_line3,
city, state, zip_code, country,
latitude, longitude, last_updated, geometry (Point WGS84)
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

_DEFAULT_JSON = Path(r"C:\Source\geocube\data\input\seed\hospital_acs_trauma.json")
_DEFAULT_OUT = Path("data") / "seed" / "acs_trauma_level.parquet"


def _pick_primary_trauma_level(levels: list[str]) -> str | None:
    """Return the adult (non-pediatric) trauma level, falling back to pediatric."""
    if not levels:
        return None
    adult = [lv for lv in levels if "pediatric" not in lv.lower()]
    return adult[0] if adult else levels[0]


def _get_trauma_program(programs: list[dict]) -> dict | None:
    """Return the first Trauma-alias QualityProgram with non-empty levels."""
    for prog in programs:
        if (
            prog.get("alias") == "Trauma"
            and prog.get("type") == "QualityProgram"
            and prog.get("levels")
        ):
            return prog
    return None


def build_acs_trauma_seed(json_path: Path) -> gpd.GeoDataFrame:
    """
    Parse the ACS JSON and return a GeoDataFrame of all child institutions
    with their trauma level (where available) and Point geometry.

    Parameters
    ----------
    json_path:
        Path to ``hospital_acs_trauma.json``.

    Returns
    -------
    GeoDataFrame with one row per child institution, CRS = EPSG:4326.
    """
    json_path = Path(json_path)
    if not json_path.exists():
        raise FileNotFoundError(f"JSON not found: {json_path}")

    with open(json_path, encoding="utf-8") as fh:
        data = json.load(fh)

    results: list[dict] = data.get("results", [])
    rows: list[dict] = []

    for parent in results:
        parent_id = parent.get("id")
        parent_name = (parent.get("name") or "").strip()
        last_updated = parent.get("lastUpdated")

        for child in parent.get("childInstitutions", []):
            loc = child.get("locationPoint") or {}
            lat = loc.get("latitude")
            lon = loc.get("longitude")

            trauma_prog = _get_trauma_program(child.get("programs", []))
            if trauma_prog:
                trauma_level = _pick_primary_trauma_level(trauma_prog.get("levels", []))
                program_name = trauma_prog.get("name")
                program_alias = trauma_prog.get("alias")
                program_type = trauma_prog.get("type")
            else:
                trauma_level = None
                program_name = None
                program_alias = None
                program_type = None

            rows.append({
                "institution_id": child.get("id"),
                "institution_name": (child.get("name") or "").strip(),
                "parent_id": parent_id,
                "parent_name": parent_name,
                "program_name": program_name,
                "program_alias": program_alias,
                "program_type": program_type,
                "trauma_level": trauma_level,
                "website": (child.get("webSite") or "").strip() or None,
                "address_line1": (child.get("addressLine1") or "").strip() or None,
                "address_line2": (child.get("addressLine2") or "").strip() or None,
                "address_line3": (child.get("addressLine3") or "").strip() or None,
                "city": (child.get("city") or "").strip() or None,
                "state": (child.get("stateAbbreviation") or child.get("state") or "").strip() or None,
                "zip_code": (child.get("zipCode") or "").strip() or None,
                "country": (child.get("country") or "").strip() or None,
                "latitude": float(lat) if lat is not None else None,
                "longitude": float(lon) if lon is not None else None,
                "last_updated": pd.to_datetime(last_updated, utc=True, errors="coerce"),
            })

    df = pd.DataFrame(rows)

    # Build geometry from lat/lon; NaN coords produce null geometry
    geoms = [
        Point(row["longitude"], row["latitude"])
        if row["longitude"] is not None and row["latitude"] is not None
        else None
        for row in rows
    ]
    gdf = gpd.GeoDataFrame(df, geometry=geoms, crs="EPSG:4326")

    return gdf.reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert hospital_acs_trauma.json → acs_trauma_level.parquet (GeoParquet)"
    )
    parser.add_argument(
        "--json-path",
        type=Path,
        default=_DEFAULT_JSON,
        help=f"Path to hospital_acs_trauma.json (default: {_DEFAULT_JSON})",
    )
    parser.add_argument(
        "--out-path",
        type=Path,
        default=_DEFAULT_OUT,
        help=f"Output GeoParquet path (default: {_DEFAULT_OUT})",
    )
    args = parser.parse_args()

    print(f"[ACS seed] Reading JSON: {args.json_path}")
    gdf = build_acs_trauma_seed(args.json_path)

    args.out_path.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_parquet(args.out_path, index=False)

    # Summary stats
    total = len(gdf)
    trauma_rows = int(gdf["trauma_level"].notna().sum())
    valid_geom = int(gdf.geometry.notna().sum())

    print(f"\n[ACS seed] Written → {args.out_path}")
    print(f"  Total rows      : {total:,}")
    print(f"  With trauma_level: {trauma_rows:,}")
    print(f"  With geometry   : {valid_geom:,}")
    print(f"  Missing geometry: {total - valid_geom:,}")

    if trauma_rows > 0:
        print("\n  Trauma level breakdown:")
        for level, count in gdf["trauma_level"].value_counts().items():
            print(f"    {level}: {count}")

    print("\n  Sample rows (first 5 with trauma_level):")
    sample_cols = ["institution_id", "institution_name", "city", "state", "trauma_level", "latitude", "longitude"]
    sample = gdf[gdf["trauma_level"].notna()][sample_cols].head(5)
    print(sample.to_string(index=False))


if __name__ == "__main__":
    main()
