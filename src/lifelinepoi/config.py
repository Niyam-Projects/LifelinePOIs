"""Configuration loader for LifelinePOI."""
from __future__ import annotations

import yaml
from pathlib import Path
import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AreaConfig:
    """Geographic area definition for a processing run.

    Each area ties together an OSM PBF extract, a bounding box for spatial
    filtering, and optional state codes for future per-source filtering.

    Attributes:
        name: Human-readable label (e.g. ``"caribbean"``).
        pbf_path: Path to the OSM PBF file for this area.
        bbox: Optional ``[min_lon, min_lat, max_lon, max_lat]`` in WGS-84.
            Used to clip Overture places at conflation time.
        state_codes: ISO 3166-2 sub-division codes present in this area
            (e.g. ``["PR", "VI"]``). Stored for future per-source filtering;
            not used to gate any source in the current implementation.
    """
    name: str
    pbf_path: str
    bbox: Optional[list[float]] = None
    state_codes: list[str] = field(default_factory=list)


@dataclass
class OvertureConfig:
    """Overture Maps Places download configuration.

    Controls the taxonomy filtered, S3 source, and DuckDB resource knobs
    used by ``lib.overture.download_overture_snapshot``.

    Taxonomy allowlist uses ``taxonomy.hierarchy`` (not the deprecated
    ``categories.primary`` field, which is removed in June 2026).

    Attributes:
        bucket: S3 bucket hosting Overture releases (public, no auth).
        s3_region: AWS region of the S3 bucket.
        release: Overture release identifier, e.g. ``"2026-04-15.0"``, or
            ``"latest"`` to auto-discover the most recent release.
        places_taxonomy: List of ``[L0, L1]`` pairs (or ``[L0, null]`` to
            match all L1s under an L0) passed to
            ``lib.overture._build_taxonomy_predicate``.  The first entry in
            the list is treated as the highest-priority taxonomy category
            during conflation.
        places_path: Absolute path to the output GeoParquet file.  If blank,
            defaults to ``{bronze}/overture/places/us_territories.parquet``.
        duckdb_memory_limit: Per-connection DuckDB memory cap (e.g. ``"4GB"``).
        duckdb_threads: Per-connection DuckDB thread count.
        workers: Number of Overture parts to download in parallel.
    """
    bucket: str = "overturemaps-us-west-2"
    s3_region: str = "us-west-2"
    release: str = "latest"
    places_taxonomy: list = field(default_factory=lambda: [["health_care", "hospital"]])
    places_path: str = ""
    duckdb_memory_limit: str = "4GB"
    duckdb_threads: int = 2
    workers: int = 2


@dataclass
class OSMConfig:
    """OSM extraction configuration.

    ``pbf_path`` is retained for backward compatibility when no ``areas`` list
    is present in the YAML.  Prefer defining areas explicitly.
    """
    pbf_path: str = ""
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
        # Override root via environment variable if present
        env_root = os.environ.get("LIFELINE_STORAGE_ROOT")
        if env_root:
            self.root = env_root

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
class ECHOConfig:
    # EPA ECHO Exporter — one row per regulated facility (~1.5M facilities, ~500 MB)
    exporter_zip_url: str = "https://echo.epa.gov/files/echodownloads/echo_exporter.zip"


@dataclass
class SDWISConfig:
    # EPA SDWIS via ECHO SDWA data downloads (public water systems)
    sdwa_zip_url: str = "https://echo.epa.gov/files/echodownloads/SDWA_downloads.zip"


@dataclass
class FCCConfig:
    # FCC Antenna Structure Registration (ASR) - registered towers (r_tower.zip)
    # Old URL https://data.fcc.gov/download/asr/asr_full.zip returns 404
    asr_zip_url: str = "https://data.fcc.gov/download/pub/uls/complete/r_tower.zip"


