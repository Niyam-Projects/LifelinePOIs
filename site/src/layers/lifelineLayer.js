import VectorTileLayer from 'ol/layer/VectorTile'
import { PMTilesVectorSource } from 'ol-pmtiles'
import { Style, Circle, Fill, Stroke } from 'ol/style'
import { LIFELINE_PMTILES_URL, LAYER_COLORS } from '../constants.js'

let lifelineLayer = null
let enabledLayers = null  // null = all on; Set<string> for filtered
let clusterModeActive = false

// Hierarchy filter — null fields mean "no filter on that tier"
let hierarchyFilter = { component: null, subcomponent: null, category: null }

const styleCache = {}

function getPointStyle(sourceLayer, radius = 5) {
  const key = `${sourceLayer}_${radius}`
  if (!styleCache[key]) {
    const color = LAYER_COLORS[sourceLayer] ?? '#888888'
    styleCache[key] = new Style({
      image: new Circle({
        radius,
        fill:   new Fill({ color: color + 'cc' }),
        stroke: new Stroke({ color: '#ffffff', width: 1.2 }),
      }),
    })
  }
  return styleCache[key]
}

function lifelineStyle(feature, resolution) {
  // In cluster mode the tile layer renders invisibly but keeps tiles loaded
  if (clusterModeActive) return null

  const sourceLayer = feature.get('source_layer')

  // Source-layer toggle filter
  if (enabledLayers !== null && !enabledLayers.has(sourceLayer)) return null

  // FEMA hierarchy filter — only applied when a tier is actively set
  if (hierarchyFilter.component !== null) {
    const fc = feature.get('lifeline_component')
    if (fc !== hierarchyFilter.component) return null
  }
  if (hierarchyFilter.subcomponent !== null) {
    const fs = feature.get('lifeline_subcomponent')
    if (fs !== hierarchyFilter.subcomponent) return null
  }
  if (hierarchyFilter.category !== null) {
    const fcat = feature.get('lifeline_category')
    if (fcat !== hierarchyFilter.category) return null
  }

  // Scale point size slightly with zoom
  const radius = resolution < 100 ? 7 : resolution < 500 ? 6 : 5
  return getPointStyle(sourceLayer, radius)
}

export function getLifelineLayer() {
  if (lifelineLayer) return lifelineLayer

  lifelineLayer = new VectorTileLayer({
    source: new PMTilesVectorSource({ url: LIFELINE_PMTILES_URL }),
    style: lifelineStyle,
    zIndex: 10,
  })
  return lifelineLayer
}

export function getLifelineSource() {
  return getLifelineLayer().getSource()
}

/** Toggle cluster mode. When active the tile layer returns null styles (invisible but still loads tiles). */
export function setClusterMode(active) {
  clusterModeActive = active
  if (lifelineLayer) lifelineLayer.changed()
}

export function updateLayerFilters(filtersObj) {
  const entries = Object.entries(filtersObj)
  const enabled = entries.filter(([, v]) => v).map(([k]) => k)
  if (enabled.length === entries.length) {
    enabledLayers = null
  } else if (enabled.length === 0) {
    enabledLayers = new Set()
  } else {
    enabledLayers = new Set(enabled)
  }
  if (lifelineLayer) lifelineLayer.changed()
}

export function updateHierarchyFilter({ component, subcomponent, category }) {
  hierarchyFilter = {
    component:    component    || null,
    subcomponent: subcomponent || null,
    category:     category     || null,
  }
  if (lifelineLayer) lifelineLayer.changed()
}

export function wrapLifelineFeature(rf) {
  const props = {
    _source:              'lifeline',
    lifeline_id:          rf.get('lifeline_id'),
    source_layer:         rf.get('source_layer'),
    display_name:         rf.get('display_name'),
    osm_category:         rf.get('osm_category'),
    lifeline_component:   rf.get('lifeline_component'),
    lifeline_subcomponent: rf.get('lifeline_subcomponent'),
    lifeline_category:    rf.get('lifeline_category'),
    confidence_score:     rf.get('confidence_score'),
    confidence_tier:      rf.get('confidence_tier'),
    source_provenance:    rf.get('source_provenance'),
    h3_index:             rf.get('h3_index'),
    // Domain attributes (present depending on layer)
    power:              rf.get('power'),
    voltage:            rf.get('voltage'),
    operator:           rf.get('operator'),
    name:               rf.get('name'),
    ref:                rf.get('ref'),
    man_made:           rf.get('man_made'),
    telecom:            rf.get('telecom'),
    industrial:         rf.get('industrial'),
    capacity:           rf.get('capacity'),
    wikidata:           rf.get('wikidata'),
    wikipedia:          rf.get('wikipedia'),
    start_date:         rf.get('start_date'),
  }
  return {
    get: (k) => props[k],
    getKeys: () => Object.keys(props),
    getGeometry: () => rf.getGeometry(),
  }
}
