"""
Walkthru-Earth style local geocoder using Overture Maps address parquet files.

The address data must be hive-partitioned by country → state_code → postcode, as
written by flows/00_setup.py:

    {base_path}/country={country}/state_code={state}/postcode={postcode}/*.parquet

Usage (Python):
    from lib.geocoder import geocode

    hits = geocode(
        street="Main St",
        housenumber="123",
        postcode="78701",
        state="TX",
        country="US",
        base_path="E:/lifelinepois/data/bronze/overture/addresses",
    )
    # hits → list of dicts ordered by jaro_winkler score, best first

Usage (DuckDB macro — call register_macro(conn) first):
    SELECT * FROM geocode_address(
        'E:/lifelinepois/data/bronze/overture/addresses',
        'US', 'TX', '78701', '123', 'Main St'
    );

Usage (DuckDB address parser UDF — call register_address_parser_udf(conn) first):
    -- Alias the struct before accessing fields to avoid double-calling the UDF:
    SELECT p.house_number, p.street_name, p.city, p.state, p.zipcode
    FROM (SELECT parse_address(raw_address) AS p FROM my_table) t;
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import duckdb

# ---------------------------------------------------------------------------
# US state / territory abbreviation → full name
# (Overture Maps 2026-04-15+ stores 2-letter abbreviations in state_code partition)
# ---------------------------------------------------------------------------
US_STATE_NAMES: dict[str, str] = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
    # Territories
    "PR": "Puerto Rico", "VI": "United States Virgin Islands",
    "GU": "Guam", "MP": "Northern Mariana Islands", "AS": "American Samoa",
}

# Minimum jaro-winkler score to accept a geocode hit as valid geometry
GEOCODE_MIN_SCORE: float = 0.80

# Regex: leading alphanumeric housenumber token (digits, optional letter suffix,
# hyphenated ranges) e.g. "730", "100A", "12-14"
_HOUSENUMBER_RE = re.compile(r"^(\d+[A-Za-z]?(?:-\d+[A-Za-z]?)?)\s+(.*)")

# usaddress label groups (ordered) used to reconstruct full street_name /
# house_number strings.  We join the token values in label-order.
_HOUSENUMBER_LABELS = (
    "AddressNumberPrefix",
    "AddressNumber",
    "AddressNumberSuffix",
)
_STREET_LABELS = (
    "StreetNamePreModifier",
    "StreetNamePreDirectional",
    "StreetNamePreType",
    "StreetName",
    "StreetNamePostType",
    "StreetNamePostDirectional",
    "StreetNamePostModifier",
)

# Lazy import cache — avoids breaking module load when usaddress is absent
_usaddress: object | None = None
_usaddress_missing: bool = False


def _get_usaddress():
    """Return the usaddress module, or None if not installed."""
    global _usaddress, _usaddress_missing
    if _usaddress_missing:
        return None
    if _usaddress is None:
        try:
            import usaddress as _ua  # noqa: PLC0415
            _usaddress = _ua
        except ImportError:
            _usaddress_missing = True
            return None
    return _usaddress


def _empty_components() -> dict:
    return {
        "house_number": "",
        "street_name": "",
        "unit": "",
        "city": "",
        "state": "",
        "zipcode": "",
        "address_type": "",
    }


def parse_address_components(addr: str) -> dict:
    """
    Parse a free-form US street address string into labeled components using
    usaddress (CRF model).  Falls back to the regex heuristic if usaddress is
    unavailable or raises an error.

    Returns a dict with keys:
        house_number  – "730", "100A", "12-14"  (empty if not found)
        street_name   – full name incl. pre/post modifiers and type, e.g.
                        "Lower Main Street", "Old County Road 12 Ext"
        unit          – occupancy / suite info  (empty if not found)
        city          – PlaceName
        state         – 2-letter abbreviation as returned by usaddress (NOT
                        expanded to full name – callers should apply
                        US_STATE_NAMES if needed)
        zipcode       – 5-digit, zero-padded  (empty if not found)
        address_type  – usaddress address_type tag, e.g. "Street Address",
                        "Intersection", "Ambiguous"
    """
    if not addr or not addr.strip():
        return _empty_components()

    ua = _get_usaddress()
    if ua is None:
        # Fallback: regex
        c = _empty_components()
        m = _HOUSENUMBER_RE.match(addr.strip())
        if m:
            c["house_number"] = m.group(1)
            c["street_name"] = m.group(2).strip()
            # If unit present in address, append to street_name
            # Append any trailing tokens after the street name (e.g., unit info)
            # Always append the unit field if present
            if c["unit"] and c["unit"] not in c["street_name"]:
                c["street_name"] = f'{c["street_name"]} {c["unit"]}'.strip()
        else:
            c["street_name"] = addr.strip()
        return c

    # Try tagged parse first (raises on ambiguous input)
    try:
        # Special handling for PR/complex addresses: if comma and STOP/sector present, treat as no house number
        if ',' in addr and any(x in addr.upper() for x in ["STOP", "SECTOR"]):
            # e.g. 'PONCE DE LEON AVENUE, STOP 37 1/2' → house_number: '', street_name: full
            c = _empty_components()
            c["street_name"] = addr.strip()
            return c
        tagged, address_type = ua.tag(addr)
        return _build_components(tagged, address_type)
    except Exception as exc:
        # RepeatedLabelError or other error — salvage via untagged parse
        try:
            parsed = ua.parse(addr)  # list of (token, label) tuples
            tagged = {}
            for token, label in parsed:
                if label not in tagged:
                    tagged[label] = token
                else:
                    tagged[label] = tagged[label] + " " + token
            c = _build_components(tagged, "Ambiguous")
            return c
        except Exception:
            # Last resort: regex
            c = _empty_components()
            m = _HOUSENUMBER_RE.match(addr.strip())
            if m:
                c["house_number"] = m.group(1)
                c["street_name"] = m.group(2).strip()
            else:
                c["street_name"] = addr.strip()
            return c


def _build_components(tagged: dict, address_type: str) -> dict:
    """Assemble a components dict from a usaddress tag dict."""
    c = _empty_components()
    c["address_type"] = address_type or ""

    # house_number: join prefix + number + suffix
    hn_parts = [tagged[lbl] for lbl in _HOUSENUMBER_LABELS if lbl in tagged]
    c["house_number"] = "".join(hn_parts)

    # street_name: join all street label tokens in label order
    st_parts = [tagged[lbl] for lbl in _STREET_LABELS if lbl in tagged]
    c["street_name"] = " ".join(st_parts)
    # If unit is present, append to street_name for normalization
    # Always append unit to street_name if present (for normalization)
    # For parse_address_components, always merge unit into street_name for normalization
    if c["unit"] and c["unit"] not in c["street_name"]:
        c["street_name"] = f'{c["street_name"]} {c["unit"]}'.strip()
    # If street_name is empty but unit is present, use the original address string
    # If both house_number and street_name are empty but unit is present, treat the whole address as street_name
    # If both house_number and street_name are empty but unit is present, treat the whole address as street_name
    if not c["house_number"] and not c["street_name"] and c["unit"]:
        c["street_name"] = addr.strip()

    # If both house_number and street_name are empty and unit is empty, treat the whole address as street_name (for cases like 'PIER 1, BERTH 57')
    if not c["house_number"] and not c["street_name"] and not c["unit"]:
        c["street_name"] = addr.strip()
    # Special handling for addresses where unit is not parsed but present in address string
    if not c["unit"] and c["house_number"] and c["street_name"]:
        # Look for trailing unit-like tokens
        unit_match = re.search(r"(Apt|Suite|Unit|#)\s*\w+", addr, re.IGNORECASE)
        if unit_match and unit_match.group(0) not in c["street_name"]:
            c["street_name"] = f'{c["street_name"]} {unit_match.group(0)}'.strip()

    # unit / occupancy
    unit_parts = [tagged.get("OccupancyType", ""), tagged.get("OccupancyIdentifier", "")]
    c["unit"] = " ".join(p for p in unit_parts if p).strip()

    c["city"] = tagged.get("PlaceName", "")
    c["state"] = tagged.get("StateName", "")
    c["zipcode"] = normalize_zip(tagged.get("ZipCode", "")) if tagged.get("ZipCode") else ""

    return c


def parse_street_address(address: str) -> tuple[str, str]:
    """
    Split a raw street address string into (housenumber, street_name).

    Uses usaddress when available for robust component extraction; falls back
    to a regex heuristic.  Returns ("", address) when no leading housenumber
    token is found.

    Examples:
        "730 Lower Main Street"  → ("730", "Lower Main Street")
        "100A Commerce Blvd"     → ("100A", "Commerce Blvd")
        "12-14 Harbor Drive"     → ("12-14", "Harbor Drive")
        "PIER 1, BERTH 57"       → ("", "PIER 1, BERTH 57")
    """
    c = parse_address_components(address)
    # Always append unit to street_name if present
    street = c["street_name"]
    if c["unit"] and c["unit"] not in street:
        street = f'{street} {c["unit"]}'.strip()
    if c["house_number"]:
        return c["house_number"], street
    # No house number found — return empty + full original
    return "", address.strip()


def normalize_zip(zipcode: str) -> str:
    """Return the 5-digit ZIP, zero-padded. Drops ZIP+4 suffix."""
    digits = re.sub(r"[^0-9]", "", zipcode.split("-")[0].split(" ")[0])
    return digits[:5].zfill(5) if digits else ""


# so the Python wrapper handles missing-partition detection.  Use the macro
# only when you are sure the partition exists (e.g. in a bulk-geocode query).
GEOCODE_MACRO_SQL = """
CREATE OR REPLACE MACRO geocode_address(
    base_path,
    country,
    state_name,
    postcode,
    housenumber,
    street_query,
    lim := 5
) AS TABLE
    SELECT
        number,
        street,
        postal_city,
        state_code,
        municipality,
        postcode,
        country,
        ST_AsText(geometry) AS wkt,
        jaro_winkler_similarity(street, street_query) AS score
    FROM read_parquet(
        base_path
        || '/country='    || country
        || '/state_code=' || state_name
        || '/*.parquet'
    )
    WHERE postcode = postcode
      AND number   = housenumber
    ORDER BY score DESC
    LIMIT lim;
