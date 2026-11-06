// Thin API client. All paths are same-origin: proxied to FastAPI in dev,
// served by the same container in production.

async function get(path) {
  const res = await fetch(path)
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

async function send(path, method, body) {
  const res = await fetch(path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.status === 204 ? null : res.json()
}

export const api = {
  stats: () => get('/api/stats'),
  systems: () => get('/api/systems'),
  scanStatus: () => get('/api/scan/status'),
  startScan: (force = false) => fetch(`/api/scan${force ? '?force=true' : ''}`, { method: 'POST' }),

  games(systemId, { search = '', coverage = '', sort = 'name', limit = 100, offset = 0, selectedFor = null } = {}) {
    const p = new URLSearchParams({ sort, limit, offset })
    if (search) p.set('search', search)
    if (coverage) p.set('coverage', coverage)
    if (selectedFor != null) p.set('selected_for', selectedFor)
    return get(`/api/systems/${encodeURIComponent(systemId)}/games?${p}`)
  },

  game: (id) => get(`/api/games/${id}`),

  // ---- collections ----
  collections: () => get('/api/collections'),
  collection: (id) => get(`/api/collections/${id}`),
  createCollection: (name, note) => send('/api/collections', 'POST', { name, note }),
  patchCollection: (id, body) => send(`/api/collections/${id}`, 'PATCH', body),
  deleteCollection: (id) => send(`/api/collections/${id}`, 'DELETE'),
  addGames: (id, games) => send(`/api/collections/${id}/add`, 'POST', { games }),
  removeGames: (id, games) => send(`/api/collections/${id}/remove`, 'POST', { games }),
  addByQuery: (id, q) => send(`/api/collections/${id}/add-query`, 'POST', q),

  // ---- export ----
  exportCollection: (id, body) => send(`/api/collections/${id}/export`, 'POST', body),
  exportStatus: () => get('/api/export/status'),

  // ---- presets ----
  presets: () => get('/api/presets'),
  createPreset: (body) => send('/api/presets', 'POST', body),
  deletePreset: (id) => send(`/api/presets/${id}`, 'DELETE'),

  // ---- mappings ----
  systemMapping: (sid) => get(`/api/systems/${encodeURIComponent(sid)}/mapping`),
  patchSystemMapping: (sid, body) => send(`/api/systems/${encodeURIComponent(sid)}/mapping`, 'PATCH', body),

  collectionGames(id, { search = '', coverage = '', sort = 'name', system = '', limit = 100, offset = 0 } = {}) {
    const p = new URLSearchParams({ sort, limit, offset })
    if (search) p.set('search', search)
    if (coverage) p.set('coverage', coverage)
    if (system) p.set('system', system)
    return get(`/api/collections/${id}/games?${p}`)
  },
}

// A game's stable natural key, for add/remove payloads.
export const gameRef = (g) => ({ system_id: g.system_id, rom_filename: g.rom_filename })

// Build a media URL; w=0 (default) serves full-size, otherwise a cached thumbnail.
export function mediaUrl(gameId, asset, w = 0) {
  const q = w ? `?w=${w}` : ''
  return `/api/games/${gameId}/media/${asset}${q}`
}

// Gameplay video stream (supports range requests for seeking).
export function videoUrl(gameId) {
  return `/api/games/${gameId}/video`
}
