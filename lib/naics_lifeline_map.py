"""
NAICS and SIC code → FEMA Lifeline mapping for LifelinePOI.

Maps industry codes to FEMA lifeline taxonomy keys defined in
data/seed/fema_lifelines_taxonomy.csv, along with a confidence boost delta and
2-digit NAICS sector for downstream BLS QCEW / BEA GDP cross-referencing.

Tier definitions:
    1 = Critical  → boost 0.25  (core lifeline infrastructure)
    2 = High      → boost 0.15  (important supporting infrastructure)
    3 = Supporting → boost 0.05 (peripheral but relevant)

NAICS/BEA 2-digit sector reference (naics_sector field):
    "11" Agriculture     "21" Mining/Oil/Gas   "22" Utilities
    "23" Construction    "31" Manufacturing    "48" Transportation
    "51" Information     "52" Finance          "61" Education
    "62" Health Care     "92" Public Admin
"""
from __future__ import annotations

from typing import TypedDict


class LifelineEntry(TypedDict):
    lifeline_key: str           # snake_case leaf key, e.g. "hospitals"
    lifeline: str               # top-level human label, e.g. "Health and Medical"
    lifeline_component: str     # component human label, e.g. "Medical Care"
    lifeline_subcomponent: str  # subcomponent human label, e.g. "Hospitals"
    tier: int                   # 1, 2, or 3
    boost: float                # additive confidence delta
    naics_sector: str           # 2-digit NAICS for BLS/BEA join
    bls_title: str              # human-readable label


# ---------------------------------------------------------------------------
# Hierarchy lookup: leaf key → (lifeline_key, lifeline_component_key)
# Used to build fema_lifeline struct dicts without reading the taxonomy CSV.
# ---------------------------------------------------------------------------
_KEY_HIERARCHY: dict[str, tuple[str, str]] = {
    "power_generation":               ("energy",               "power_grid"),
    "power_transmission":             ("energy",               "power_grid"),
    "power_distribution":             ("energy",               "power_grid"),
    "fuel_refineries":                ("energy",               "fuel"),
    "fuel_storage":                   ("energy",               "fuel"),
    "fuel_pipelines":                 ("energy",               "fuel"),
    "fuel_distribution":              ("energy",               "fuel"),
    "offshore_oil_platforms":         ("energy",               "fuel"),
    "wastewater_treatment":           ("water_systems",        "wastewater_management"),
    "wastewater_collection":          ("water_systems",        "wastewater_management"),
    "wastewater_storage":             ("water_systems",        "wastewater_management"),
    "wastewater_discharge":           ("water_systems",        "wastewater_management"),
    "potable_water_intake":           ("water_systems",        "potable_water_infrastructure"),
    "potable_water_treatment":        ("water_systems",        "potable_water_infrastructure"),
    "potable_water_storage":          ("water_systems",        "potable_water_infrastructure"),
    "potable_water_distribution":     ("water_systems",        "potable_water_infrastructure"),
    "hospitals":                      ("health_and_medical",   "medical_care"),
    "dialysis":                       ("health_and_medical",   "medical_care"),
    "pharmacies":                     ("health_and_medical",   "medical_care"),
    "long_term_care":                 ("health_and_medical",   "medical_care"),
    "va_health_system":               ("health_and_medical",   "medical_care"),
    "veterinary_services":            ("health_and_medical",   "medical_care"),
    "home_care":                      ("health_and_medical",   "medical_care"),
    "health_surveillance":            ("health_and_medical",   "public_health"),
    "public_health_human_services":   ("health_and_medical",   "public_health"),
    "behavioral_health":              ("health_and_medical",   "public_health"),
    "vector_control":                 ("health_and_medical",   "public_health"),
    "public_health_labs":             ("health_and_medical",   "public_health"),
    "emergency_medical_services":     ("health_and_medical",   "patient_movement"),
    "medical_supply_blood":           ("health_and_medical",   "medical_supply_chain"),
    "medical_supply_pharma_devices":  ("health_and_medical",   "medical_supply_chain"),
    "medical_supply_gases":           ("health_and_medical",   "medical_supply_chain"),
    "medical_supply_distribution":    ("health_and_medical",   "medical_supply_chain"),
    "critical_clinical_research":     ("health_and_medical",   "medical_supply_chain"),
    "medical_supply_sterilization":   ("health_and_medical",   "medical_supply_chain"),
    "medical_supply_raw_materials":   ("health_and_medical",   "medical_supply_chain"),
    "mortuary_services":              ("health_and_medical",   "fatality_management"),
    "police_stations":                ("safety_and_security",  "law_enforcement_security"),
    "law_enforcement":                ("safety_and_security",  "law_enforcement_security"),
    "site_security":                  ("safety_and_security",  "law_enforcement_security"),
    "correctional_facilities":        ("safety_and_security",  "law_enforcement_security"),
    "fire_stations":                  ("safety_and_security",  "fire_service"),
    "firefighting_resources":         ("safety_and_security",  "fire_service"),
    "search_and_rescue":              ("safety_and_security",  "fire_service"),
    "local_search_and_rescue":        ("safety_and_security",  "fire_service"),
    "emergency_operation_centers":    ("safety_and_security",  "government_service"),
    "essential_government_functions": ("safety_and_security",  "government_service"),
    "government_offices":             ("safety_and_security",  "government_service"),
    "government_schools":             ("safety_and_security",  "government_service"),
    "public_records":                 ("safety_and_security",  "government_service"),
    "historic_cultural_resources":    ("safety_and_security",  "government_service"),
    "flood_control":                  ("safety_and_security",  "community_safety"),
    "community_other_hazards":        ("safety_and_security",  "community_safety"),
    "food_commercial_distribution":   ("food_hydration_shelter", "food"),
    "food_supply_chain":              ("food_hydration_shelter", "food"),
    "food_distribution_programs":     ("food_hydration_shelter", "food"),
    "hydration_temporary_missions":   ("food_hydration_shelter", "hydration"),
    "commercial_water_supply_chain":  ("food_hydration_shelter", "hydration"),
    "shelter_housing":                ("food_hydration_shelter", "shelter"),
    "shelter_commercial_facilities":  ("food_hydration_shelter", "shelter"),
    "animals_and_agriculture":        ("food_hydration_shelter", "agriculture"),
    "comms_wireless":                 ("communications",       "comms_infrastructure"),
    "comms_cable_wireline":           ("communications",       "comms_infrastructure"),
    "comms_broadcast":                ("communications",       "comms_infrastructure"),
    "comms_satellite":                ("communications",       "comms_infrastructure"),
    "data_centers_internet":          ("communications",       "comms_infrastructure"),
    "land_mobile_radio":              ("communications",       "responder_communications"),
    "public_safety_answering_points": ("communications",       "911_and_dispatch"),
    "emergency_dispatch":             ("communications",       "911_and_dispatch"),
    "banking_services":               ("communications",       "comms_finance"),
    "electronic_payment_processing":  ("communications",       "comms_finance"),
    "local_alert_warning":            ("communications",       "alerts_warnings_messages"),
    "ipaws_access":                   ("communications",       "alerts_warnings_messages"),
    "nawas_terminals":                ("communications",       "alerts_warnings_messages"),
    "roads":                          ("transportation",       "highway_roadway_motor_vehicle"),
    "bridges":                        ("transportation",       "highway_roadway_motor_vehicle"),
    "mass_transit_bus":               ("transportation",       "mass_transit"),
    "mass_transit_rail":              ("transportation",       "mass_transit"),
    "mass_transit_ferry":             ("transportation",       "mass_transit"),
    "railway_freight":                ("transportation",       "railway"),
    "railway_passenger":              ("transportation",       "railway"),
    "aviation_commercial":            ("transportation",       "aviation"),
    "aviation_general":               ("transportation",       "aviation"),
    "aviation_military":              ("transportation",       "aviation"),
    "maritime_waterways":             ("transportation",       "maritime"),
    "maritime_ports":                 ("transportation",       "maritime"),
    "hazmat_facilities":              ("hazardous_materials",  "hazmat_facilities_component"),
    "hazmat_facility_incidents":      ("hazardous_materials",  "hazmat_facilities_component"),
    "hazmat_mobile_incidents":        ("hazardous_materials",  "hazmat_pollutants_contaminants"),
    "radiological_nuclear_incidents": ("hazardous_materials",  "hazmat_pollutants_contaminants"),
}


