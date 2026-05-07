"""DuckDB connection utilities for LifelinePOI."""
from __future__ import annotations

import duckdb
from pathlib import Path


def _split_sql(sql: str) -> list[str]:
    """Split a SQL string into individual statements on ';', ignoring semicolons
    inside single-quoted string literals (e.g. str_split(s, ';'))."""
    statements: list[str] = []
    current: list[str] = []
    in_string = False
    i = 0
    while i < len(sql):
        ch = sql[i]
        if ch == "'" and not in_string:
            in_string = True
            current.append(ch)
        elif ch == "'" and in_string:
            # Handle escaped single-quote ('')
            if i + 1 < len(sql) and sql[i + 1] == "'":
                current.append("''")
                i += 2
                continue
            in_string = False
            current.append(ch)
        elif ch == ";" and not in_string:
            stmt = "".join(current).strip()
            if stmt:
                statements.append(stmt)
            current = []
        else:
            current.append(ch)
        i += 1
    # trailing statement without a final semicolon
    stmt = "".join(current).strip()
    if stmt:
        statements.append(stmt)
    return statements


def get_connection(memory_limit: str = "16GB") -> duckdb.DuckDBPyConnection:
    """Return a DuckDB connection with osmium and spatial extensions loaded."""
    conn = duckdb.connect()
    conn.execute(f"SET memory_limit = '{memory_limit}'")
    conn.execute("INSTALL osmium from community")
    conn.execute("LOAD osmium")
    conn.execute("INSTALL spatial")
    conn.execute("LOAD spatial")
    return conn


def run_layer_sql(
    conn: duckdb.DuckDBPyConnection,
    sql_dir: Path,
    layer: str,
    pbf_path: str,
    output_path: str,
    osmium_index_type: str = "flex_mem",
) -> None:
    """
    Execute a Layercake-style SQL layer file against a PBF input.

    Concatenates macros.sql + <layer>.sql, substitutes {{INPUT}} and {{OUTPUT}}
    placeholders, and executes via the provided DuckDB connection.
    """
    macros_sql = (sql_dir / "macros.sql").read_text(encoding="utf-8")
    layer_sql = (sql_dir / f"{layer}.sql").read_text(encoding="utf-8")

    combined = macros_sql + "\n" + layer_sql
    combined = combined.replace("{{INPUT}}", pbf_path.replace("\\", "/"))
    combined = combined.replace("{{OUTPUT}}", str(output_path).replace("\\", "/"))

    conn.execute(f"SET osmium_index_type = '{osmium_index_type}'")

    # Execute each statement separately (DuckDB doesn't support multi-statement execute).
    # Use a character-level parser to split on ';' outside of string literals.
    for stmt in _split_sql(combined):
        conn.execute(stmt)
