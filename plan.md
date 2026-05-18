This revised implementation plan pivots away from traditional BI naming conventions (`fct_`/`dim_`) and focuses on a Marimo-driven workflow that prioritizes a master conflated layer in Silver and wide, distribution-ready tables in Gold.

## 1. Updated Architecture Decisions

### Medallion Layer Definitions

| Layer | Naming Style | Logic |
| :--- | :--- | :--- |
| **Bronze** | `bronze/<source>/<dataset>` | Raw GeoParquet. Direct extracts from OSM (Overpass/Overture), EIA (Form 860/923), and EPA (FRS). |
| **Silver** | `silver/lifeline_points` | **The Master Table.** One row per unique POI. Contains core attributes: `lifeline_id`, `category`, `name`, `geometry`, `source_provenance`. |
| **Silver (Attr)** | `silver/attr_<category>` | **Domain Extensions.** Deep attributes (e.g., `voltage_kv`, `flow_mgd`) joined to Silver Master via UUID. |
| **Gold** | `gold/wide_<category>` | **The Wide Layer.** Flattened tables joining Silver Master + Attr + GERSite building metadata for final consumption. |

### Key Logic Shifts
* **Source Swap**: HIFLD is removed. Primary authoritative validation now comes from **EIA Form 860** (Power) and **EPA FRS** (Water/Environmental) to supplement or verify OSM.
* **Infrastructure over SQL**: Schema definitions are handled within Marimo flows using DuckDB/PyArrow types, rather than standalone `.sql` files.
* **The Bridge**: A specific flow is added to intersect `silver/lifeline_points` with GERSite building footprints to associate infrastructure points with physical structures.

---

## 2. Updated Directory Structure

```text
LifelinePOI/
├── pyproject.toml                # Managed by uv
├── config.lifeline.yaml          # S3/Local paths, AOIs, scoring weights
├── flows/                        # Marimo notebooks (Prefect tasks)
│   ├── 01_ingest.py              # Pull OSM, EIA, EPA -> Bronze
│   ├── 02_silver_conflation.py   # Conflate -> silver/lifeline_points + attr_*
│   ├── 03_gersite_bridge.py      # Spatial join: POI points <-> GERSite buildings
│   ├── 04_gold_production.py     # Create wide analytical tables
│   └── 05_generate_tiles.py      # Produce PMTiles for QA/QC
├── lib/                          # Ported from GERSite/OpenPOIs
│   ├── duckdb_utils.py           # DuckDB connection/config
│   ├── scoring.py                # CS = (dist * 0.4) + (attr * 0.4) + (src * 0.2)
│   └── spatial.py                # H3 indexing & AOI clipping
├── data/
│   └── seed/
│       └── fema_lifelines.csv    # Source for lifeline_id assignment
├── src/
│   └── lifelinepoi/              # Core logic for match/merge
└── justfile                      # Task runner (e.g., 'just run-all')
```

---

## 3. Implementation Roadmap

### Phase 1: Foundation & Ingestion
* Initialize environment using `uv` for high-speed dependency management.
* **Flow 01 (Ingest)**:
    * Fetch **EIA 860** (Electric Power) and **EPA FRS** (Wastewater/Potable Water).
    * Query **OSM Overpass** for `power`, `man_made=water_works`, `telecom=exchange`, etc.
    * Standardize all raw inputs to GeoParquet in Bronze.

### Phase 2: Silver Conflation (The "Master" Layer)
* **Flow 02 (Silver)**:
    * Assign a stable UUID to every point.
    * Map `fema_lifelines.csv` to POI categories to generate the `lifeline_id` (e.g., Energy -> Power Grid -> Distribution).
    * **Conflation Logic**: Use `rapidfuzz` for name matching and BallTree for spatial proximity.
    * Populate `silver/lifeline_points` (Core) and `silver/attr_*` (Extensions).

### Phase 3: GERSite Bridging & Gold Production
* **Flow 03 (Bridge)**:
    * Load building footprints from GERSite output.
    * Perform a spatial "Point-in-Polygon" or "Nearest Neighbor" join.
    * Store a bridge table: `silver/bridge_poi_building`.
* **Flow 04 (Gold)**:
    * Generate "Wide" tables (e.g., `gold/wide_substations`) by joining Silver core, attributes, and building metadata (address, building area).
    * Partition output by H3 or Administrative Boundary for performance.

### Phase 4: QA & Distribution
* **Flow 05 (Tiles)**:
    * Invoke `tippecanoe` via Python to generate PMTiles for the Gold layer.
    * Include the Confidence Score in the tile metadata for visual filtering.
* **Visual QA**: Use Marimo’s interactive UI to inspect "Low Confidence" clusters where OSM and EIA/EPA significantly diverge.

---

## 4. Key Source Integration

| Category | Authoritative Source | OSM Filter Keys |
| :--- | :--- | :--- |
| **Power** | EIA Form 860/923 | `power=substation`, `power=plant` |
| **Water** | EPA FRS (Water/WW) | `man_made=water_works`, `man_made=wastewater_plant` |
| **Telecom** | FCC (Mobile/Tower) | `telecom=cell_tower`, `man_made=antenna` |
| **Fuel** | EIA (Terminals/Storage) | `industrial=fuel`, `amenity=fuel` (bulk storage focus) |

---

## 5. Confidence Score Refinement
Since HIFLD is out, the **Source Score ($S_{source}$)** component should weight EIA/EPA higher for location accuracy, while weighting OSM higher for recent "last-seen" updates or local name tagging.

* **Verified High**: Point exists in both OSM and EIA/EPA within < 50m.
* **Verified Medium**: Point exists in EIA/EPA but missing from OSM (or vice versa).
* **Unverified**: OSM-only point with no corresponding entry in authoritative domain datasets.