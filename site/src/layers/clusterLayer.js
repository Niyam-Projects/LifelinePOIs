import VectorLayer from 'ol/layer/Vector'
import VectorSource from 'ol/source/Vector'
import Cluster from 'ol/source/Cluster'
import Feature from 'ol/Feature'
import Point from 'ol/geom/Point'
import { createEmpty, extend as extendExtent } from 'ol/extent'
import { Style, Circle, Fill, Stroke, Text } from 'ol/style'
import { LAYER_COLORS, CLUSTER_ZOOM_THRESHOLD } from '../constants.js'

// Per-tile feature storage keyed by "z/x/y" — only tiles below zoom threshold.
// Stores ALL features (unfiltered) so filters can be changed without re-fetching.
const tileFeatureMap = new Map()

const clusterVectorSource = new VectorSource()

const clusterSource = new Cluster({
  source: clusterVectorSource,
  distance: 60,
})

// ---------------------------------------------------------------------------
// Cluster style
// ---------------------------------------------------------------------------

const styleCache = new Map()

function clusterStyle(feature) {
  const clusterFeatures = feature.get('features')
  const count = clusterFeatures.length

  if (count === 1) {
    const sl = clusterFeatures[0].get('source_layer')
    const color = LAYER_COLORS[sl] ?? '#888888'
    const cacheKey = `single_${sl}`
    if (!styleCache.has(cacheKey)) {
      styleCache.set(cacheKey, new Style({
        image: new Circle({
          radius: 6,
          fill:   new Fill({ color: color + 'cc' }),
          stroke: new Stroke({ color: '#ffffff', width: 1.5 }),
        }),
      }))
    }
    return styleCache.get(cacheKey)
  }

  // Determine dominant source layer for the cluster circle color
  const counts = {}
  for (const f of clusterFeatures) {
    const sl = f.get('source_layer') ?? 'unknown'
    counts[sl] = (counts[sl] ?? 0) + 1
  }
  const dominantLayer = Object.entries(counts).sort((a, b) => b[1] - a[1])[0][0]
  const color = LAYER_COLORS[dominantLayer] ?? '#888888'

  const radius = Math.max(12, Math.min(40, 8 + Math.sqrt(count) * 2))
  const label  = count >= 1000 ? (count / 1000).toFixed(1) + 'k' : String(count)
  const fontSize = Math.max(10, Math.min(14, radius - 2))

  // Don't cache multi-feature styles — count and color change constantly
  return [
    new Style({
      image: new Circle({
        radius,
        fill:   new Fill({ color }),
        stroke: new Stroke({ color: '#ffffff', width: 2 }),
      }),
    }),
    new Style({
      text: new Text({
        text:   label,
        fill:   new Fill({ color: '#ffffff' }),
        font:   `bold ${fontSize}px sans-serif`,
        offsetY: 0,
      }),
    }),
  ]
}

export const clusterLayer = new VectorLayer({
  source: clusterSource,
  style:  clusterStyle,
  zIndex: 11,
  visible: false,
})

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Ingest features from a VectorTile tile into the in-memory feature map.
 * Only processes tiles with zoom < CLUSTER_ZOOM_THRESHOLD.
 * Returns the new Feature[] if newly ingested, or null if skipped/duplicate.
 */
export function ingestTileFeatures(tile) {
  const tileCoord = tile.getTileCoord()
  const tileZ = tileCoord[0]
  if (tileZ >= CLUSTER_ZOOM_THRESHOLD) return null

  const key = tileCoord.join('/')
  if (tileFeatureMap.has(key)) return null

  const renderFeatures = tile.getFeatures() ?? []
  const features = renderFeatures
    .filter(rf => rf.getType() === 'Point')
    .map(rf => {
      const coords = rf.getFlatCoordinates()
      return new Feature({
        geometry:           new Point([coords[0], coords[1]]),
        source_layer:       rf.get('source_layer'),
        lifeline_id:        rf.get('lifeline_id'),
        display_name:       rf.get('display_name'),
        lifeline_component: rf.get('lifeline_component'),
      })
    })

  tileFeatureMap.set(key, features)
  return features
}

/**
 * Add a batch of freshly ingested features to the cluster source (incremental update).
 * Applies source-layer filter from layerFilters.
 */
export function addTileFeaturesToCluster(features, layerFilters) {
  if (!features?.length) return
  const filtered = applyLayerFilter(features, layerFilters)
  if (filtered.length) clusterVectorSource.addFeatures(filtered)
}

/**
 * Clear the cluster source and rebuild it from all stored tile features,
 * applying the current layerFilters.  Call when entering cluster mode or
 * when filters change while cluster mode is active.
 */
export function rebuildCluster(layerFilters) {
  const all = []
  for (const features of tileFeatureMap.values()) {
    for (const f of applyLayerFilter(features, layerFilters)) {
      all.push(f)
    }
  }
  clusterVectorSource.clear()
  if (all.length) clusterVectorSource.addFeatures(all)
}

export function getClusterLayer() { return clusterLayer }

/**
 * Given a cluster OL Feature (which has a `features` property array),
 * compute the bounding extent of all its member features.
 */
export function getClusterExtent(clusterFeature) {
  const extent = createEmpty()
  for (const f of clusterFeature.get('features') ?? []) {
    extendExtent(extent, f.getGeometry().getExtent())
  }
  return extent
}

function applyLayerFilter(features, layerFilters) {
  if (!layerFilters) return features
  return features.filter(f => layerFilters[f.get('source_layer')] !== false)
}
