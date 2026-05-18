// URL for the combined lifeline PMTiles — served locally by Vite middleware.
// For production, replace with a public HTTP URL.
export const LIFELINE_PMTILES_URL = '/tiles/lifeline_poi.pmtiles'

// Lifeline infrastructure layers (match `source_layer` values in PMTiles)
export const LIFELINE_LAYERS = [
  { id: 'power',                label: 'Energy: Power',      color: '#e15759' },  // red
  { id: 'fuel',                 label: 'Energy: Fuel',        color: '#f28e2b' },  // orange
  { id: 'water_infrastructure', label: 'Water Systems',       color: '#4e79a7' },  // blue
  { id: 'telecom',              label: 'Communications',      color: '#9467bd' },  // purple
  { id: 'safety',               label: 'Safety & Security',   color: '#e377c2' },  // pink
  { id: 'health',               label: 'Health & Medical',    color: '#2ca02c' },  // green
  { id: 'transportation',       label: 'Transportation',      color: '#8c564b' },  // brown
]

export const LAYER_COLORS = Object.fromEntries(
  LIFELINE_LAYERS.map(l => [l.id, l.color])
)
export const LAYER_LABELS = Object.fromEntries(
  LIFELINE_LAYERS.map(l => [l.id, l.label])
)

// FEMA Lifeline 3-tier hierarchy: Component → Subcomponent → [Categories]
// Derived from the project's naics_lifeline_map.py and hifld layer definitions.
export const FEMA_HIERARCHY = {
  'Energy': {
    'Power Grid': [
      'Electric Power Generation',
      'Electric Power Transmission',
      'Electric Power Distribution',
      'Natural Gas Distribution',
      'Electric Power Generation, Transmission and Distribution',
    ],
    'Fuel': [
      'Pipelines (Natural Gas)',
      'Pipelines (Crude Oil)',
      'Pipelines (Petroleum)',
      'Petroleum Refineries',
      'Fuel Storage and Terminals',
      'Oil and Gas Extraction',
    ],
  },
  'Water Systems': {
    'Potable Water Infastrucutre': [
      'Potable Water Infastructure',
    ],
    'Wastewater Management': [
      'Wastewater Systems',
    ],
  },
  'Hazardous Materials': {
    'Faculties': [
      'Solid Waste Landfill',
      'Waste Treatment and Disposal',
    ],
  },
  'Health and Medical': {
    'Medical Care': [
      'Hospitals',
      'Specialty Hospitals',
      'Emergency Medical Services',
      'Urgent Care',
      'Pharmacies',
    ],
    'Public Health': [
      'Outpatient Care Center',
    ],
    'Medical Supply Chain': [
      'Pharmaceutical Manufacturing',
    ],
  },
  'Safety and Security': {
    'Law Enforcement/Security': [
      'Police Stations',
    ],
    'Fire Service': [
      'Fire Stations',
    ],
  },
  'Communications': {
    'Infrastructure': [
      'Wireless',
      'Satellite',
      'Cable Systems and Wireline',
    ],
  },
  'Transportation': {
    'Aviation': [
      'Air Transportation',
    ],
    'Truck Transportation': [
      'Truck Transportation',
    ],
    'Railway': [
      'Rail Transportation',
    ],
  },
}

// OpenFreeMap base map styles
export const BASE_MAP_STYLES = [
  { key: 'positron',  label: 'Positron',    url: 'https://tiles.openfreemap.org/styles/positron' },
  { key: 'liberty',   label: 'Liberty',     url: 'https://tiles.openfreemap.org/styles/liberty'  },
  { key: 'dark',      label: 'Dark Matter', url: 'https://tiles.openfreemap.org/styles/dark'     },
]

// Confidence color thresholds
export const CONFIDENCE_THRESHOLDS = { low: 0.4, high: 0.75 }

// Map defaults
export const INITIAL_CENTER = [-98.5795, 39.8283]  // Geographic center of US
export const INITIAL_ZOOM   = 5
export const MIN_ZOOM       = 3

// Below this zoom level, points are replaced with clusters for performance.
// Zoom 7 is slightly more zoomed-in than a full Texas view (Texas fits ~z5-6).
export const CLUSTER_ZOOM_THRESHOLD = 7

// Nominatim geocoding (no API key required)
export const NOMINATIM_URL = 'https://nominatim.openstreetmap.org/search'
