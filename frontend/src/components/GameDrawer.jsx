import React from 'react'
import { api, mediaUrl, videoUrl } from '../api.js'
import { CoveragePill, formatSize } from './bits.jsx'

export default function GameDrawer({ gameId, onClose }) {
  const [game, setGame] = React.useState(null)

  React.useEffect(() => {
    let alive = true
    setGame(null)
    api.game(gameId).then((g) => { if (alive) setGame(g) }).catch(() => {})
    return () => { alive = false }
  }, [gameId])

  React.useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const [videoFailed, setVideoFailed] = React.useState(false)
  React.useEffect(() => { setVideoFailed(false) }, [gameId])

  const g = game
  const showVideo = g && g.has.video && !videoFailed
  return (
    <div className="scrim" onClick={onClose}>
      <div className="drawer" onClick={(e) => e.stopPropagation()}>
        <div className="dhead">
          {showVideo ? (
            <video
              className="shot"
              src={videoUrl(g.id)}
              poster={g.has.screenshot ? mediaUrl(g.id, 'screenshot', 1024) : undefined}
              autoPlay muted loop playsInline controls
              onError={() => setVideoFailed(true)}
            />
          ) : g && g.has.screenshot ? (
            <img className="shot" src={mediaUrl(g.id, 'screenshot', 1024)} alt="" />
          ) : (
            <div className="shot" />
          )}
          <button className="close" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <div className="dbody">
          {!g ? (
            <p className="mono" style={{ color: 'var(--ink-faint)' }}>loading…</p>
          ) : (
            <>
              {g.has.cover && (
                <img className="cover" src={mediaUrl(g.id, 'cover', 320)} alt="" />
              )}
              <h2>{g.name}</h2>
              <div className="fn">{g.rom_filename}</div>
              <div style={{ marginTop: 10 }}><CoveragePill value={g.coverage} /></div>
              {g.description && <p className="desc">{g.description}</p>}
              <dl className="facts">
                {g.developer && (<><dt>Developer</dt><dd>{g.developer}</dd></>)}
                {g.publisher && (<><dt>Publisher</dt><dd>{g.publisher}</dd></>)}
                {g.genre && (<><dt>Genre</dt><dd>{g.genre}</dd></>)}
                {g.releasedate && (<><dt>Released</dt><dd>{g.releasedate}</dd></>)}
                {g.players && (<><dt>Players</dt><dd>{g.players}</dd></>)}
                {g.rating != null && (<><dt>Rating</dt><dd>{Math.round(g.rating * 100)}%</dd></>)}
                <dt>File</dt><dd>{g.ext?.toUpperCase()} · {formatSize(g.size_bytes)}</dd>
              </dl>
              <div className="assets">
                {['cover', 'screenshot', 'wheel', 'marquee', 'video'].map((a) => (
                  <span key={a} className={g.has[a] ? 'flag pa' : 'flag'}
                        style={g.has[a] ? { background: 'var(--ok-wash)', color: 'var(--ok)' } : undefined}>
                    {g.has[a] ? `✓ ${a}` : `✕ ${a}`}
                  </span>
                ))}
                <span className="flag" style={g.has.metadata ? { background: 'var(--ok-wash)', color: 'var(--ok)' } : undefined}>
                  {g.has.metadata ? '✓ metadata' : '✕ metadata'}
                </span>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
