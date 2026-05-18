import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { createReadStream, statSync, readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'

// Path to the tiles directory.
// Reads TILES_DIR from site/.env.local first; falls back to the sibling data/tiles folder.
// Create site/.env.local and set TILES_DIR=<absolute_path> to point at your generated tiles.
function loadEnvLocal() {
  const envPath = join(__dirname, '.env.local')
  try {
    const text = readFileSync(envPath, 'utf8')
    for (const line of text.split('\n')) {
      const [key, ...rest] = line.split('=')
      if (key && rest.length) process.env[key.trim()] = rest.join('=').trim()
    }
  } catch { /* file absent — ignore */ }
}
loadEnvLocal()

const TILES_DIR = process.env.TILES_DIR
  ? resolve(process.env.TILES_DIR)
  : resolve(__dirname, '../data/tiles')

/**
 * Serve local PMTiles files at /tiles/* with HTTP Range request support.
 * PMTiles reads only small byte ranges from the archive, so Range responses
 * are required for any tile to render.
 */
function tilesMiddleware(req, res, next) {
  if (!req.url.startsWith('/tiles/')) return next()

  const relPath = decodeURIComponent(req.url.slice('/tiles/'.length).split('?')[0])
  const filePath = join(TILES_DIR, relPath)

  let stat
  try { stat = statSync(filePath) } catch { return next() }

  const range = req.headers['range']
  const headers = {
    'Content-Type': 'application/octet-stream',
    'Accept-Ranges': 'bytes',
    'Access-Control-Allow-Origin': '*',
  }

  if (range) {
    const [, s, e] = range.match(/bytes=(\d+)-(\d*)/) ?? []
    if (s == null) return next()
    const start = parseInt(s, 10)
    const end   = e ? parseInt(e, 10) : stat.size - 1
    res.writeHead(206, {
      ...headers,
      'Content-Range':  `bytes ${start}-${end}/${stat.size}`,
      'Content-Length': end - start + 1,
    })
    createReadStream(filePath, { start, end }).pipe(res)
  } else {
    res.writeHead(200, { ...headers, 'Content-Length': stat.size })
    createReadStream(filePath).pipe(res)
  }
}

export default defineConfig({
  base: '/',
  plugins: [
    vue(),
    {
      name: 'local-tiles',
      configureServer(server) { server.middlewares.use(tilesMiddleware) },
    },
  ],
  server: {
    fs: { allow: ['..'] },
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('node_modules/ol-mapbox-style')) return 'ol-mapbox-style'
          if (id.includes('node_modules/ol-pmtiles')) return 'ol-pmtiles'
          if (id.includes('node_modules/ol/')) return 'ol'
          if (id.includes('node_modules/vue')) return 'vue'
        },
      },
    },
  },
})