@dataclass
class IRSConfig:
    # IRS Terminal Control Number (TCN) directory — fuel terminals (Excel, monthly update)
    tcn_xlsx_url: str = "https://www.irs.gov/pub/irs-sbse/tcn-db.xlsx"


@dataclass
class EIAApiConfig:
    """EIA Open Data API config — API key read from .env.local at runtime.
    Register at: https://www.eia.gov/opendata/register.php
    """
    env_key_name: str = "EIA_API_KEY"
    terminal_stocks_url: str = "https://api.eia.gov/v2/petroleum/move/terminalstocks/"


@dataclass
class HifldLayerDef:
    gcs_path: str
    lon_field: str
    lat_field: str
    osm_layer: str  # which silver OSM layer this validates
    lifeline_key: str = ""
    lifeline: str = ""
    lifeline_component: str = ""
    lifeline_subcomponent: str = ""
    display_name_field: str = ""   # which field to use as display_name
    nested_properties: bool = False  # True if real data is in a 'properties' dict column
    id_field: str = "OBJECTID"       # field to use in UUID5 generation


@dataclass
class HifldConfig:
    enabled: bool = True
    layers: dict = field(default_factory=lambda: {
        "hospitals": HifldLayerDef(
            gcs_path="gs://seerai-hifld-archive/hospitals/hospitals/hospitals.parquet",
            lon_field="LONGITUDE",
            lat_field="LATITUDE",
            osm_layer="health",
            lifeline_key="hospitals",
            lifeline="Health and Medical",
            lifeline_component="Medical Care",
            lifeline_subcomponent="Hospitals",
            display_name_field="NAME",
            nested_properties=False,
            id_field="OBJECTID",
        ),
        "cellular": HifldLayerDef(
            gcs_path="gs://seerai-hifld-archive/cellular-towers/cellular/cellular.parquet",
            lon_field="londec", lat_field="latdec", osm_layer="telecom",
            lifeline_key="comms_wireless",
            lifeline="Communications",
            lifeline_component="Infrastructure",
            lifeline_subcomponent="Wireless",
            display_name_field="Licensee",
            nested_properties=False,
            id_field="FID",
        ),
        "microwave": HifldLayerDef(
            gcs_path="gs://seerai-hifld-archive/microwave-service-towers/microwave/microwave.parquet",
            lon_field="properties.londec", lat_field="properties.latdec", osm_layer="telecom",
            lifeline_key="comms_wireless",
            lifeline="Communications",
            lifeline_component="Infrastructure",
            lifeline_subcomponent="Wireless",
            display_name_field="Licensee",
            nested_properties=True,
            id_field="FID",
        ),
        "lm_private": HifldLayerDef(
            gcs_path="gs://seerai-hifld-archive/land-mobile-private-transmission-towers/lm-private/lm-private.parquet",
            lon_field="properties.londec", lat_field="properties.latdec", osm_layer="telecom",
            lifeline_key="comms_wireless",
            lifeline="Communications",
            lifeline_component="Infrastructure",
            lifeline_subcomponent="Wireless",
            display_name_field="Licensee",
            nested_properties=True,
            id_field="FID",
        ),
        "lm_commercial": HifldLayerDef(
            gcs_path="gs://seerai-hifld-archive/land-mobile-commercial-transmission-towers/lm-commercial/lm-commercial.parquet",
            lon_field="properties.londec", lat_field="properties.latdec", osm_layer="telecom",
            lifeline_key="comms_cable_wireline",
            lifeline="Communications",
            lifeline_component="Infrastructure",
            lifeline_subcomponent="Cable Systems and Wireline",
            display_name_field="Licensee",
            nested_properties=True,
            id_field="FID",
        ),
        "wastewater_treatment_plants": HifldLayerDef(
            gcs_path="gs://seerai-hifld-archive/epa-facility-registry-service-frs---integrated-compliance-information-system-icis-wastewater-treatment-plants/integrated-compliance-information-system-icis-wastewater-treatment-plants/integrated-compliance-information-system-icis-wastewater-treatment-plants.parquet",
            lon_field="properties.FAC_LONG", lat_field="properties.FAC_LAT", osm_layer="water_infrastructure",
            lifeline_key="wastewater_treatment",
            lifeline="Water Systems",
            lifeline_component="Wastewater Management",
            lifeline_subcomponent="Wastewater Treatment",
            display_name_field="CWP_NAME",
            nested_properties=True,
            id_field="OBJECTID",
        ),
        "lng_terminals": HifldLayerDef(
            gcs_path="gs://seerai-hifld-archive/liquified-natural-gas-lng-import-and-export-terminals/lng-import-and-export-terminals/lng-import-and-export-terminals.parquet",
            lon_field="Longitude", lat_field="Latitude", osm_layer="fuel",
            lifeline_key="fuel_storage",
            lifeline="Energy",
            lifeline_component="Fuel",
            lifeline_subcomponent="Fuel Storage and Terminals",
            display_name_field="Facility",
            nested_properties=False,
            id_field="OBJECTID",
        ),
        "petroleum_refineries": HifldLayerDef(
            gcs_path="gs://seerai-hifld-archive/petroleum-refineries/petroleum-refinery/petroleum-refinery.parquet",
            lon_field="Longitude", lat_field="Latitude", osm_layer="fuel",
            lifeline_key="fuel_refineries",
            lifeline="Energy",
            lifeline_component="Fuel",
            lifeline_subcomponent="Petroleum Refineries",
            display_name_field="Site",
            nested_properties=False,
            id_field="OBJECTID",
        ),
    })


