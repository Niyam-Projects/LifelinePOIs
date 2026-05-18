<template>
  <div class="map-container">
    <div ref="mapEl" style="width: 100%; height: 100%"></div>

    <button class="geolocation-btn" title="My location" @click="handleGeolocate">
      <span class="material-symbols-outlined">my_location</span>
    </button>

    <!-- Desktop basemap switcher -->
    <div class="basemap-switcher basemap-switcher-desktop">
      <select v-model="selectedStyle" @change="switchBaseMap">
        <option v-for="s in baseMapStyles" :key="s.key" :value="s.key">{{ s.label }}</option>
      </select>
    </div>

    <!-- Mobile basemap modal trigger -->
    <button class="basemap-switcher basemap-mobile-btn" @click="basemapModalOpen = true">
      {{ baseMapStyles.find(s => s.key === selectedStyle)?.label }} ▾
    </button>
    <Teleport to="body">
      <div v-if="basemapModalOpen" class="basemap-modal-overlay" @click.self="basemapModalOpen = false">
        <div class="basemap-modal">
          <div class="basemap-modal-title">Base Map</div>
          <button
            v-for="s in baseMapStyles" :key="s.key"
            :class="['basemap-modal-option', { active: selectedStyle === s.key }]"
            @click="selectBasemap(s.key)"
          >{{ s.label }}</button>
        </div>
      </div>
    </Teleport>

    <!-- Feature popup -->
    <div ref="popupEl">
      <FeaturePopup :feature="selectedFeature" @close="closePopup" />
    </div>

    <div class="map-attribution">
      &copy;
      <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap contributors</a>,
      <a href="https://www.openmaptiles.org/" target="_blank" rel="noopener noreferrer">OpenMapTiles</a>
    </div>
  </div>
</template>