def make_fema_lifeline_struct(lifeline_key: str) -> dict:
    """Build a fema_lifeline struct dict for a given leaf taxonomy key."""
    lf_key, comp_key = _KEY_HIERARCHY.get(lifeline_key, ("", ""))
    return {
        "primary": lifeline_key,
        "hierarchy": [lf_key, comp_key, lifeline_key],
        "alternates": [],
    }


# ---------------------------------------------------------------------------
# NAICS Code → Lifeline Map
# Keys are 6-digit NAICS strings (no spaces). Multi-code entries in the FRS
# parquet (pipe or comma-delimited) are split before lookup.
# ---------------------------------------------------------------------------
NAICS_LIFELINE_MAP: dict[str, LifelineEntry] = {

    # ------------------------------------------------------------------
    # Energy / Power Grid / Generation
    # ------------------------------------------------------------------
    "221111": {"lifeline_key": "power_generation",
               "lifeline": "Energy", "lifeline_component": "Power Grid",
               "lifeline_subcomponent": "Electric Power Generation",
               "tier": 1, "boost": 0.25, "naics_sector": "22",
               "bls_title": "Hydroelectric Power Generation"},
    "221112": {"lifeline_key": "power_generation",
               "lifeline": "Energy", "lifeline_component": "Power Grid",
               "lifeline_subcomponent": "Electric Power Generation",
               "tier": 1, "boost": 0.25, "naics_sector": "22",
               "bls_title": "Fossil Fuel Electric Power Generation"},
    "221113": {"lifeline_key": "power_generation",
               "lifeline": "Energy", "lifeline_component": "Power Grid",
               "lifeline_subcomponent": "Electric Power Generation",
               "tier": 1, "boost": 0.25, "naics_sector": "22",
               "bls_title": "Nuclear Electric Power Generation"},
    "221114": {"lifeline_key": "power_generation",
               "lifeline": "Energy", "lifeline_component": "Power Grid",
               "lifeline_subcomponent": "Electric Power Generation",
               "tier": 1, "boost": 0.25, "naics_sector": "22",
               "bls_title": "Solar Electric Power Generation"},
    "221115": {"lifeline_key": "power_generation",
               "lifeline": "Energy", "lifeline_component": "Power Grid",
               "lifeline_subcomponent": "Electric Power Generation",
               "tier": 1, "boost": 0.25, "naics_sector": "22",
               "bls_title": "Wind Electric Power Generation"},
    "221116": {"lifeline_key": "power_generation",
               "lifeline": "Energy", "lifeline_component": "Power Grid",
               "lifeline_subcomponent": "Electric Power Generation",
               "tier": 1, "boost": 0.25, "naics_sector": "22",
               "bls_title": "Geothermal Electric Power Generation"},
    "221117": {"lifeline_key": "power_generation",
               "lifeline": "Energy", "lifeline_component": "Power Grid",
               "lifeline_subcomponent": "Electric Power Generation",
               "tier": 1, "boost": 0.25, "naics_sector": "22",
               "bls_title": "Biomass Electric Power Generation"},
    "221118": {"lifeline_key": "power_generation",
               "lifeline": "Energy", "lifeline_component": "Power Grid",
               "lifeline_subcomponent": "Electric Power Generation",
               "tier": 1, "boost": 0.25, "naics_sector": "22",
               "bls_title": "Other Electric Power Generation"},
    "221119": {"lifeline_key": "power_generation",
               "lifeline": "Energy", "lifeline_component": "Power Grid",
               "lifeline_subcomponent": "Electric Power Generation",
               "tier": 1, "boost": 0.25, "naics_sector": "22",
               "bls_title": "Other Electric Power Generation"},

    # ------------------------------------------------------------------
    # Energy / Power Grid / Transmission & Distribution
    # ------------------------------------------------------------------
    "221121": {"lifeline_key": "power_transmission",
               "lifeline": "Energy", "lifeline_component": "Power Grid",
               "lifeline_subcomponent": "Electric Power Transmission",
               "tier": 1, "boost": 0.25, "naics_sector": "22",
               "bls_title": "Electric Bulk Power Transmission and Control"},
    "221122": {"lifeline_key": "power_distribution",
               "lifeline": "Energy", "lifeline_component": "Power Grid",
               "lifeline_subcomponent": "Electric Power Distribution",
               "tier": 1, "boost": 0.25, "naics_sector": "22",
               "bls_title": "Electric Power Distribution"},
    "221210": {"lifeline_key": "power_distribution",
               "lifeline": "Energy", "lifeline_component": "Power Grid",
               "lifeline_subcomponent": "Electric Power Distribution",
               "tier": 1, "boost": 0.25, "naics_sector": "22",
               "bls_title": "Natural Gas Distribution"},

    # ------------------------------------------------------------------
    # Energy / Fuel / Pipelines
    # ------------------------------------------------------------------
    "486210": {"lifeline_key": "fuel_pipelines",
               "lifeline": "Energy", "lifeline_component": "Fuel",
               "lifeline_subcomponent": "Fuel Pipelines",
               "tier": 1, "boost": 0.25, "naics_sector": "48",
               "bls_title": "Pipeline Transportation of Natural Gas"},
    "486110": {"lifeline_key": "fuel_pipelines",
               "lifeline": "Energy", "lifeline_component": "Fuel",
               "lifeline_subcomponent": "Fuel Pipelines",
               "tier": 1, "boost": 0.25, "naics_sector": "48",
               "bls_title": "Pipeline Transportation of Crude Oil"},
    "486910": {"lifeline_key": "fuel_pipelines",
               "lifeline": "Energy", "lifeline_component": "Fuel",
               "lifeline_subcomponent": "Fuel Pipelines",
               "tier": 2, "boost": 0.15, "naics_sector": "48",
               "bls_title": "Other Pipeline Transportation"},

    # ------------------------------------------------------------------
    # Water Systems / Wastewater Management
    # ------------------------------------------------------------------
    "221320": {"lifeline_key": "wastewater_treatment",
               "lifeline": "Water Systems", "lifeline_component": "Wastewater Management",
               "lifeline_subcomponent": "Wastewater Treatment",
               "tier": 1, "boost": 0.25, "naics_sector": "22",
               "bls_title": "Sewage Treatment Facilities"},

    # ------------------------------------------------------------------
    # Energy / Fuel / Refineries & Storage
    # ------------------------------------------------------------------
    "324110": {"lifeline_key": "fuel_refineries",
               "lifeline": "Energy", "lifeline_component": "Fuel",
               "lifeline_subcomponent": "Petroleum Refineries",
               "tier": 1, "boost": 0.25, "naics_sector": "31",
               "bls_title": "Petroleum Refineries"},
    "493120": {"lifeline_key": "fuel_storage",
               "lifeline": "Energy", "lifeline_component": "Fuel",
               "lifeline_subcomponent": "Fuel Storage and Terminals",
               "tier": 2, "boost": 0.15, "naics_sector": "48",
               "bls_title": "Refrigerated Warehousing and Storage"},

    # ------------------------------------------------------------------
    # Hazardous Materials / Facilities
    # ------------------------------------------------------------------
    "562212": {"lifeline_key": "hazmat_facilities",
               "lifeline": "Hazardous Materials", "lifeline_component": "Facilities",
               "lifeline_subcomponent": "Hazardous Material Facilities",
               "tier": 2, "boost": 0.15, "naics_sector": "56",
               "bls_title": "Solid Waste Landfill"},
    "562910": {"lifeline_key": "hazmat_facilities",
               "lifeline": "Hazardous Materials", "lifeline_component": "Facilities",
               "lifeline_subcomponent": "Hazardous Material Facilities",
               "tier": 3, "boost": 0.05, "naics_sector": "56",
               "bls_title": "Remediation Services"},
    "562998": {"lifeline_key": "hazmat_facilities",
               "lifeline": "Hazardous Materials", "lifeline_component": "Facilities",
               "lifeline_subcomponent": "Hazardous Material Facilities",
               "tier": 3, "boost": 0.05, "naics_sector": "56",
               "bls_title": "All Other Miscellaneous Waste Management Services"},

    # ------------------------------------------------------------------
    # Energy / Fuel / Oil and Gas Extraction
    # ------------------------------------------------------------------
    "211111": {"lifeline_key": "offshore_oil_platforms",
               "lifeline": "Energy", "lifeline_component": "Fuel",
               "lifeline_subcomponent": "Offshore Oil/Gas Platforms",
               "tier": 2, "boost": 0.15, "naics_sector": "21",
               "bls_title": "Crude Petroleum and Natural Gas Extraction"},
    "211112": {"lifeline_key": "offshore_oil_platforms",
               "lifeline": "Energy", "lifeline_component": "Fuel",
               "lifeline_subcomponent": "Offshore Oil/Gas Platforms",
               "tier": 2, "boost": 0.15, "naics_sector": "21",
               "bls_title": "Natural Gas Liquid Extraction"},
    "211120": {"lifeline_key": "offshore_oil_platforms",
               "lifeline": "Energy", "lifeline_component": "Fuel",
               "lifeline_subcomponent": "Offshore Oil/Gas Platforms",
               "tier": 2, "boost": 0.15, "naics_sector": "21",
               "bls_title": "Crude Petroleum Extraction"},
    "211130": {"lifeline_key": "offshore_oil_platforms",
               "lifeline": "Energy", "lifeline_component": "Fuel",
               "lifeline_subcomponent": "Offshore Oil/Gas Platforms",
               "tier": 2, "boost": 0.15, "naics_sector": "21",
               "bls_title": "Natural Gas Extraction"},
    "213112": {"lifeline_key": "offshore_oil_platforms",
               "lifeline": "Energy", "lifeline_component": "Fuel",
               "lifeline_subcomponent": "Offshore Oil/Gas Platforms",
               "tier": 3, "boost": 0.05, "naics_sector": "21",
               "bls_title": "Support Activities for Oil and Gas Operations"},
    "424710": {"lifeline_key": "fuel_storage",
               "lifeline": "Energy", "lifeline_component": "Fuel",
               "lifeline_subcomponent": "Fuel Storage and Terminals",
               "tier": 1, "boost": 0.25, "naics_sector": "42",
               "bls_title": "Petroleum Bulk Stations and Terminals"},
    "424720": {"lifeline_key": "fuel_storage",
               "lifeline": "Energy", "lifeline_component": "Fuel",
               "lifeline_subcomponent": "Fuel Storage and Terminals",
               "tier": 2, "boost": 0.15, "naics_sector": "42",
               "bls_title": "Petroleum and Petroleum Products Merchant Wholesalers"},

    # ------------------------------------------------------------------
    # Water Systems / Potable Water
    # ------------------------------------------------------------------
    "221310": {"lifeline_key": "potable_water_distribution",
               "lifeline": "Water Systems",
               "lifeline_component": "Potable Water Infrastructure",
               "lifeline_subcomponent": "Potable Water Distribution",
               "tier": 1, "boost": 0.25, "naics_sector": "22",
               "bls_title": "Water Supply and Irrigation Systems"},

    # ------------------------------------------------------------------
    # Health and Medical / Medical Care
    # ------------------------------------------------------------------
    "622110": {"lifeline_key": "hospitals",
               "lifeline": "Health and Medical", "lifeline_component": "Medical Care",
               "lifeline_subcomponent": "Hospitals",
               "tier": 1, "boost": 0.25, "naics_sector": "62",
               "bls_title": "General Medical and Surgical Hospitals"},
    "622210": {"lifeline_key": "hospitals",
               "lifeline": "Health and Medical", "lifeline_component": "Medical Care",
               "lifeline_subcomponent": "Hospitals",
               "tier": 2, "boost": 0.15, "naics_sector": "62",
               "bls_title": "Psychiatric and Substance Abuse Hospitals"},
    "622310": {"lifeline_key": "hospitals",
               "lifeline": "Health and Medical", "lifeline_component": "Medical Care",
               "lifeline_subcomponent": "Hospitals",
               "tier": 2, "boost": 0.15, "naics_sector": "62",
               "bls_title": "Specialty Hospitals"},
    "621111": {"lifeline_key": "public_health_human_services",
               "lifeline": "Health and Medical", "lifeline_component": "Public Health",
               "lifeline_subcomponent": "Public Health/Human Services",
               "tier": 2, "boost": 0.15, "naics_sector": "62",
               "bls_title": "Offices of Physicians (except Mental Health)"},
    "446110": {"lifeline_key": "pharmacies",
               "lifeline": "Health and Medical", "lifeline_component": "Medical Care",
               "lifeline_subcomponent": "Pharmacies",
               "tier": 2, "boost": 0.15, "naics_sector": "44",
               "bls_title": "Pharmacies and Drug Stores"},
    "325412": {"lifeline_key": "medical_supply_pharma_devices",
               "lifeline": "Health and Medical", "lifeline_component": "Medical Supply Chain",
               "lifeline_subcomponent": "Pharmaceutical/Medical Devices",
               "tier": 3, "boost": 0.05, "naics_sector": "31",
               "bls_title": "Pharmaceutical Preparation Manufacturing"},

    # ------------------------------------------------------------------
    # Health and Medical / Patient Movement / Emergency
    # ------------------------------------------------------------------
    "621493": {"lifeline_key": "emergency_medical_services",
               "lifeline": "Health and Medical", "lifeline_component": "Patient Movement",
               "lifeline_subcomponent": "Emergency Medical Services",
               "tier": 1, "boost": 0.25, "naics_sector": "62",
               "bls_title": "Freestanding Ambulatory Surgical and Emergency Centers"},
    "621999": {"lifeline_key": "public_health_human_services",
               "lifeline": "Health and Medical", "lifeline_component": "Public Health",
               "lifeline_subcomponent": "Public Health/Human Services",
               "tier": 2, "boost": 0.15, "naics_sector": "62",
               "bls_title": "All Other Miscellaneous Ambulatory Health Care Services"},
    "621910": {"lifeline_key": "emergency_medical_services",
               "lifeline": "Health and Medical", "lifeline_component": "Patient Movement",
               "lifeline_subcomponent": "Emergency Medical Services",
               "tier": 2, "boost": 0.15, "naics_sector": "62",
               "bls_title": "Ambulance Services"},

    # ------------------------------------------------------------------
    # Safety and Security / Law Enforcement
    # ------------------------------------------------------------------
    "928110": {"lifeline_key": "police_stations",
               "lifeline": "Safety and Security",
               "lifeline_component": "Law Enforcement/Security",
               "lifeline_subcomponent": "Police Stations",
               "tier": 1, "boost": 0.25, "naics_sector": "92",
               "bls_title": "National Security"},
    "922120": {"lifeline_key": "police_stations",
               "lifeline": "Safety and Security",
               "lifeline_component": "Law Enforcement/Security",
               "lifeline_subcomponent": "Police Stations",
               "tier": 1, "boost": 0.25, "naics_sector": "92",
               "bls_title": "Police Protection"},

    # ------------------------------------------------------------------
    # Safety and Security / Fire Service
    # ------------------------------------------------------------------
    "922160": {"lifeline_key": "fire_stations",
               "lifeline": "Safety and Security", "lifeline_component": "Fire Service",
               "lifeline_subcomponent": "Fire Stations",
               "tier": 1, "boost": 0.25, "naics_sector": "92",
               "bls_title": "Fire Protection"},

    # ------------------------------------------------------------------
    # Communications / Infrastructure / Wireless & Wireline
    # ------------------------------------------------------------------
    "517210": {"lifeline_key": "comms_wireless",
               "lifeline": "Communications", "lifeline_component": "Infrastructure",
               "lifeline_subcomponent": "Wireless",
               "tier": 1, "boost": 0.25, "naics_sector": "51",
               "bls_title": "Wireless Telecommunications Carriers (except Satellite)"},
    "517211": {"lifeline_key": "comms_wireless",
               "lifeline": "Communications", "lifeline_component": "Infrastructure",
               "lifeline_subcomponent": "Wireless",
               "tier": 1, "boost": 0.25, "naics_sector": "51",
               "bls_title": "Paging"},
    "517212": {"lifeline_key": "comms_wireless",
               "lifeline": "Communications", "lifeline_component": "Infrastructure",
               "lifeline_subcomponent": "Wireless",
               "tier": 1, "boost": 0.25, "naics_sector": "51",
               "bls_title": "Cellular and Other Wireless Telecommunications"},
    "517410": {"lifeline_key": "comms_satellite",
               "lifeline": "Communications", "lifeline_component": "Infrastructure",
               "lifeline_subcomponent": "Satellite",
               "tier": 2, "boost": 0.15, "naics_sector": "51",
               "bls_title": "Satellite Telecommunications"},
    "517110": {"lifeline_key": "comms_cable_wireline",
               "lifeline": "Communications", "lifeline_component": "Infrastructure",
               "lifeline_subcomponent": "Cable/Wireline",
               "tier": 1, "boost": 0.25, "naics_sector": "51",
               "bls_title": "Wired Telecommunications Carriers"},
    "517311": {"lifeline_key": "comms_cable_wireline",
               "lifeline": "Communications", "lifeline_component": "Infrastructure",
               "lifeline_subcomponent": "Cable/Wireline",
               "tier": 1, "boost": 0.25, "naics_sector": "51",
               "bls_title": "Wired Telecommunications Carriers"},
    "237130": {"lifeline_key": "comms_cable_wireline",
               "lifeline": "Communications", "lifeline_component": "Infrastructure",
               "lifeline_subcomponent": "Cable/Wireline",
               "tier": 3, "boost": 0.05, "naics_sector": "23",
               "bls_title": "Power and Communication Line and Related Structures Construction"},

    # ------------------------------------------------------------------
    # Transportation / Aviation
    # ------------------------------------------------------------------
    "488119": {"lifeline_key": "aviation_commercial",
               "lifeline": "Transportation", "lifeline_component": "Aviation",
               "lifeline_subcomponent": "Commercial Aviation",
               "tier": 1, "boost": 0.25, "naics_sector": "48",
               "bls_title": "Other Airport Operations"},
    "488111": {"lifeline_key": "aviation_commercial",
               "lifeline": "Transportation", "lifeline_component": "Aviation",
               "lifeline_subcomponent": "Commercial Aviation",
               "tier": 1, "boost": 0.25, "naics_sector": "48",
               "bls_title": "Air Traffic Control"},
    "481111": {"lifeline_key": "aviation_commercial",
               "lifeline": "Transportation", "lifeline_component": "Aviation",
               "lifeline_subcomponent": "Commercial Aviation",
               "tier": 2, "boost": 0.15, "naics_sector": "48",
               "bls_title": "Scheduled Passenger Air Transportation"},
    "481112": {"lifeline_key": "aviation_commercial",
               "lifeline": "Transportation", "lifeline_component": "Aviation",
               "lifeline_subcomponent": "Commercial Aviation",
               "tier": 2, "boost": 0.15, "naics_sector": "48",
               "bls_title": "Scheduled Freight Air Transportation"},

    # ------------------------------------------------------------------
    # Transportation / Highway (Truck)
    # ------------------------------------------------------------------
    "484110": {"lifeline_key": "roads",
               "lifeline": "Transportation", "lifeline_component": "Highway Roadway Motor Vehicle",
               "lifeline_subcomponent": "Roads",
               "tier": 3, "boost": 0.05, "naics_sector": "48",
               "bls_title": "General Freight Trucking, Local"},
    "484121": {"lifeline_key": "roads",
               "lifeline": "Transportation", "lifeline_component": "Highway Roadway Motor Vehicle",
               "lifeline_subcomponent": "Roads",
               "tier": 3, "boost": 0.05, "naics_sector": "48",
               "bls_title": "General Freight Trucking, Long-Distance, Truckload"},
    "484122": {"lifeline_key": "roads",
               "lifeline": "Transportation", "lifeline_component": "Highway Roadway Motor Vehicle",
               "lifeline_subcomponent": "Roads",
               "tier": 3, "boost": 0.05, "naics_sector": "48",
               "bls_title": "General Freight Trucking, Long-Distance, Less Than Truckload"},

    # ------------------------------------------------------------------
    # Transportation / Railway
    # ------------------------------------------------------------------
    "482111": {"lifeline_key": "railway_freight",
               "lifeline": "Transportation", "lifeline_component": "Railway",
               "lifeline_subcomponent": "Rail Freight",
               "tier": 1, "boost": 0.25, "naics_sector": "48",
               "bls_title": "Line-Haul Railroads"},
    "482112": {"lifeline_key": "railway_freight",
               "lifeline": "Transportation", "lifeline_component": "Railway",
               "lifeline_subcomponent": "Rail Freight",
               "tier": 2, "boost": 0.15, "naics_sector": "48",
               "bls_title": "Short Line Railroads"},
}