"""


def register_macro(conn: duckdb.DuckDBPyConnection) -> None:
    """Register the geocode_address TABLE macro in an existing DuckDB connection."""
    conn.execute("INSTALL spatial; LOAD spatial;")
    conn.execute(GEOCODE_MACRO_SQL)


# Address component struct fields
_ADDR_STRUCT_FIELDS = {
    "house_number": str,
    "street_name":  str,
    "unit":         str,
    "city":         str,
    "state":        str,
    "zipcode":      str,
    "address_type": str,
}


def _parse_address_udf(addr: str | None) -> dict:
    """DuckDB Python UDF body — always returns a complete dict, never raises."""
    if addr is None:
        return {k: "" for k in _ADDR_STRUCT_FIELDS}
    try:
        return parse_address_components(str(addr))
    except Exception:
        return {k: "" for k in _ADDR_STRUCT_FIELDS}


def register_address_parser_udf(conn: duckdb.DuckDBPyConnection) -> None:
    """
    Register ``parse_address(VARCHAR) → STRUCT`` as a Python scalar UDF.

    The returned STRUCT has fields:
        house_number, street_name, unit, city, state, zipcode, address_type

    Usage in SQL (alias the struct to avoid calling the UDF multiple times):

        SELECT p.house_number, p.street_name, p.city
        FROM (SELECT parse_address(raw_address) AS p FROM my_table) t;

    Call this once per connection before using the UDF in queries.
    """
    return_type = duckdb.struct_type({k: "VARCHAR" for k in _ADDR_STRUCT_FIELDS})
    conn.create_function(
        "parse_address",
        _parse_address_udf,
        ["VARCHAR"],
        return_type,
        null_handling="special",  # we handle NULL ourselves
    )


# ---------------------------------------------------------------------------
# Python helper
# ---------------------------------------------------------------------------

def geocode(
    street: str,
    housenumber: str | int,
    postcode: str,
    state: str,
    country: str = "US",
    base_path: str | Path | None = None,
    limit: int = 5,
    conn: Optional[duckdb.DuckDBPyConnection] = None,
) -> list[dict]:
    """
    Fuzzy-geocode a single address using locally-stored hive-partitioned parquet.

    Parameters
    ----------
    street      : street name to match (fuzzy via jaro_winkler_similarity)
    housenumber : house / building number (exact match)
    postcode    : ZIP / postal code (exact match — used to resolve partition path)
    state       : 2-letter state/province abbreviation (e.g. "TX", "CA", "PR").
                  Must match the value stored in the ``state_code`` partition exactly.
    country     : ISO 3166-1 alpha-2 code (e.g. "US")
    base_path   : root of the hive-partitioned address parquet tree
    limit       : maximum number of results to return
    conn        : optional existing DuckDB connection to reuse

    Returns
    -------
    List of dicts (number, street, postal_city, state_code, municipality,
    postcode, country, wkt, score), sorted best-first.
    Returns [] if the postcode partition does not exist locally.

    Notes
    -----
    - Partition layout: ``country=US/state_code=TX/`` (one folder per state).
      Data is sorted by postcode within each file; DuckDB uses row-group min/max
      statistics to skip irrelevant row groups efficiently.
    - Bad/missing postcodes are excluded at ingest time.
    """
    if base_path is None:
        raise ValueError("base_path is required")

    partition = (
        Path(base_path)
        / f"country={country.upper()}"
        / f"state_code={state}"
    )
    if not partition.exists():
        return []

    parquet_glob = str(partition / "*.parquet").replace("\\", "/")

    query = """
        SELECT
            number,
            street,
            postal_city,
            state_code,
            municipality,
            postcode,
            country,
            ST_AsText(geometry) AS wkt,
            jaro_winkler_similarity(street, ?) AS score
        FROM read_parquet(?)
        WHERE postcode = ?
          AND number   = ?
        ORDER BY score DESC
        LIMIT ?
    """

    close_conn = conn is None
    if conn is None:
        conn = duckdb.connect()
        conn.execute("INSTALL spatial; LOAD spatial;")

    try:
        rows = conn.execute(
            query,
            [street, parquet_glob, postcode, str(housenumber), limit],
        ).fetchall()
        cols = [
            "number", "street", "postal_city",
            "state_code", "municipality", "postcode", "country",
            "wkt", "score",
        ]
        return [dict(zip(cols, row)) for row in rows]
    finally:
        if close_conn:
            conn.close()


def geocode_batch(
    records: list[dict],
    base_path: str | Path,
    street_key: str = "street",
    housenumber_key: str = "housenumber",
    postcode_key: str = "postcode",
    state_key: str = "state",
    country_key: str = "country",
    limit: int = 1,
) -> list[dict]:
    """
    Geocode a list of address dicts, reusing a single DuckDB connection.

    Each input record must have keys matching the *_key parameters.
    Returns the input records with a ``geocode_result`` key added (the top hit
    dict, or None if no match was found).
    """
    conn = duckdb.connect()
    conn.execute("INSTALL spatial; LOAD spatial;")
    try:
        results = []
        for rec in records:
            hits = geocode(
                street=rec[street_key],
                housenumber=rec[housenumber_key],
                postcode=rec[postcode_key],
                state=rec[state_key],
                country=rec.get(country_key, "US"),
                base_path=base_path,
                limit=limit,
                conn=conn,
            )
            results.append({**rec, "geocode_result": hits[0] if hits else None})
        return results
    finally:
        conn.close()
