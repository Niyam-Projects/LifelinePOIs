<template>
  <div class="layer-filter">
    <div class="filter-header" @click="collapsed = !collapsed">
      <span>Layers</span>
      <span>{{ collapsed ? '+' : '−' }}</span>
    </div>
    <div v-if="!collapsed" class="filter-body">
      <div class="filter-actions">
        <button class="filter-action-btn" @click="selectAll">All</button>
        <button class="filter-action-btn" @click="selectNone">None</button>
      </div>
      <div class="filter-list">
        <label v-for="layer in LIFELINE_LAYERS" :key="layer.id">
          <input
            type="checkbox"
            :checked="filters[layer.id]"
            @change="toggle(layer.id)"
          />
          <span class="layer-swatch" :style="{ background: layer.color }" />
          {{ layer.label }}
        </label>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { LIFELINE_LAYERS } from '../constants.js'

const props = defineProps({
  filters: { type: Object, required: true },
})
const emit = defineEmits(['update:filters'])
const collapsed = ref(false)

function toggle(id) {
  emit('update:filters', { ...props.filters, [id]: !props.filters[id] })
}
function selectAll() {
  emit('update:filters', Object.fromEntries(LIFELINE_LAYERS.map(l => [l.id, true])))
}
function selectNone() {
  emit('update:filters', Object.fromEntries(LIFELINE_LAYERS.map(l => [l.id, false])))
}
</script>

<style scoped>
.layer-filter {
  position: absolute;
  bottom: 2.5rem;
  left: 0.75rem;
  background: #fff;
  border-radius: 6px;
  box-shadow: 0 2px 10px rgba(0,0,0,0.2);
  min-width: 160px;
  font-size: 0.82rem;
  z-index: 1000;
  overflow: hidden;
}
.filter-header {
  display: flex;
  justify-content: space-between;
  padding: 6px 10px;
  cursor: pointer;
  background: #f4f4f4;
  font-weight: 600;
  user-select: none;
}
.filter-header:hover { background: #e8e8e8; }
.filter-body { padding: 8px 10px; }
.filter-actions { display: flex; gap: 6px; margin-bottom: 6px; }
.filter-action-btn {
  flex: 1; padding: 3px 0;
  border: 1px solid #ccc; border-radius: 4px;
  background: #f9f9f9; cursor: pointer; font-size: 0.78rem;
}
.filter-action-btn:hover { background: #e8e8e8; }
.filter-list { display: flex; flex-direction: column; gap: 4px; }
.filter-list label { display: flex; align-items: center; gap: 6px; cursor: pointer; }
.layer-swatch {
  display: inline-block; width: 12px; height: 12px;
  border-radius: 50%; flex-shrink: 0;
}
</style>