@dataclass
class CmsConfig:
    """CMS Hospital & Non-Hospital Provider Info enrichment configuration."""
    enabled: bool = True
    api_url: str = "https://data.cms.gov/data-api/v1/dataset/8ba0f9b4-9493-4aa0-9f82-44ea9468d1b5/data"
    name_similarity_threshold: float = 0.80
    page_size: int = 5000
    # Overture address geocoding — set to the partitioned address parquet root to enable
    geocode_address_path: Optional[str] = None
    geocode_min_score: float = 0.80
    # PRVDR_CTGRY_CD values to geocode; null = all records; ["01"] = hospitals only
    geocode_provider_categories: Optional[list] = None
    # Census Bureau batch geocoder fallback (free; covers 50 states + DC + PR)
    # followed by Nominatim for territory records still unmatched after Census.
    geocode_census_fallback: bool = True
    # Tier 1 spatial match: BallTree over CMS records with geocoded coordinates.
    # Buffer is larger than HIFLD (50 m) because address-level geocoding lands
    # at the kerb, not the building centroid.
    spatial_match_distance_m: float = 200.0
    # Minimum fuzzy name score required to accept a spatial match (guards against
    # coincidentally nearby hospitals in dense areas).
    spatial_name_threshold: float = 0.55


@dataclass
class EpaNaicsConfig:
    """EPA FRS NAICS/SIC lifeline boost + new POI minting configuration."""
    enabled: bool = True
    pass1_match_distance_m: float = 50.0
    pass2_match_distance_m: float = 100.0
    max_displacement_m: float = 500.0
    boost_tier1: float = 0.25
    boost_tier2: float = 0.15
    boost_tier3: float = 0.05
    mint_new_pois: bool = True
    geocode_address_path: Optional[str] = None


@dataclass
class CampusCollapseLayerConfig:
    """Per-OSM-layer campus collapse settings."""
    campus_polygon_amenities: list = field(default_factory=list)


