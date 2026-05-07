"""Configuration loader for LifelinePOI."""
from __future__ import annotations

import yaml
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class OSMConfig:
    pbf_path: str
    osmium_index_type: str = "flex_mem"
    layers: list[str] = field(default_factory=lambda: ["power", "water_infrastructure", "telecom", "fuel"])


@dataclass
class DuckDBConfig:
    memory_limit: str = "16GB"


@dataclass
class StorageConfig:
    """Storage paths for LifelinePOI data layers.

    Set ``root`` to an absolute directory (e.g. ``E:/lifelinepois/data``) and
    keep the four sub-paths relative.  If a sub-path is already absolute it is
    used as-is, so the old single-path style still works.
    """
    root: str = ""
    bronze_path: str = "bronze"
    silver_path: str = "silver"
    gold_path: str = "gold"
    tiles_path: str = "tiles"

    def __post_init__(self) -> None:
        if self.root:
            _root = Path(self.root)
            for attr in ("bronze_path", "silver_path", "gold_path", "tiles_path"):
                val = getattr(self, attr)
                if not Path(val).is_absolute():
                    setattr(self, attr, str(_root / val))


@dataclass
class EIAConfig:
    # Direct zip download URLs — update year as new data is published
    form860_zip_url: str = "https://www.eia.gov/electricity/data/eia860/archive/xls/eia8602023.zip"
    form923_zip_url: str = "https://www.eia.gov/electricity/data/eia923/archive/xls/f923_2023.zip"


@dataclass
class EPAConfig:
    # EPA Facility Registry Service national single-file CSV zip
    frs_zip_url: str = "https://ordsext.epa.gov/FLA/www3/state_files/national_single.zip"


@dataclass
class ConflationConfig:
    name_similarity_threshold: float = 0.85
    spatial_proximity_meters: float = 50.0
    confidence_weights: dict = field(default_factory=lambda: {
        "distance": 0.40, "attributes": 0.40, "source": 0.20
    })


@dataclass
class TilesConfig:
    min_zoom: int = 4
    max_zoom: int = 14
    layer_name: str = "lifeline_poi"


@dataclass
class LifelineConfig:
    osm: OSMConfig
    duckdb: DuckDBConfig = field(default_factory=DuckDBConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    eia: EIAConfig = field(default_factory=EIAConfig)
    epa: EPAConfig = field(default_factory=EPAConfig)
    conflation: ConflationConfig = field(default_factory=ConflationConfig)
    tiles: TilesConfig = field(default_factory=TilesConfig)

    @classmethod
    def from_yaml(cls, path: str | Path = "config.lifeline.yaml") -> "LifelineConfig":
        with open(path) as f:
            raw = yaml.safe_load(f)
        osm = OSMConfig(**raw["osm"])
        duckdb = DuckDBConfig(**raw.get("duckdb", {}))
        storage = StorageConfig(**raw.get("storage", {}))
        eia = EIAConfig(**raw.get("eia", {}))
        epa = EPAConfig(**raw.get("epa", {}))
        conflation_raw = raw.get("conflation", {})
        conflation = ConflationConfig(
            name_similarity_threshold=conflation_raw.get("name_similarity_threshold", 0.85),
            spatial_proximity_meters=conflation_raw.get("spatial_proximity_meters", 50.0),
            confidence_weights=conflation_raw.get("confidence_weights", {
                "distance": 0.40, "attributes": 0.40, "source": 0.20
            }),
        )
        tiles = TilesConfig(**raw.get("tiles", {}))
        return cls(osm=osm, duckdb=duckdb, storage=storage, eia=eia, epa=epa,
                   conflation=conflation, tiles=tiles)
