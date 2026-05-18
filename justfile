# LifelinePOI Task Runner
# Install: https://github.com/casey/just

# Run the full pipeline: ingest -> process -> tiles
run-all: ingest process tiles

# Ingest all sources: OSM PBF + EIA + EPA -> Bronze
ingest:
    uv run flows/01_ingest.py

# Extract only OSM PBF layers -> Bronze (fast, no network required)
ingest-osm:
    uv run flows/01_ingest.py --osm-only

# Process Bronze -> Silver -> Gold (conflation + campus collapse + bridge + wide tables)
process:
    uv run flows/02_silver_conflation.py
    uv run flows/02b_campus_collapse.py
    uv run flows/03_gersite_bridge.py --skip
    uv run flows/04_gold_production.py

# Generate PMTiles from Gold layer (no tippecanoe required)
tiles:
    uv run flows/05_generate_tiles.py

# Open Marimo interactive QA dashboard
qa:
    uv run marimo flows/06_qa.py

# Start the map viewer dev server (requires: just setup-site first)
site-dev:
    cd site && npm run dev

# Build the map viewer for production
site-build:
    cd site && npm run build

# Install Python dependencies with uv
setup:
    uv sync

# Install site (JavaScript) dependencies
setup-site:
    cd site && npm install

# Install everything (Python + site)
setup-all: setup setup-site

# Run tests
test:
    pytest tests/ -v

# Lint Python code
lint:
    ruff check .
    ruff format --check .
