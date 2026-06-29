# Use a lightweight Python 3.11 image
FROM python:3.11-slim

# Set environment variables to prevent Python from writing .pyc files and buffering stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Install system dependencies
# - build-essential: needed for some python package builds (e.g., rapidfuzz, shapely)
# - curl/wget: for potential downloads
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    wget \
    && rm -rf /var/lib/apt/lists/*

# Create and set the working directory
WORKDIR /app

# Copy dependency files first to leverage Docker cache
# Install uv for efficient dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Copy pyproject.toml and install dependencies using uv
COPY pyproject.toml .
RUN uv sync

# Copy the rest of the application code
COPY src/ ./src/
COPY flows/ ./flows/
COPY lib/ ./lib/
COPY config.lifeline.yaml .
COPY run_pipeline.py .
COPY . .

# Default environment variables (can be overridden at runtime)ß
ENV LIFELINE_STORAGE_ROOT=/app/data
ENV LIFELINE_PBF_DIR=/app/input

# Set the default command to run the ingestion flow as an example
# Note: Users will likely override this to run specific flows or use marimo.
CMD ["python", "run", "run_pipeline.py"]
