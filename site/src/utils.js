/**
 * Map a confidence value [0,1] to a hex color via red→yellow→green gradient.
 */
const CONF_STOPS = [
  { t: 0.0, r: 215, g: 48,  b: 39  },
  { t: 0.5, r: 254, g: 224, b: 139 },
  { t: 1.0, r: 26,  g: 152, b: 80  },
]

function lerpChannel(a, b, t) {
  return Math.round(a + (b - a) * t)
}

export function confidenceColor(value) {
  if (value == null || isNaN(value)) return '#999999'
  const v = Math.max(0, Math.min(1, value))
  let lo = CONF_STOPS[0]
  let hi = CONF_STOPS[CONF_STOPS.length - 1]
  for (let i = 0; i < CONF_STOPS.length - 1; i++) {
    if (v <= CONF_STOPS[i + 1].t) { lo = CONF_STOPS[i]; hi = CONF_STOPS[i + 1]; break }
  }
  const span = hi.t - lo.t
  const t = span === 0 ? 0 : (v - lo.t) / span
  return `rgb(${lerpChannel(lo.r, hi.r, t)},${lerpChannel(lo.g, hi.g, t)},${lerpChannel(lo.b, hi.b, t)})`
}

export function discretizeConf(conf) {
  if (conf == null || isNaN(conf)) return 'null'
  return Math.round(Math.max(0, Math.min(1, conf)) * 20)
}