# ---------------------------------------------------------------------------
# SIC Code → Lifeline Map
# ---------------------------------------------------------------------------
SIC_LIFELINE_MAP: dict[str, LifelineEntry] = {

    # Energy — Generation
    "4911": {"lifeline_key": "power_generation",
             "lifeline": "Energy", "lifeline_component": "Power Grid",
             "lifeline_subcomponent": "Electric Power Generation",
             "tier": 1, "boost": 0.25, "naics_sector": "22",
             "bls_title": "Electric Services"},
    "4931": {"lifeline_key": "power_generation",
             "lifeline": "Energy", "lifeline_component": "Power Grid",
             "lifeline_subcomponent": "Electric Power Generation",
             "tier": 1, "boost": 0.25, "naics_sector": "22",
             "bls_title": "Electric and Other Services Combined"},
    "4939": {"lifeline_key": "power_generation",
             "lifeline": "Energy", "lifeline_component": "Power Grid",
             "lifeline_subcomponent": "Electric Power Generation",
             "tier": 2, "boost": 0.15, "naics_sector": "22",
             "bls_title": "Combination Utilities, NEC"},

    # Energy — Natural Gas Pipelines
    "4922": {"lifeline_key": "fuel_pipelines",
             "lifeline": "Energy", "lifeline_component": "Fuel",
             "lifeline_subcomponent": "Fuel Pipelines",
             "tier": 1, "boost": 0.25, "naics_sector": "22",
             "bls_title": "Natural Gas Transmission"},
    "4923": {"lifeline_key": "power_distribution",
             "lifeline": "Energy", "lifeline_component": "Power Grid",
             "lifeline_subcomponent": "Electric Power Distribution",
             "tier": 1, "boost": 0.25, "naics_sector": "22",
             "bls_title": "Natural Gas Transmission and Distribution"},
    "4924": {"lifeline_key": "power_distribution",
             "lifeline": "Energy", "lifeline_component": "Power Grid",
             "lifeline_subcomponent": "Electric Power Distribution",
             "tier": 1, "boost": 0.25, "naics_sector": "22",
             "bls_title": "Natural Gas Distribution"},
    "4991": {"lifeline_key": "power_generation",
             "lifeline": "Energy", "lifeline_component": "Power Grid",
             "lifeline_subcomponent": "Electric Power Generation",
             "tier": 2, "boost": 0.15, "naics_sector": "22",
             "bls_title": "Cogeneration Services and Small Power Producers"},

    # Energy — Fuel / Oil and Gas
    "1311": {"lifeline_key": "offshore_oil_platforms",
             "lifeline": "Energy", "lifeline_component": "Fuel",
             "lifeline_subcomponent": "Offshore Oil/Gas Platforms",
             "tier": 2, "boost": 0.15, "naics_sector": "21",
             "bls_title": "Crude Petroleum and Natural Gas"},
    "1321": {"lifeline_key": "offshore_oil_platforms",
             "lifeline": "Energy", "lifeline_component": "Fuel",
             "lifeline_subcomponent": "Offshore Oil/Gas Platforms",
             "tier": 2, "boost": 0.15, "naics_sector": "21",
             "bls_title": "Natural Gas Liquids"},
    "2911": {"lifeline_key": "fuel_refineries",
             "lifeline": "Energy", "lifeline_component": "Fuel",
             "lifeline_subcomponent": "Petroleum Refineries",
             "tier": 1, "boost": 0.25, "naics_sector": "31",
             "bls_title": "Petroleum Refining"},
    "5171": {"lifeline_key": "fuel_storage",
             "lifeline": "Energy", "lifeline_component": "Fuel",
             "lifeline_subcomponent": "Fuel Storage and Terminals",
             "tier": 1, "boost": 0.25, "naics_sector": "51",
             "bls_title": "Petroleum Bulk Stations and Terminals"},
    "5172": {"lifeline_key": "fuel_storage",
             "lifeline": "Energy", "lifeline_component": "Fuel",
             "lifeline_subcomponent": "Fuel Storage and Terminals",
             "tier": 2, "boost": 0.15, "naics_sector": "51",
             "bls_title": "Petroleum and Petroleum Products Wholesalers"},

    # Hazardous Materials
    "4953": {"lifeline_key": "hazmat_facilities",
             "lifeline": "Hazardous Materials", "lifeline_component": "Facilities",
             "lifeline_subcomponent": "Hazardous Material Facilities",
             "tier": 2, "boost": 0.15, "naics_sector": "49",
             "bls_title": "Refuse Systems"},
    "9511": {"lifeline_key": "hazmat_facilities",
             "lifeline": "Hazardous Materials", "lifeline_component": "Facilities",
             "lifeline_subcomponent": "Hazardous Material Facilities",
             "tier": 3, "boost": 0.05, "naics_sector": "92",
             "bls_title": "Air and Water Resource and Solid Waste Management"},

    # Water Systems — Potable Water
    "4941": {"lifeline_key": "potable_water_distribution",
             "lifeline": "Water Systems",
             "lifeline_component": "Potable Water Infrastructure",
             "lifeline_subcomponent": "Potable Water Distribution",
             "tier": 1, "boost": 0.25, "naics_sector": "22",
             "bls_title": "Water Supply"},

    # Water Systems — Wastewater
    "4952": {"lifeline_key": "wastewater_treatment",
             "lifeline": "Water Systems", "lifeline_component": "Wastewater Management",
             "lifeline_subcomponent": "Wastewater Treatment",
             "tier": 1, "boost": 0.25, "naics_sector": "22",
             "bls_title": "Sewerage Systems"},
    "4959": {"lifeline_key": "wastewater_treatment",
             "lifeline": "Water Systems", "lifeline_component": "Wastewater Management",
             "lifeline_subcomponent": "Wastewater Treatment",
             "tier": 2, "boost": 0.15, "naics_sector": "22",
             "bls_title": "Sanitary Services, NEC"},

    # Health and Medical
    "8062": {"lifeline_key": "hospitals",
             "lifeline": "Health and Medical", "lifeline_component": "Medical Care",
             "lifeline_subcomponent": "Hospitals",
             "tier": 1, "boost": 0.25, "naics_sector": "62",
             "bls_title": "General Medical and Surgical Hospitals"},
    "8063": {"lifeline_key": "hospitals",
             "lifeline": "Health and Medical", "lifeline_component": "Medical Care",
             "lifeline_subcomponent": "Hospitals",
             "tier": 2, "boost": 0.15, "naics_sector": "62",
             "bls_title": "Psychiatric Hospitals"},
    "8069": {"lifeline_key": "hospitals",
             "lifeline": "Health and Medical", "lifeline_component": "Medical Care",
             "lifeline_subcomponent": "Hospitals",
             "tier": 2, "boost": 0.15, "naics_sector": "62",
             "bls_title": "Specialty Hospitals, Except Psychiatric"},
    "5912": {"lifeline_key": "pharmacies",
             "lifeline": "Health and Medical", "lifeline_component": "Medical Care",
             "lifeline_subcomponent": "Pharmacies",
             "tier": 2, "boost": 0.15, "naics_sector": "59",
             "bls_title": "Drug Stores and Proprietary Stores"},
    "8011": {"lifeline_key": "public_health_human_services",
             "lifeline": "Health and Medical", "lifeline_component": "Public Health",
             "lifeline_subcomponent": "Public Health/Human Services",
             "tier": 2, "boost": 0.15, "naics_sector": "62",
             "bls_title": "Offices and Clinics of Doctors of Medicine"},
    "8049": {"lifeline_key": "public_health_human_services",
             "lifeline": "Health and Medical", "lifeline_component": "Public Health",
             "lifeline_subcomponent": "Public Health/Human Services",
             "tier": 3, "boost": 0.05, "naics_sector": "62",
             "bls_title": "Offices and Clinics of Other Health Practitioners"},

    # Safety and Security — Law Enforcement
    "9221": {"lifeline_key": "police_stations",
             "lifeline": "Safety and Security",
             "lifeline_component": "Law Enforcement/Security",
             "lifeline_subcomponent": "Police Stations",
             "tier": 1, "boost": 0.25, "naics_sector": "92",
             "bls_title": "Police Protection"},
    "9229": {"lifeline_key": "correctional_facilities",
             "lifeline": "Safety and Security",
             "lifeline_component": "Law Enforcement/Security",
             "lifeline_subcomponent": "Correctional Facilities",
             "tier": 2, "boost": 0.15, "naics_sector": "92",
             "bls_title": "Public Order and Safety, NEC"},

    # Safety and Security — Fire
    "9224": {"lifeline_key": "fire_stations",
             "lifeline": "Safety and Security", "lifeline_component": "Fire Service",
             "lifeline_subcomponent": "Fire Stations",
             "tier": 1, "boost": 0.25, "naics_sector": "92",
             "bls_title": "Fire Protection"},

    # Communications
    "4812": {"lifeline_key": "comms_wireless",
             "lifeline": "Communications", "lifeline_component": "Infrastructure",
             "lifeline_subcomponent": "Wireless",
             "tier": 1, "boost": 0.25, "naics_sector": "48",
             "bls_title": "Radiotelephone Communications"},
    "4813": {"lifeline_key": "comms_cable_wireline",
             "lifeline": "Communications", "lifeline_component": "Infrastructure",
             "lifeline_subcomponent": "Cable/Wireline",
             "tier": 1, "boost": 0.25, "naics_sector": "48",
             "bls_title": "Telephone Communications (Except Radiotelephone)"},
    "4899": {"lifeline_key": "comms_cable_wireline",
             "lifeline": "Communications", "lifeline_component": "Infrastructure",
             "lifeline_subcomponent": "Cable/Wireline",
             "tier": 2, "boost": 0.15, "naics_sector": "48",
             "bls_title": "Communications Services, NEC"},

    # Transportation — Aviation
    "4581": {"lifeline_key": "aviation_commercial",
             "lifeline": "Transportation", "lifeline_component": "Aviation",
             "lifeline_subcomponent": "Commercial Aviation",
             "tier": 1, "boost": 0.25, "naics_sector": "45",
             "bls_title": "Airports, Flying Fields, and Airport Terminal Services"},
    "4512": {"lifeline_key": "aviation_commercial",
             "lifeline": "Transportation", "lifeline_component": "Aviation",
             "lifeline_subcomponent": "Commercial Aviation",
             "tier": 2, "boost": 0.15, "naics_sector": "48",
             "bls_title": "Air Transportation, Scheduled"},
    "4522": {"lifeline_key": "aviation_commercial",
             "lifeline": "Transportation", "lifeline_component": "Aviation",
             "lifeline_subcomponent": "Commercial Aviation",
             "tier": 2, "boost": 0.15, "naics_sector": "48",
             "bls_title": "Air Transportation, Nonscheduled"},

    # Transportation — Truck
    "4213": {"lifeline_key": "roads",
             "lifeline": "Transportation", "lifeline_component": "Highway Roadway Motor Vehicle",
             "lifeline_subcomponent": "Roads",
             "tier": 3, "boost": 0.05, "naics_sector": "48",
             "bls_title": "Trucking, Except Local"},

    # Transportation — Rail
    "4011": {"lifeline_key": "railway_freight",
             "lifeline": "Transportation", "lifeline_component": "Railway",
             "lifeline_subcomponent": "Rail Freight",
             "tier": 1, "boost": 0.25, "naics_sector": "40",
             "bls_title": "Railroads, Line-Haul Operating"},
    "4013": {"lifeline_key": "railway_freight",
             "lifeline": "Transportation", "lifeline_component": "Railway",
             "lifeline_subcomponent": "Rail Freight",
             "tier": 2, "boost": 0.15, "naics_sector": "40",
             "bls_title": "Railroad Switching and Terminal Establishments"},
}


