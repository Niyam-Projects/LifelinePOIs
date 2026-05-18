# LifelinePOI

A data pipeline for conflating FEMA Lifeline infrastructure POIs from OpenStreetMap (PBF), EIA Form 860/923, and EPA FRS into cloud-native GeoParquet and PMTiles.

## Quick Start

```bash
# Install dependencies
uv sync

# Edit config to set your PBF path
nano config.lifeline.yaml  # set osm.pbf_path

# Run the full pipeline
just run-all
```

## OSM PBF Source

Download regional PBF extracts from:
- [Geofabrik](https://download.geofabrik.de/)
- [BBBike](https://extract.bbbike.org/)

Set the path in `config.lifeline.yaml`:
```yaml
osm:
  pbf_path: "/data/osm/your-region.osm.pbf"
```

## Architecture

See `plan.md` for the full architecture description.

| Layer | Path | Description |
|-------|------|-------------|
| Bronze | `data/bronze/` | Raw extracts from OSM PBF, EIA, EPA |
| Silver | `data/silver/` | Conflated master POI table + domain attrs |
| Gold | `data/gold/` | Wide analytical tables for distribution |
| Tiles | `data/tiles/` | PMTiles for visual QA and distribution |

## Layercake Compatibility

The `sql/` directory contains DuckDB SQL files for extracting infrastructure layers from OSM PBF. These follow the [osmus/layercake](https://github.com/osmus/layercake) convention and can be contributed back to that project.
