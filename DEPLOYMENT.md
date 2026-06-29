# Deployment and Execution Guide

This project is designed to run in Docker/Podman containers with transparent data access via volume mounts.

## Local Execution (macOS Silicon with Podman)

To run the pipeline locally, mount your local PBF input directory and a data storage directory into the container.

### 1. Build the Image
```bash
podman build -t lifelinepoi .
```

### 2. Run the Ingestion Flow
Replace `/Users/yourname/lifelinepois/input` and `/Users/yourname/lifelinepois/data` with your actual local paths.

```bash
podman run -it --rm \
  -v /Users/yourname/lifelinepois/input:/app/input:Z \
  -v /Users/yourname/lifelinepois/data:/app/data:Z \
  -e LIFELINE_PBF_DIR=/app/input \
  -e LIFELINE_STORAGE_ROOT=/app/data \
  lifelinepoi marimo run flows/01_ingest.py -- --run-osm true
```

**Note:** The `:Z` flag is used for SELinux labeling (required on some Podman setups).

## AWS Deployment (EC2 / Fargate)

For production runs on AWS, we use **Mountpoint for Amazon S3** to provide high-performance, transparent access to PBF files and GeoParquet data stored in S3.

### 1. Setup Mountpoint for Amazon S3
Mount your input and data buckets to the local filesystem of the EC2 instance or Fargate task:

```bash
# Mount PBF input bucket
mkdir -p /mnt/lifeline/input
mount-s3 my-lifeline-inputs-bucket /mnt/lifeline/input

# Mount Data storage bucket
mkdir -p /mnt/lifeline/data
mount-s3 my-lifeline-data-bucket /mnt/lifeline/data
```

### 2. Run the Container
Execute the container using the mount paths as environment variable overrides:

```bash
docker run -it --rm \
  -v /mnt/lifeline/input:/app/input \
  -v /mnt/lifeline/data:/app/data \
  -e LIFELINE_PBF_DIR=/app/input \
  -e LIFELINE_STORAGE_ROOT=/app/data \
  lifelinepoi marimo run flows/01_ingest.py -- --run-osm true
```

## Configuration Overrides

The following environment variables can be used to override the `config.lifeline.yaml` settings:

| Environment Variable | Description | Default (in Dockerfile) |
|----------------------|-------------|-------------------------|
| `LIFELINE_STORAGE_ROOT` | Root directory for bronze, silver, gold, and tiles data. | `/app/data` |
| `LIFELINE_PBF_DIR` | Directory containing the `.osm.pbf` extracts. | `/app/input` |