def lookup_code(naics_str: str | None, sic_str: str | None) -> LifelineEntry | None:
    """
    Parse pipe/comma-delimited NAICS and SIC code strings from the FRS parquet
    and return the highest-tier (lowest tier number) lifeline match.
    Returns None if no match is found in either map.
    """
    best: LifelineEntry | None = None

    def _check(code_str: str | None, code_map: dict[str, LifelineEntry]) -> None:
        nonlocal best
        if not code_str:
            return
        for raw in _split_codes(str(code_str)):
            entry = code_map.get(raw)
            if entry and (best is None or entry["tier"] < best["tier"]):
                best = entry

    _check(naics_str, NAICS_LIFELINE_MAP)
    _check(sic_str, SIC_LIFELINE_MAP)
    return best


def _split_codes(code_str: str) -> list[str]:
    """Split pipe-delimited or comma-delimited code strings; strip whitespace."""
    import re
    return [c.strip() for c in re.split(r"[|,]", code_str) if c.strip()]


def boost_for_tier(tier: int, cfg_overrides: dict | None = None) -> float:
    """
    Return the configured boost delta for the given tier.
    cfg_overrides keys: boost_tier1, boost_tier2, boost_tier3.
    """
    defaults = {1: 0.25, 2: 0.15, 3: 0.05}
    if cfg_overrides:
        key = f"boost_tier{tier}"
        if key in cfg_overrides:
            return float(cfg_overrides[key])
    return defaults.get(tier, 0.0)
