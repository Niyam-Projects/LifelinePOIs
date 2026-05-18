<template>
  <div class="top-bar">
    <div class="app-title">LifelinePOI Explorer</div>
    <SearchBar @fly-to="handleFlyTo" />
    <LifelineHierarchyFilter @update:hierarchyFilter="onHierarchyFilter" />
    <div class="top-bar-right">
      <a
        href="https://github.com/Niyam-Projects/LifelinePOI"
        target="_blank"
        rel="noopener noreferrer"
        class="about-link"
      >GitHub</a>
    </div>
  </div>
  <MapContainer ref="mapRef" :layer-filters="layerFilters" />
  <LayerFilter :filters="layerFilters" @update:filters="layerFilters = $event" />
</template>

<script setup>
import { ref } from 'vue'
import SearchBar from './components/SearchBar.vue'
import MapContainer from './components/MapContainer.vue'
import LayerFilter from './components/LayerFilter.vue'
import LifelineHierarchyFilter from './components/LifelineHierarchyFilter.vue'
import { LIFELINE_LAYERS } from './constants.js'
import { updateHierarchyFilter } from './layers/lifelineLayer.js'

const mapRef = ref(null)
const layerFilters = ref(
  Object.fromEntries(LIFELINE_LAYERS.map(l => [l.id, true]))
)

function handleFlyTo(bbox) {
  mapRef.value?.flyToBbox(bbox)
}

function onHierarchyFilter(filter) {
  updateHierarchyFilter(filter)
}
</script>
