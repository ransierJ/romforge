import React from 'react'
import { api, gameRef } from './api.js'
import { ListView, DetailsView, BoxartView, GapsView } from './components/Views.jsx'
import { CuratePicker, CollectionHeader } from './components/Collections.jsx'
import GameDrawer from './components/GameDrawer.jsx'
import ExportDialog from './components/ExportDialog.jsx'
import MappingEditor from './components/MappingEditor.jsx'

const VIEWS = ['List', 'Details', 'Boxart', 'Gaps']
const PAGE = 100

function useDebounced(value, ms) {
  const [v, setV] = React.useState(value)
  React.useEffect(() => {
    const t = setTimeout(() => setV(value), ms)
    return () => clearTimeout(t)
  }, [value, ms])
  return v
}

const initial = new URLSearchParams(window.location.search)

export default function App() {
  const [mode, setMode] = React.useState(initial.get('mode') === 'collections' ? 'collections' : 'library')
  const [systems, setSystems] = React.useState([])
  const [active, setActive] = React.useState(initial.get('system') || null)
  const [view, setView] = React.useState(VIEWS.includes(initial.get('view')) ? initial.get('view') : 'Boxart')
  const [search, setSearch] = React.useState('')
  const [coverage, setCoverage] = React.useState('')
  const [sort, setSort] = React.useState('name')

  const [collections, setCollections] = React.useState([])
  const [curateInto, setCurateInto] = React.useState(initial.get('curate') ? Number(initial.get('curate')) : null)
  const [activeCollection, setActiveCollection] = React.useState(initial.get('collection') ? Number(initial.get('collection')) : null)
  const [collDetail, setCollDetail] = React.useState(null)

  const [games, setGames] = React.useState([])
  const [total, setTotal] = React.useState(0)
  const [offset, setOffset] = React.useState(0)
  const [loading, setLoading] = React.useState(false)
  const [openId, setOpenId] = React.useState(initial.get('game') ? Number(initial.get('game')) : null)
  const [scan, setScan] = React.useState(null)
  const [toast, setToast] = React.useState(null)
  const [reloadKey, setReloadKey] = React.useState(0)
  const [exportOpen, setExportOpen] = React.useState(initial.get('export') === '1')
  const [mapOpen, setMapOpen] = React.useState(initial.get('mapping') === '1')
  const forceReload = () => { setOffset(0); setReloadKey((k) => k + 1) }

  const dSearch = useDebounced(search, 250)

  const flash = (msg) => { setToast(msg); setTimeout(() => setToast(null), 2600) }

  const loadSystems = React.useCallback(() => {
    api.systems().then((s) => {
      setSystems(s)
      setActive((cur) => cur || (s[0] && s[0].id) || null)
    }).catch(() => {})
  }, [])
  const loadCollections = React.useCallback(() => {
    api.collections().then(setCollections).catch(() => {})
  }, [])

  React.useEffect(() => { loadSystems(); loadCollections() }, [loadSystems, loadCollections])
  React.useEffect(() => {
    const poll = () => api.scanStatus().then(setScan).catch(() => {})
    poll(); const t = setInterval(poll, 4000); return () => clearInterval(t)
  }, [])

  // Reset paging on any query change.
  React.useEffect(() => { setOffset(0) }, [mode, active, activeCollection, dSearch, coverage, sort, curateInto])

  // Fetch a page for whichever mode is active.
  React.useEffect(() => {
    let alive = true
    const opts = { search: dSearch, coverage, sort, limit: PAGE, offset }
    let req
    if (mode === 'collections') {
      if (!activeCollection) { setGames([]); setTotal(0); return }
      req = api.collectionGames(activeCollection, opts)
    } else {
      if (!active) { setGames([]); setTotal(0); return }
      req = api.games(active, { ...opts, selectedFor: curateInto })
    }
    setLoading(true)
    req.then((res) => {
      if (!alive) return
      setTotal(res.total)
      setGames((prev) => (offset === 0 ? res.games : [...prev, ...res.games]))
    }).catch(() => { if (alive) { setGames([]); setTotal(0) } })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [mode, active, activeCollection, dSearch, coverage, sort, offset, curateInto, reloadKey])

  // Collection detail for the Collections page header.
  React.useEffect(() => {
    if (mode === 'collections' && activeCollection) {
      api.collection(activeCollection).then(setCollDetail).catch(() => setCollDetail(null))
    }
  }, [mode, activeCollection])

  // Refresh after a scan completes.
  const prevState = React.useRef()
  React.useEffect(() => {
    if (prevState.current === 'running' && scan && scan.state === 'idle') { loadSystems(); setOffset(0) }
    prevState.current = scan && scan.state
  }, [scan, loadSystems])

  const activeSys = systems.find((s) => s.id === active)
  const View = { List: ListView, Details: DetailsView, Boxart: BoxartView, Gaps: GapsView }[view]
  const hasMore = games.length < total

  // ---- collection mutations ----
  const newCollection = async () => {
    const name = window.prompt('Name this collection (e.g. "RG35XX", "Living room")')
    if (!name || !name.trim()) return
    const c = await api.createCollection(name.trim())
    await loadCollections()
    setCurateInto(c.id)
    flash(`Created “${c.name}” — now curating into it`)
  }

  const toggleMembership = async (g) => {
    const on = g.selected
    setGames((gs) => gs.map((x) => (x.id === g.id ? { ...x, selected: !on } : x)))
    try {
      if (on) await api.removeGames(curateInto, [gameRef(g)])
      else await api.addGames(curateInto, [gameRef(g)])
      loadCollections()
    } catch {
      setGames((gs) => gs.map((x) => (x.id === g.id ? { ...x, selected: on } : x))) // revert
    }
  }

  const removeFromCollection = async (g) => {
    setGames((gs) => gs.filter((x) => x.id !== g.id))
    setTotal((t) => t - 1)
    try {
      const d = await api.removeGames(activeCollection, [gameRef(g)])
      setCollDetail(d); loadCollections()
    } catch { /* leave as is; next load corrects */ }
  }

  const bulkAddFiltered = async () => {
    const label = collections.find((c) => c.id === curateInto)?.name || 'collection'
    const n = total
    const d = await api.addByQuery(curateInto, { system_id: active, search: dSearch, coverage: coverage || null })
    forceReload()          // re-fetch to light up the checkboxes
    loadCollections()
    flash(`Added ${n} to “${label}” — ${d.game_count} games total`)
  }

  const renameCollection = async (name) => {
    if (!name) return
    const d = await api.patchCollection(activeCollection, { name }); setCollDetail(d); loadCollections()
  }
  const deleteCollection = async () => {
    if (!window.confirm(`Delete “${collDetail.name}”? The ROMs are untouched — only the collection is removed.`)) return
    await api.deleteCollection(activeCollection)
    setActiveCollection(null); setCollDetail(null); loadCollections(); setMode('library')
  }

  const sel = mode === 'collections'
    ? { active: true, isOn: () => true, toggle: removeFromCollection }
    : (curateInto != null
        ? { active: true, isOn: (g) => !!g.selected, toggle: toggleMembership }
        : null)

  const openGame = (g) => setOpenId(g.id)

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <span className="mark">🕹</span><b>ROMFORGE</b><span className="ver">v0.4</span>
        </div>
        <div className="tabs">
          <button className={mode === 'library' ? 'on' : ''} onClick={() => setMode('library')}>Systems</button>
          <button className={mode === 'collections' ? 'on' : ''} onClick={() => setMode('collections')}>
            Collections{collections.length ? ` (${collections.length})` : ''}
          </button>
        </div>

        {mode === 'library' ? (
          <nav className="sys-list">
            {systems.map((s) => {
              const pct = s.rom_count ? (100 * s.complete_count / s.rom_count) : 0
              return (
                <button key={s.id} className={`sys${s.id === active ? ' active' : ''}`} onClick={() => setActive(s.id)}>
                  <span className="row1"><span className="nm">{s.display_name}</span><span className="ct tnum">{s.rom_count}</span></span>
                  <span className="covbar"><i className="c" style={{ width: `${pct}%` }} /><i className="m" style={{ width: `${100 - pct}%` }} /></span>
                </button>
              )
            })}
            {systems.length === 0 && <p className="side-empty">No systems indexed. Run a scan →</p>}
          </nav>
        ) : (
          <nav className="sys-list">
            {collections.map((c) => (
              <button key={c.id} className={`sys${c.id === activeCollection ? ' active' : ''}`} onClick={() => setActiveCollection(c.id)}>
                <span className="row1"><span className="nm">{c.name}</span><span className="ct tnum">{c.game_count}</span></span>
              </button>
            ))}
            <button className="newcoll" onClick={newCollection}>+ New collection</button>
            {collections.length === 0 && <p className="side-empty">No collections yet.</p>}
          </nav>
        )}

        <div className="foot">
          <span>{scan ? scan.state : '…'}</span>
          <button className="rescan" onClick={async () => { await api.startScan(false); setScan((s) => ({ ...(s || {}), state: 'running' })) }}
                  disabled={scan && scan.state === 'running'} title="Incremental — only re-reads changed systems">
            {scan && scan.state === 'running' ? 'scanning…' : 'rescan'}
          </button>
          <button className="rescan full" onClick={async () => { await api.startScan(true); setScan((s) => ({ ...(s || {}), state: 'running' })) }}
                  disabled={scan && scan.state === 'running'} title="Full re-scan — re-reads every system (use after a bulk re-scrape)">full</button>
        </div>
      </aside>

      <main className="main">
        {mode === 'collections' && collDetail && (
          <CollectionHeader detail={collDetail} onRename={renameCollection} onDelete={deleteCollection} onExport={() => setExportOpen(true)} />
        )}

        <div className="toolbar">
          {mode === 'library' && <h1>{activeSys ? activeSys.display_name : 'ROMForge'}</h1>}
          {mode === 'collections' && <h1 className="sub-h1">{collDetail ? 'Games' : 'Select a collection'}</h1>}
          <span className="count tnum">{total}{(search || coverage) && mode === 'library' ? ` / ${activeSys ? activeSys.rom_count : 0}` : ''} games</span>

          <div className="search">
            <span className="ic">⌕</span>
            <input placeholder="search title…" value={search} onChange={(e) => setSearch(e.target.value)} />
          </div>
          <select className="ctl" value={coverage} onChange={(e) => setCoverage(e.target.value)}>
            <option value="">all coverage</option>
            <option value="complete">complete</option>
            <option value="partial">partial</option>
            <option value="missing">missing</option>
          </select>
          <select className="ctl" value={sort} onChange={(e) => setSort(e.target.value)}>
            <option value="name">A–Z</option>
            <option value="name_desc">Z–A</option>
            <option value="rating">top rated</option>
            <option value="coverage">least complete</option>
          </select>
          <div className="views">
            {VIEWS.map((v) => <button key={v} className={v === view ? 'on' : ''} onClick={() => setView(v)}>{v}</button>)}
          </div>

          {mode === 'library' && activeSys && (
            <button className="btn sm ghost map-btn" onClick={() => setMapOpen(true)} title="Edit this system's frontend mapping">⚙ mapping</button>
          )}
          {mode === 'library' && (
            <CuratePicker collections={collections} curateInto={curateInto} setCurateInto={setCurateInto} onNew={newCollection} />
          )}
          {mode === 'library' && curateInto != null && total > 0 && (
            <button className="btn add-all" onClick={bulkAddFiltered}>+ Add all {total}</button>
          )}
        </div>

        <div className="content">
          {games.length === 0 && !loading ? (
            <div className="empty">
              {mode === 'collections' && !activeCollection ? 'Pick a collection on the left, or create one.'
                : mode === 'collections' ? 'This collection is empty. Switch to Systems and curate games into it.'
                : 'No games match.'}
            </div>
          ) : (
            <>
              <View games={games} onOpen={openGame} sel={sel} />
              {hasMore && (
                <div className="more">
                  <button onClick={() => setOffset(offset + PAGE)} disabled={loading}>
                    {loading ? 'loading…' : `load more (${games.length} / ${total})`}
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      </main>

      {exportOpen && collDetail && (
        <ExportDialog collectionId={activeCollection} detail={collDetail} onClose={() => setExportOpen(false)} />
      )}
      {mapOpen && active && (
        <MappingEditor systemId={active} onClose={() => setMapOpen(false)} onSaved={loadSystems} />
      )}
      {openId != null && <GameDrawer gameId={openId} onClose={() => setOpenId(null)} />}
      {toast && <div className="banner">{toast}</div>}
      {scan && scan.state === 'running' && <div className="banner">Scanning library…</div>}
    </div>
  )
}
