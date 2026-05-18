<template>
  <div class="hierarchy-filter">
    <select
      :value="component"
      @change="onComponent($event.target.value)"
      class="hf-select"
      title="FEMA Lifeline Component"
    >
      <option value="">Component (All)</option>
      <option v-for="c in components" :key="c" :value="c">{{ c }}</option>
    </select>

    <span class="hf-sep">›</span>

    <select
      :value="subcomponent"
      @change="onSubcomponent($event.target.value)"
      class="hf-select"
      :disabled="!component"
      title="FEMA Lifeline Subcomponent"
    >
      <option value="">Subcomponent (All)</option>
      <option v-for="s in subcomponents" :key="s" :value="s">{{ s }}</option>
    </select>

    <span class="hf-sep">›</span>

    <select
      :value="category"
      @change="onCategory($event.target.value)"
      class="hf-select"
      :disabled="!subcomponent"
      title="FEMA Lifeline Category"
    >
      <option value="">Category (All)</option>
      <option v-for="cat in categories" :key="cat" :value="cat">{{ cat }}</option>
    </select>

    <button
      v-if="component || subcomponent || category"
      class="hf-clear"
      title="Clear filters"
      @click="clearAll"
    >✕</button>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { FEMA_HIERARCHY } from '../constants.js'

const emit = defineEmits(['update:hierarchyFilter'])

const component    = ref('')
const subcomponent = ref('')
const category     = ref('')

const components = computed(() => Object.keys(FEMA_HIERARCHY))

const subcomponents = computed(() => {
  if (!component.value) return []
  return Object.keys(FEMA_HIERARCHY[component.value] ?? {})
})

const categories = computed(() => {
  if (!component.value || !subcomponent.value) return []
  return FEMA_HIERARCHY[component.value]?.[subcomponent.value] ?? []
})

function onComponent(val) {
  component.value    = val
  subcomponent.value = ''
  category.value     = ''
  emit('update:hierarchyFilter', { component: val, subcomponent: '', category: '' })
}

function onSubcomponent(val) {
  subcomponent.value = val
  category.value     = ''
  emit('update:hierarchyFilter', { component: component.value, subcomponent: val, category: '' })
}

function onCategory(val) {
  category.value = val
  emit('update:hierarchyFilter', { component: component.value, subcomponent: subcomponent.value, category: val })
}

function clearAll() {
  component.value    = ''
  subcomponent.value = ''
  category.value     = ''
  emit('update:hierarchyFilter', { component: '', subcomponent: '', category: '' })
}
</script>

<style scoped>
.hierarchy-filter {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-wrap: wrap;
  flex-shrink: 0;
}

.hf-select {
  height: 28px;
  padding: 0 6px;
  border: 1px solid #ccc;
  border-radius: 4px;
  background: #fff;
  color: #333;
  font-size: 0.78rem;
  cursor: pointer;
  min-width: 130px;
  max-width: 200px;
}

.hf-select:disabled {
  opacity: 0.45;
  cursor: default;
}

.hf-sep {
  color: #aaa;
  font-size: 0.85rem;
  user-select: none;
}

.hf-clear {
  height: 22px;
  width: 22px;
  border: 1px solid #ccc;
  border-radius: 50%;
  background: #f5f5f5;
  color: #666;
  font-size: 0.7rem;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  line-height: 1;
  flex-shrink: 0;
}

.hf-clear:hover {
  background: #e0e0e0;
  color: #333;
}

@media (max-width: 700px) {
  .hierarchy-filter { display: none; }
}
</style>