@dataclass
class CampusCollapseConfig:
    """Campus-style POI collapse: one gold point per OSM campus polygon boundary.

    For each configured layer, OSM polygon features with an amenity value in
    ``campus_polygon_amenities`` define campus boundaries.  All silver points
    (nodes and building-footprint polygons) whose centroid falls within a
    boundary are collapsed into a single primary campus POI.  Sub-features are
    moved to ``silver/campus_buildings.parquet`` and the polygon geometry is
    saved to ``silver/campus_polygons.parquet``.
    """
    enabled: bool = True
    layers: dict = field(default_factory=lambda: {
        "health": CampusCollapseLayerConfig(campus_polygon_amenities=["hospital"]),
        "education": CampusCollapseLayerConfig(
            campus_polygon_amenities=["university", "school", "college", "kindergarten"]
        ),
    })


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
    osm: OSMConfig = field(default_factory=OSMConfig)
    duckdb: DuckDBConfig = field(default_factory=DuckDBConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    eia: EIAConfig = field(default_factory=EIAConfig)
    epa: EPAConfig = field(default_factory=EPAConfig)
    echo: ECHOConfig = field(default_factory=ECHOConfig)
    sdwis: SDWISConfig = field(default_factory=SDWISConfig)
    fcc: FCCConfig = field(default_factory=FCCConfig)
    irs: IRSConfig = field(default_factory=IRSConfig)
    eia_api: EIAApiConfig = field(default_factory=EIAApiConfig)
    conflation: ConflationConfig = field(default_factory=ConflationConfig)
    tiles: TilesConfig = field(default_factory=TilesConfig)
    hifld: HifldConfig = field(default_factory=HifldConfig)
    epa_naics: EpaNaicsConfig = field(default_factory=EpaNaicsConfig)
    campus_collapse: CampusCollapseConfig = field(default_factory=CampusCollapseConfig)
    cms: CmsConfig = field(default_factory=CmsConfig)
    areas: list[AreaConfig] = field(default_factory=list)
    overture: OvertureConfig = field(default_factory=OvertureConfig)
    # Active area for this run — populated from LIFELINE_AREA_NAME env var.
    # When set, cfg.aoi.bbox and cfg.aoi.state_codes are used by silver
    # conflation to clip national datasets (EPA FRS, HIFLD, etc.) to the
    # target geography, avoiding bleed-through from other states.
    aoi: Optional[AreaConfig] = None

    @classmethod
    def from_yaml(cls, path: str | Path = "config.lifeline.yaml") -> "LifelineConfig":
        with open(path) as f:
            raw = yaml.safe_load(f)

        # --- areas -----------------------------------------------------------
        # New style: explicit areas list.
        # Backward compat: if no areas but osm.pbf_path is set, wrap into one area.
        areas: list[AreaConfig] = []
        areas_raw = raw.get("areas", []) or []
        
        # Resolve PBF directory override from environment
        pbf_dir = os.environ.get("LIFELINE_PBF_DIR")
        
        for a in areas_raw:
            pbf_path = a.get("pbf_path", "")
            # If pbf_dir is set and pbf_path is relative, join them
            if pbf_dir and pbf_path and not Path(pbf_path).is_absolute():
                pbf_path = str(Path(pbf_dir) / pbf_path)
                
            areas.append(AreaConfig(
                name=a.get("name", "default"),
                pbf_path=pbf_path,
                bbox=a.get("bbox"),
                state_codes=a.get("state_codes", []),
            ))

        # --- osm -------------------------------------------------------------
        osm_raw = raw.get("osm", {})
        # If no areas defined but legacy pbf_path is present, create one area.
        if not areas and osm_raw.get("pbf_path"):
            aoi_bbox = (raw.get("aoi") or {}).get("bbox")
            pbf_path = osm_raw["pbf_path"]
            # Resolve PBF directory override from environment
            pbf_dir = os.environ.get("LIFELINE_PBF_DIR")
            if pbf_dir and pbf_path and not Path(pbf_path).is_absolute():
                pbf_path = str(Path(pbf_dir) / pbf_path)
                
            areas.append(AreaConfig(
                name="default",
                pbf_path=pbf_path,
                bbox=aoi_bbox,
                state_codes=[],
            ))
        # OSMConfig no longer requires pbf_path; use first area if available.
        osm_kwargs = {k: v for k, v in osm_raw.items() if k in OSMConfig.__dataclass_fields__}
        if "pbf_path" not in osm_kwargs and areas:
            osm_kwargs["pbf_path"] = areas[0].pbf_path
        osm = OSMConfig(**osm_kwargs)

        # --- overture --------------------------------------------------------
        overture_raw = raw.get("overture", {})
        overture = OvertureConfig(**{k: v for k, v in overture_raw.items()
                                     if k in OvertureConfig.__dataclass_fields__})

        duckdb = DuckDBConfig(**raw.get("duckdb", {}))
        storage = StorageConfig(**raw.get("storage", {}))
        eia = EIAConfig(**raw.get("eia", {}))
        epa = EPAConfig(**raw.get("epa", {}))
        echo = ECHOConfig(**raw.get("echo", {}))
        sdwis = SDWISConfig(**raw.get("sdwis", {}))
        fcc = FCCConfig(**raw.get("fcc", {}))
        irs = IRSConfig(**raw.get("irs", {}))
        eia_api = EIAApiConfig(**raw.get("eia_api", {}))
        conflation_raw = raw.get("conflation", {})
        conflation = ConflationConfig(
            name_similarity_threshold=conflation_raw.get("name_similarity_threshold", 0.85),
            spatial_proximity_meters=conflation_raw.get("spatial_proximity_meters", 50.0),
            confidence_weights=conflation_raw.get("confidence_weights", {
                "distance": 0.40, "attributes": 0.40, "source": 0.20
            }),
        )
        tiles = TilesConfig(**raw.get("tiles", {}))
        hifld_raw = raw.get("hifld", {})
        hifld = HifldConfig(enabled=hifld_raw.get("enabled", True)) if hifld_raw else HifldConfig()
        epa_naics_raw = raw.get("epa_naics", {})
        epa_naics = EpaNaicsConfig(**{k: v for k, v in epa_naics_raw.items()
                                      if k in EpaNaicsConfig.__dataclass_fields__})
        cc_raw = raw.get("campus_collapse", {})
        if cc_raw:
            cc_layers = {}
            for lname, ldata in cc_raw.get("layers", {}).items():
                cc_layers[lname] = CampusCollapseLayerConfig(
                    campus_polygon_amenities=ldata.get("campus_polygon_amenities", [])
                )
            campus_collapse = CampusCollapseConfig(
                enabled=cc_raw.get("enabled", True),
                layers=cc_layers if cc_layers else CampusCollapseConfig().layers,
            )
        else:
            campus_collapse = CampusCollapseConfig()
        cms_raw = raw.get("cms", {})
        cms = CmsConfig(**{k: v for k, v in cms_raw.items() if k in CmsConfig.__dataclass_fields__})
        return cls(osm=osm, duckdb=duckdb, storage=storage, eia=eia, epa=epa,
        # Resolve active area for this run from LIFELINE_AREA_NAME env var.
        # This populates cfg.aoi so that silver conflation can clip national
        # datasets (EPA FRS, HIFLD, CMS) to the target bbox + state_codes.
        area_name_env = os.environ.get("LIFELINE_AREA_NAME", "").strip()
        aoi: Optional[AreaConfig] = None
        if area_name_env and areas:
            aoi = next((a for a in areas if a.name == area_name_env), None)
            if aoi is None:
                import warnings
                warnings.warn(
                    f"LIFELINE_AREA_NAME='{area_name_env}' not found in config areas. "
                    f"Available: {[a.name for a in areas]}. Skipping bbox filter."
                )

        return cls(osm=osm, duckdb=duckdb, storage=storage, eia=eia, epa=epa,
                   echo=echo, sdwis=sdwis, fcc=fcc, irs=irs, eia_api=eia_api,
                   conflation=conflation, tiles=tiles, hifld=hifld, epa_naics=epa_naics,
                   campus_collapse=campus_collapse, cms=cms,
                   areas=areas, overture=overture, aoi=aoi)
