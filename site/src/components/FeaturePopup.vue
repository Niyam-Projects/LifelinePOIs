<template>
  <div class="feature-popup" v-if="feature">
    <button class="close-btn" @click="$emit('close')">&times;</button>

    <div class="source-badge" :class="`badge--${sourceLayer}`">
      {{ layerLabel }}
    </div>

    <h3>{{ f.display_name || f.name || 'Infrastructure Feature' }}</h3>

    <div v-if="f.osm_category" class="detail-row">
      <span class="detail-label">Type</span>
      <span class="detail-value">{{ f.osm_category }}</span>
    </div>
    <div v-if="f.operator" class="detail-row">
      <span class="detail-label">Operator</span>
      <span class="detail-value">{{ f.operator }}</span>
    </div>
    <div v-if="f.voltage" class="detail-row">
      <span class="detail-label">Voltage</span>
      <span class="detail-value">{{ f.voltage }}</span>
    </div>
    <div v-if="f.capacity" class="detail-row">
      <span class="detail-label">Capacity</span>
      <span class="detail-value">{{ f.capacity }}</span>
    </div>
    <div v-if="f.ref" class="detail-row">
      <span class="detail-label">Ref</span>
      <span class="detail-value">{{ f.ref }}</span>
    </div>
    <div v-if="f.start_date" class="detail-row">
      <span class="detail-label">Start date</span>
      <span class="detail-value">{{ f.start_date }}</span>
    </div>

    <template v-if="validNum(f.confidence_score)">
      <div class="detail-row">
        <span class="detail-label">Confidence</span>
        <span class="detail-value">
          {{ (Number(f.confidence_score) * 100).toFixed(0) }}%
          <span class="conf-tier" :class="`tier--${f.confidence_tier}`">{{ f.confidence_tier }}</span>
        </span>
      </div>
      <div class="confidence-bar">
        <div class="confidence-fill" :style="confStyle(f.confidence_score)" />
      </div>
    </template>

    <div v-if="f.lifeline_component" class="detail-row">
      <span class="detail-label">FEMA Lifeline</span>
      <span class="detail-value">
        {{ f.lifeline_component }}
        <template v-if="f.lifeline_subcomponent"> › {{ f.lifeline_subcomponent }}</template>
        <template v-if="f.lifeline_category"> › {{ f.lifeline_category }}</template>
      </span>
    </div>

    <div v-if="f.wikidata" class="detail-row">
      <span class="detail-label">Wikidata</span>
      <a
        class="detail-value detail-link"
        :href="`https://www.wikidata.org/wiki/${f.wikidata}`"
        target="_blank"
        rel="noopener"
      >{{ f.wikidata }}</a>
    </div>

    <div v-if="f.lifeline_id" class="detail-row detail-row--muted">
      <span class="detail-label">ID</span>
      <span class="detail-value detail-monospace small-id">{{ f.lifeline_id }}</span>
    </div>

    <div class="source-footer">Source: {{ f.source_provenance ?? 'OSM' }}</div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { LAYER_LABELS } from '../constants.js'
import { confidenceColor } from '../utils.js'

const props = defineProps({ feature: { type: Object, default: null } })
defineEmits(['close'])

const sourceLayer = computed(() => props.feature?.get('source_layer') ?? '')
const layerLabel  = computed(() => LAYER_LABELS[sourceLayer.value] ?? sourceLayer.value)

const f = computed(() => {
  if (!props.feature) return {}
  const obj = {}
  props.feature.getKeys().forEach(k => { obj[k] = props.feature.get(k) })
  return obj
})

function validNum(v) { return v != null && !isNaN(Number(v)) }

function confStyle(conf) {
  return { width: (conf * 100) + '%', backgroundColor: confidenceColor(conf) }
}
</script>

<style scoped>
.feature-popup {
  background: #fff;
  border-radius: 8px;
  box-shadow: 0 4px 20px rgba(0,0,0,0.3);
  padding: 1rem;
  min-width: 220px;
  max-width: 300px;
  font-size: 0.82rem;
  position: relative;
}
.close-btn {
  position: absolute; top: 6px; right: 8px;
  background: none; border: none; font-size: 1.1rem;
  cursor: pointer; color: #666; line-height: 1;
}
.close-btn:hover { color: #000; }
.source-badge {
  display: inline-block; padding: 1px 7px; border-radius: 4px;
  font-size: 0.72rem; font-weight: 600; margin-bottom: 6px; color: #fff;
}
.badge--power                { background: #e15759; }
.badge--water_infrastructure { background: #4e79a7; }
.badge--telecom              { background: #9467bd; }
.badge--fuel                 { background: #f28e2b; }
.badge--safety               { background: #e377c2; }
.badge--health               { background: #2ca02c; }
.badge--transportation       { background: #8c564b; }
h3 { margin: 0 0 8px; font-size: 0.9rem; }
.detail-row { display: flex; gap: 6px; margin: 3px 0; align-items: baseline; }
.detail-row--muted { opacity: 0.65; }
.detail-label { color: #666; font-size: 0.75rem; white-space: nowrap; flex-shrink: 0; min-width: 90px; }
.detail-value { flex: 1; word-break: break-word; }
.detail-monospace { font-family: monospace; font-size: 0.78rem; }
.detail-link { color: #2563eb; text-decoration: none; }
.detail-link:hover { text-decoration: underline; }
.small-id { font-size: 0.68rem; }
.conf-tier {
  font-size: 0.7rem; padding: 1px 5px; border-radius: 3px;
  font-weight: 600; margin-left: 4px; color: #fff;
}
.tier--high   { background: #1a9850; }
.tier--medium { background: #f28e2b; }
.tier--low    { background: #d62728; }
.confidence-bar {
  height: 5px; background: #eee; border-radius: 3px;
  margin: 2px 0 5px; overflow: hidden;
}
.confidence-fill { height: 100%; border-radius: 3px; transition: width 0.2s; }
.source-footer {
  margin-top: 10px; font-size: 0.68rem; color: #999;
  border-top: 1px solid #eee; padding-top: 5px;
}
</style>
