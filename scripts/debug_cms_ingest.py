"""
Debug / validation script for the CMS hospital provider bronze parquet.

Usage
-----
    python scripts/debug_cms_ingest.py
    python scripts/debug_cms_ingest.py --bronze-path E:/lifelinepois/data/bronze

Reads ``bronze/cms/cms_hospital_providers.parquet`` and prints:
- Row count and column list with dtypes
- Null / missing value counts for key columns
- Breakdown of PRVDR_CTGRY_CD values
- Sample rows (first 10 hospital category "01" records)
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


_KEY_COLS = ["PRVDR_CTGRY_CD", "BED_CNT", "FAC_NAME", "STATE_CD", "ZIP_CD"]


def debug_cms_parquet(bronze_path: Path) -> None:
    parquet_file = Path(bronze_path) / "cms" / "cms_hospital_providers.parquet"

    if not parquet_file.exists():
        print(f"[CMS debug] File not found: {parquet_file}")
        print("  Run flow 01_ingest.py with run_cms=True to download first.")
        return

    df = pd.read_parquet(parquet_file)

    # Detect the known bad-ingest format: single column named "json" containing
    # list-of-tuples.  This happens when the CMS API's [key,val] pair format
    # was not normalized before writing.  Guide the user to re-download.
    if list(df.columns) == ["json"]:
        print(f"[CMS debug] *** MALFORMED PARQUET DETECTED ***")
        print(f"  File    : {parquet_file}")
        print(f"  Rows    : {len(df):,}")
        print(f"  Columns : {df.columns.tolist()}")
        print()
        print("  The parquet has a single 'json' column containing list-of-tuple rows.")
        print("  This means the CMS API's [key, val] array format was not converted to")
        print("  proper named columns during ingest.")
        print()
        print("  Fix: delete the file and re-run Flow 01 with run_cms=True:")
        print(f"    del \"{parquet_file}\"")
        print("  (lib/cms_ingest.py has been updated to normalize the format correctly.)")
        return

    print(f"[CMS debug] {parquet_file}")
    print(f"  Rows : {len(df):,}")
    print(f"  Cols : {len(df.columns)}")

    print("\n  Column dtypes:")
    for col, dtype in df.dtypes.items():
        print(f"    {col:<40} {dtype}")

    present_key_cols = [c for c in _KEY_COLS if c in df.columns]
    if present_key_cols:
        print("\n  Null counts (key columns):")
        for col in present_key_cols:
            nulls = int(df[col].isna().sum())
            pct = nulls / len(df) * 100 if len(df) else 0
            print(f"    {col:<40} {nulls:>8,}  ({pct:.1f}%)")

    if "PRVDR_CTGRY_CD" in df.columns:
        print("\n  PRVDR_CTGRY_CD value counts (top 15):")
        for val, count in df["PRVDR_CTGRY_CD"].value_counts().head(15).items():
            marker = " ← hospitals" if str(val) == "01" else ""
            print(f"    {str(val):<10} {count:>8,}{marker}")

        hospitals = df[df["PRVDR_CTGRY_CD"].astype(str) == "01"]
        print(f"\n  Hospital (PRVDR_CTGRY_CD='01') records: {len(hospitals):,}")

        if len(hospitals) > 0:
            sample_cols = [c for c in ["FAC_NAME", "STATE_CD", "ZIP_CD", "BED_CNT"] if c in hospitals.columns]
            print(f"\n  Sample hospital rows (first 10):")
            print(hospitals[sample_cols].head(10).to_string(index=False))
    else:
        print("\n  WARNING: PRVDR_CTGRY_CD column not found — column names may differ.")
        print("  Showing first 5 rows of all columns:")
        print(df.head(5).to_string())


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate the CMS hospital provider bronze parquet"
    )
    parser.add_argument(
        "--bronze-path",
        type=Path,
        default=None,
        help="Bronze data directory (default: read from config.lifeline.yaml)",
    )
    args = parser.parse_args()

    bronze_path = args.bronze_path
    if bronze_path is None:
        try:
            import sys
            sys.path.insert(0, str(Path(".").resolve()))
            from src.lifelinepoi.config import LifelineConfig
            cfg = LifelineConfig.from_yaml("config.lifeline.yaml")
            bronze_path = Path(cfg.storage.bronze_path)
        except Exception as exc:
            print(f"[CMS debug] Could not load config: {exc}")
            print("  Pass --bronze-path explicitly, e.g.:")
            print("    python scripts/debug_cms_ingest.py --bronze-path E:/lifelinepois/data/bronze")
            return

    debug_cms_parquet(bronze_path)


if __name__ == "__main__":
    main()