<script setup>
import { ref, shallowRef, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'
import Map from 'ol/Map'
import View from 'ol/View'
import Overlay from 'ol/Overlay'
import { fromLonLat, transformExtent } from 'ol/proj'
import { apply } from 'ol-mapbox-style'
import FeaturePopup from './FeaturePopup.vue'
import { useGeolocation } from '../composables/useGeolocation.js'
import { getLifelineLayer, getLifelineSource, setClusterMode, updateLayerFilters, wrapLifelineFeature } from '../layers/lifelineLayer.js'
import { getClusterLayer, ingestTileFeatures, addTileFeaturesToCluster, rebuildCluster, getClusterExtent } from '../layers/clusterLayer.js'
import { BASE_MAP_STYLES, INITIAL_CENTER, INITIAL_ZOOM, MIN_ZOOM, CLUSTER_ZOOM_THRESHOLD } from '../constants.js'

const props = defineProps({
  layerFilters: { type: Object, required: true },
})

const mapEl          = ref(null)
const popupEl        = ref(null)
const map            = shallowRef(null)
const popupOverlay   = shallowRef(null)
const selectedFeature  = shallowRef(null)
const selectedStyle    = ref('positron')
const basemapModalOpen = ref(false)
const baseMapStyles    = BASE_MAP_STYLES

const { locate } = useGeolocation()

defineExpose({ flyToBbox })

onMounted(async () => {
  const view = new View({
    center: fromLonLat(INITIAL_CENTER),
    zoom: INITIAL_ZOOM,
    minZoom: MIN_ZOOM,
  })

  const lifelineLyr = getLifelineLayer()
  const lifelineSource = getLifelineSource()
  const clusterLyr = getClusterLayer()

  const olMap = new Map({ target: mapEl.value, view, layers: [] })
  map.value = olMap

  document.getElementById('initial-loader')?.remove()

  // Ingest low-zoom tiles as they load; incrementally update cluster when in cluster mode
  lifelineSource.on('tileloadend', (e) => {
    const newFeatures = ingestTileFeatures(e.tile)
    if (newFeatures && clusterLyr.getVisible()) {
      addTileFeaturesToCluster(newFeatures, props.layerFilters)
    }
  })

  // Detect zoom-level threshold crossings
  let inClusterMode = null
  olMap.on('moveend', () => {
    const zoom = olMap.getView().getZoom()
    const nowCluster = zoom < CLUSTER_ZOOM_THRESHOLD
    if (nowCluster === inClusterMode) return
    inClusterMode = nowCluster
    setClusterMode(nowCluster)
    clusterLyr.setVisible(nowCluster)
    if (nowCluster) {
      // Entering cluster mode: rebuild from cached tiles with current filters
      rebuildCluster(props.layerFilters)
    }
  })

  await applyBaseStyle('positron')

  // Set initial cluster/tile mode based on starting zoom
  const initZoom = olMap.getView().getZoom()
  inClusterMode = initZoom < CLUSTER_ZOOM_THRESHOLD
  setClusterMode(inClusterMode)
  clusterLyr.setVisible(inClusterMode)

  await nextTick()
  popupOverlay.value = new Overlay({
    element: popupEl.value,
    positioning: 'bottom-center',
    offset: [0, -8],
  })
  olMap.addOverlay(popupOverlay.value)
  popupEl.value.addEventListener('pointerdown', e => e.stopPropagation())
  olMap.on('singleclick', handleClick)

  updateLayerFilters(props.layerFilters)
  handleGeolocate()
})

onBeforeUnmount(() => { if (map.value) map.value.setTarget(null) })

watch(() => props.layerFilters, (f) => {
  updateLayerFilters(f)
  // When cluster layer is visible, rebuild it with the updated source-layer filters
  const clusterLyr = getClusterLayer()
  if (clusterLyr.getVisible()) rebuildCluster(f)
}, { deep: true })

async function applyBaseStyle(styleKey) {
  const style = BASE_MAP_STYLES.find(s => s.key === styleKey)
  if (!style || !map.value) return
  const olMap = map.value
  const lifelineLyr = getLifelineLayer()
  const clusterLyr  = getClusterLayer()
  const center = olMap.getView().getCenter()
  const zoom   = olMap.getView().getZoom()
  // Remove our layers before the basemap apply() replaces all layers
  if (olMap.getLayers().getArray().includes(lifelineLyr)) olMap.removeLayer(lifelineLyr)
  if (olMap.getLayers().getArray().includes(clusterLyr))  olMap.removeLayer(clusterLyr)
  try {
    await apply(olMap, style.url)
    olMap.addLayer(lifelineLyr)
    olMap.addLayer(clusterLyr)
    olMap.getView().setCenter(center)
    olMap.getView().setZoom(zoom)
  } catch (err) {
    console.error('Failed to apply base style:', err)
    olMap.addLayer(lifelineLyr)
    olMap.addLayer(clusterLyr)
  }
}

function switchBaseMap() { applyBaseStyle(selectedStyle.value) }
function selectBasemap(key) { selectedStyle.value = key; basemapModalOpen.value = false; applyBaseStyle(key) }

function handleClick(evt) {
  const olMap = map.value
  if (!olMap) return
  const features = olMap.getFeaturesAtPixel(evt.pixel, { hitTolerance: 6 })
  if (!features || features.length === 0) { closePopup(); return }

  const raw = features[0]
  const clusterFeatures = raw.get('features')

  if (clusterFeatures) {
    // Clicked a cluster feature
    if (clusterFeatures.length === 1) {
      // Single-point cluster — show a simplified popup
      const f = clusterFeatures[0]
      selectedFeature.value = {
        get:       (k) => f.get(k),
        getKeys:   () => ['source_layer', 'display_name', 'lifeline_id', 'lifeline_component'],
        getGeometry: () => f.getGeometry(),
      }
      popupOverlay.value?.setPosition(evt.coordinate)
    } else {
      // Multi-point cluster — zoom into cluster extent
      const extent = getClusterExtent(raw)
      olMap.getView().fit(extent, {
        padding: [80, 80, 80, 80],
        duration: 400,
        maxZoom: CLUSTER_ZOOM_THRESHOLD + 1,
      })
    }
    return
  }

  // Regular VectorTile feature
  const wrapped = wrapLifelineFeature(raw)
  selectedFeature.value = wrapped
  popupOverlay.value?.setPosition(evt.coordinate)
}

function closePopup() {
  selectedFeature.value = null
  popupOverlay.value?.setPosition(undefined)
}

async function handleGeolocate() {
  try {
    const { lon, lat } = await locate()
    map.value?.getView().animate({ center: fromLonLat([lon, lat]), zoom: 12, duration: 800 })
  } catch { /* silently ignore */ }
}

function flyToBbox({ west, south, east, north }) {
  if (!map.value) return
  const extent = transformExtent([west, south, east, north], 'EPSG:4326', 'EPSG:3857')
  map.value.getView().fit(extent, { padding: [60, 60, 60, 60], duration: 600, maxZoom: 15 })
}
</script>
