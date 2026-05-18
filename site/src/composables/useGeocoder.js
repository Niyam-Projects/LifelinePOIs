import { ref } from 'vue'
import { NOMINATIM_URL } from '../constants.js'

const results = ref([])
const loading = ref(false)
let debounceTimer = null

async function search(query) {
  if (!query || query.length < 3) { results.value = []; return }
  clearTimeout(debounceTimer)
  return new Promise((resolve) => {
    debounceTimer = setTimeout(async () => {
      loading.value = true
      try {
        const params = new URLSearchParams({
          q: query,
          format: 'geojson',
          limit: '5',
          addressdetails: '1',
        })
        const resp = await fetch(`${NOMINATIM_URL}?${params}`, {
          headers: { 'Accept-Language': 'en', 'User-Agent': 'LifelinePOI/1.0' },
        })
        const data = await resp.json()
        results.value = (data.features ?? []).map(f => ({
          ...f,
          properties: {
            ...f.properties,
            label: f.properties.display_name,
            id: f.properties.place_id,
          },
        }))
      } catch (err) {
        console.error('Geocoding error:', err)
        results.value = []
      } finally {
        loading.value = false
        resolve()
      }
    }, 350)
  })
}

function clear() { results.value = [] }

export function useGeocoder() {
  return { results, loading, search, clear }
}
