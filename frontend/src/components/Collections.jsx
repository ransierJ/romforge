import React from 'react'
import { formatSize } from './bits.jsx'

// Curate-into picker shown in the library toolbar. Choosing a collection turns
// on selection checkboxes across the views.
export function CuratePicker({ collections, curateInto, setCurateInto, onNew }) {
  return (
    <div className="curate">
      <span className="lbl">curate into</span>
      <select
        className="ctl"
        value={curateInto ?? ''}
        onChange={(e) => {
          if (e.target.value === '__new') { onNew(); return }
          setCurateInto(e.target.value ? Number(e.target.value) : null)
        }}
      >
        <option value="">— off —</option>
        {collections.map((c) => (
          <option key={c.id} value={c.id}>{c.name} ({c.game_count})</option>
        ))}
        <option value="__new">+ New collection…</option>
      </select>
    </div>
  )
}

// Header for the Collections page: name (editable), stats, actions.
export function CollectionHeader({ detail, onRename, onDelete, onExport }) {
  const [editing, setEditing] = React.useState(false)
  const [name, setName] = React.useState(detail.name)
  React.useEffect(() => { setName(detail.name) }, [detail.id, detail.name])

  const cov = detail.coverage || { complete: 0, partial: 0, missing: 0 }
  const resolved = detail.resolved || 0
  const dangling = detail.game_count - resolved

  return (
    <div className="chead">
      <div className="chead-top">
        {editing ? (
          <form className="rename" onSubmit={(e) => { e.preventDefault(); onRename(name.trim()); setEditing(false) }}>
            <input autoFocus value={name} onChange={(e) => setName(e.target.value)} />
            <button type="submit" className="btn sm">save</button>
            <button type="button" className="btn sm ghost" onClick={() => { setName(detail.name); setEditing(false) }}>cancel</button>
          </form>
        ) : (
          <h1 onClick={() => setEditing(true)} title="Click to rename">{detail.name}</h1>
        )}
        <div className="chead-actions">
          <button className="btn sm primary" onClick={onExport} disabled={!detail.game_count}>Export →</button>
          <button className="btn sm danger" onClick={onDelete}>delete</button>
        </div>
      </div>

      <div className="chead-stats">
        <div className="stat-tile">
          <div className="n tnum">{detail.game_count}</div><div className="k">games</div>
        </div>
        <div className="stat-tile">
          <div className="n tnum">{detail.systems?.length || 0}</div><div className="k">systems</div>
        </div>
        <div className="stat-tile">
          <div className="n tnum">{formatSize(detail.total_bytes)}</div><div className="k">on disk</div>
        </div>
        <div className="covtiles">
          <span className="stat complete"><span className="d" />{cov.complete} complete</span>
          <span className="stat partial"><span className="d" />{cov.partial} partial</span>
          <span className="stat missing"><span className="d" />{cov.missing} missing</span>
          {dangling > 0 && (
            <span className="stat missing" title="In the collection but the ROM isn't currently on disk">
              <span className="d" />{dangling} unresolved
            </span>
          )}
        </div>
      </div>

      {detail.systems?.length > 0 && (
        <div className="chead-systems">
          {detail.systems.map((s) => (
            <span key={s.system_id} className="chip">{s.display_name} <b className="tnum">{s.n}</b></span>
          ))}
        </div>
      )}
    </div>
  )
}
