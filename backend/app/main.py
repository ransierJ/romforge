"""ROMForge HTTP API + static frontend host.

Read-only over the library in Phase 1: scan the disk into the index, browse it,
and serve art. Collections and export arrive in later phases.
"""
from __future__ import annotations

import mimetypes
import threading
import time
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, db, export, platforms, scanner, thumbnails
from .scanner import scan

app = FastAPI(title="ROMForge", version="0.1.0")

# A single shared connection is fine: SQLite in WAL mode handles our read-mostly,
# single-writer access pattern, and FastAPI's threadpool is modest.
config.ensure_dirs()
_conn = db.connect()
db.init_db(_conn)

# Guards the one background scan so two can't run at once.
_scan_lock = threading.Lock()
_scanning = False


# --------------------------------------------------------------------------- #
# Scan
# --------------------------------------------------------------------------- #
def _run_scan(force: bool) -> None:
    global _scanning
    try:
        conn = db.connect()  # own connection for the worker thread
        scan(conn, force=force)
        conn.close()
    finally:
        _scanning = False
        _scan_lock.release()


@app.post("/api/scan")
def start_scan(force: bool = Query(False)):
    global _scanning
    if not _scan_lock.acquire(blocking=False):
        return JSONResponse({"state": "running"}, status_code=409)
    _scanning = True
    threading.Thread(target=_run_scan, args=(force,), daemon=True).start()
    return {"state": "started", "force": force}


@app.get("/api/scan/status")
def scan_status():
    return {
        "state": "running" if _scanning else (db.get_meta(_conn, "scan_state") or "idle"),
        "last_scan_epoch": db.get_meta(_conn, "last_scan_epoch"),
        "last_scan_seconds": db.get_meta(_conn, "last_scan_seconds"),
        "roms_root": db.get_meta(_conn, "roms_root") or str(config.ROMS_ROOT),
    }


# --------------------------------------------------------------------------- #
# Systems & games
# --------------------------------------------------------------------------- #
@app.get("/api/systems")
def list_systems():
    rows = _conn.execute(
        "SELECT id, display_name, folder, known, rom_count, complete_count,"
        " es_id, batocera_id, retrobat_id FROM systems "
        "WHERE rom_count > 0 ORDER BY display_name"
    ).fetchall()
    return [dict(r) for r in rows]


class SystemOverride(BaseModel):
    display_name: Optional[str] = None
    es_id: Optional[str] = None
    batocera_id: Optional[str] = None
    retrobat_id: Optional[str] = None


@app.get("/api/systems/{sid}/mapping")
def system_mapping(sid: str):
    row = _conn.execute("SELECT * FROM systems WHERE id = ?", (sid,)).fetchone()
    if not row:
        raise HTTPException(404, "Unknown system")
    d = platforms.resolve(row["folder"])
    ov = _conn.execute("SELECT * FROM system_overrides WHERE system_id = ?", (sid,)).fetchone()
    keys = ("display_name", "es_id", "batocera_id", "retrobat_id")
    return {
        "system_id": sid, "folder": row["folder"],
        "current": {k: row[k] for k in keys},
        "default": {"display_name": d.display, "es_id": d.es,
                    "batocera_id": d.batocera, "retrobat_id": d.retrobat, "known": d.known},
        "override": {k: (ov[k] if ov else None) for k in keys},
    }


@app.patch("/api/systems/{sid}/mapping")
def patch_system_mapping(sid: str, body: SystemOverride):
    row = _conn.execute("SELECT folder FROM systems WHERE id = ?", (sid,)).fetchone()
    if not row:
        raise HTTPException(404, "Unknown system")
    d = platforms.resolve(row["folder"])

    def norm(v):
        v = v.strip() if isinstance(v, str) else v
        return v or None

    ov = {k: norm(getattr(body, k)) for k in
          ("display_name", "es_id", "batocera_id", "retrobat_id")}

    if any(ov.values()):
        _conn.execute(
            "INSERT INTO system_overrides(system_id, display_name, es_id, batocera_id, retrobat_id) "
            "VALUES (?,?,?,?,?) ON CONFLICT(system_id) DO UPDATE SET "
            "display_name=excluded.display_name, es_id=excluded.es_id, "
            "batocera_id=excluded.batocera_id, retrobat_id=excluded.retrobat_id",
            (sid, ov["display_name"], ov["es_id"], ov["batocera_id"], ov["retrobat_id"]),
        )
    else:
        _conn.execute("DELETE FROM system_overrides WHERE system_id = ?", (sid,))

    # Effective = override, else built-in default, else the canonical id.
    eff = {
        "display_name": ov["display_name"] or d.display,
        "es_id": ov["es_id"] or d.es or sid,
        "batocera_id": ov["batocera_id"] or d.batocera or sid,
        "retrobat_id": ov["retrobat_id"] or d.retrobat or sid,
    }
    _conn.execute(
        "UPDATE systems SET display_name=?, es_id=?, batocera_id=?, retrobat_id=? WHERE id=?",
        (eff["display_name"], eff["es_id"], eff["batocera_id"], eff["retrobat_id"], sid),
    )
    _conn.commit()
    return system_mapping(sid)


@app.get("/api/stats")
def stats():
    row = _conn.execute(
        "SELECT COUNT(*) roms,"
        " SUM(coverage='complete') complete,"
        " SUM(coverage='partial') partial,"
        " SUM(coverage='missing') missing FROM games"
    ).fetchone()
    systems = _conn.execute(
        "SELECT COUNT(*) n FROM systems WHERE rom_count > 0"
    ).fetchone()["n"]
    return {
        "systems": systems,
        "roms": row["roms"] or 0,
        "complete": row["complete"] or 0,
        "partial": row["partial"] or 0,
        "missing": row["missing"] or 0,
    }


_SORTS = {
    "name": "g.name COLLATE NOCASE ASC",
    "name_desc": "g.name COLLATE NOCASE DESC",
    "rating": "g.rating DESC NULLS LAST, g.name COLLATE NOCASE ASC",
    "coverage": "CASE g.coverage WHEN 'missing' THEN 0 WHEN 'partial' THEN 1 ELSE 2 END ASC, g.name COLLATE NOCASE ASC",
}


def _game_dict(r) -> dict:
    return {
        "id": r["id"],
        "system_id": r["system_id"],
        "rom_filename": r["rom_filename"],
        "name": r["name"],
        "ext": r["ext"],
        "size_bytes": r["size_bytes"],
        "rating": r["rating"],
        "releasedate": r["releasedate"],
        "developer": r["developer"],
        "publisher": r["publisher"],
        "genre": r["genre"],
        "players": r["players"],
        "coverage": r["coverage"],
        "has": {
            "cover": bool(r["cover_file"]),
            "screenshot": bool(r["screenshot_file"]),
            "wheel": bool(r["wheel_file"]),
            "marquee": bool(r["marquee_file"]),
            "metadata": bool(r["has_textual"]),
        },
    }


def _games_where(system_id: str, search: str, coverage: Optional[str]):
    """Shared WHERE builder for library listing and bulk add-by-query."""
    where = ["g.system_id = ?"]
    params: list = [system_id]
    if search and search.strip():
        where.append("(g.name LIKE ? OR g.rom_filename LIKE ?)")
        term = f"%{search.strip()}%"
        params += [term, term]
    if coverage:
        where.append("g.coverage = ?")
        params.append(coverage)
    return " AND ".join(where), params


@app.get("/api/systems/{system_id}/games")
def list_games(
    system_id: str,
    search: str = "",
    coverage: Optional[str] = Query(None, pattern="^(complete|partial|missing)$"),
    sort: str = Query("name"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    selected_for: Optional[int] = Query(None, description="collection id to flag membership"),
):
    if not _conn.execute("SELECT 1 FROM systems WHERE id = ?", (system_id,)).fetchone():
        raise HTTPException(404, "Unknown system")

    where_sql, params = _games_where(system_id, search, coverage)
    order = _SORTS.get(sort, _SORTS["name"])

    total = _conn.execute(
        f"SELECT COUNT(*) n FROM games g WHERE {where_sql}", params
    ).fetchone()["n"]

    sel_join = ""
    sel_col = "0 AS selected"
    sel_params: list = []
    if selected_for is not None:
        sel_col = "(cg.collection_id IS NOT NULL) AS selected"
        sel_join = ("LEFT JOIN collection_games cg ON cg.collection_id = ? "
                    "AND cg.system_id = g.system_id AND cg.rom_filename = g.rom_filename")
        sel_params = [selected_for]

    rows = _conn.execute(
        f"SELECT g.*, {sel_col} FROM games g {sel_join} WHERE {where_sql} "
        f"ORDER BY {order} LIMIT ? OFFSET ?",
        sel_params + params + [limit, offset],
    ).fetchall()

    out = []
    for r in rows:
        d = _game_dict(r)
        d["selected"] = bool(r["selected"])
        out.append(d)
    return {"total": total, "limit": limit, "offset": offset, "games": out}


@app.get("/api/games/{game_id}")
def game_detail(game_id: int):
    r = _conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
    if not r:
        raise HTTPException(404, "Unknown game")
    d = _game_dict(r)
    d["description"] = r["description"]
    d["stem"] = r["stem"]
    d["has"]["video"] = _video_path(r) is not None
    return d


# --------------------------------------------------------------------------- #
# Collections (Phase 2)
# --------------------------------------------------------------------------- #
class CollectionCreate(BaseModel):
    name: str
    note: Optional[str] = None


class CollectionPatch(BaseModel):
    name: Optional[str] = None
    note: Optional[str] = None


class GameRef(BaseModel):
    system_id: str
    rom_filename: str


class GamesBody(BaseModel):
    games: List[GameRef]


class QueryAddBody(BaseModel):
    system_id: str
    search: Optional[str] = ""
    coverage: Optional[str] = None


def _collection_or_404(cid: int):
    row = _conn.execute("SELECT * FROM collections WHERE id = ?", (cid,)).fetchone()
    if not row:
        raise HTTPException(404, "Unknown collection")
    return row


def _touch(cid: int) -> None:
    _conn.execute("UPDATE collections SET updated_epoch = ? WHERE id = ?",
                  (int(time.time()), cid))


def _collection_detail(cid: int) -> dict:
    c = _collection_or_404(cid)
    stats = _conn.execute(
        "SELECT COUNT(*) n,"
        " SUM(g.coverage='complete') complete,"
        " SUM(g.coverage='partial') partial,"
        " SUM(g.coverage='missing') missing,"
        " COALESCE(SUM(g.size_bytes),0) bytes "
        "FROM collection_games cg "
        "JOIN games g ON g.system_id=cg.system_id AND g.rom_filename=cg.rom_filename "
        "WHERE cg.collection_id = ?", (cid,)
    ).fetchone()
    total_members = _conn.execute(
        "SELECT COUNT(*) n FROM collection_games WHERE collection_id = ?", (cid,)
    ).fetchone()["n"]
    systems = _conn.execute(
        "SELECT cg.system_id, COALESCE(s.display_name, cg.system_id) display_name, COUNT(*) n "
        "FROM collection_games cg LEFT JOIN systems s ON s.id = cg.system_id "
        "WHERE cg.collection_id = ? GROUP BY cg.system_id ORDER BY display_name",
        (cid,)
    ).fetchall()
    return {
        "id": c["id"], "name": c["name"], "note": c["note"],
        "created_epoch": c["created_epoch"], "updated_epoch": c["updated_epoch"],
        "game_count": total_members,
        "resolved": stats["n"] or 0,
        "coverage": {
            "complete": stats["complete"] or 0,
            "partial": stats["partial"] or 0,
            "missing": stats["missing"] or 0,
        },
        "total_bytes": stats["bytes"] or 0,
        "systems": [dict(r) for r in systems],
    }


@app.get("/api/collections")
def list_collections():
    rows = _conn.execute(
        "SELECT c.*, (SELECT COUNT(*) FROM collection_games cg WHERE cg.collection_id=c.id) game_count "
        "FROM collections c ORDER BY c.updated_epoch DESC, c.name COLLATE NOCASE"
    ).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/collections")
def create_collection(body: CollectionCreate):
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(400, "Name required")
    now = int(time.time())
    cur = _conn.execute(
        "INSERT INTO collections(name, note, created_epoch, updated_epoch) VALUES (?,?,?,?)",
        (name, body.note, now, now),
    )
    _conn.commit()
    return _collection_detail(cur.lastrowid)


@app.get("/api/collections/{cid}")
def get_collection(cid: int):
    return _collection_detail(cid)


@app.patch("/api/collections/{cid}")
def patch_collection(cid: int, body: CollectionPatch):
    _collection_or_404(cid)
    sets, params = [], []
    if body.name is not None:
        n = body.name.strip()
        if not n:
            raise HTTPException(400, "Name cannot be empty")
        sets.append("name = ?"); params.append(n)
    if body.note is not None:
        sets.append("note = ?"); params.append(body.note)
    if sets:
        sets.append("updated_epoch = ?"); params.append(int(time.time()))
        _conn.execute(f"UPDATE collections SET {', '.join(sets)} WHERE id = ?", params + [cid])
        _conn.commit()
    return _collection_detail(cid)


@app.delete("/api/collections/{cid}")
def delete_collection(cid: int):
    _collection_or_404(cid)
    _conn.execute("DELETE FROM collections WHERE id = ?", (cid,))
    _conn.commit()
    return {"deleted": cid}


@app.post("/api/collections/{cid}/add")
def add_games(cid: int, body: GamesBody):
    _collection_or_404(cid)
    now = int(time.time())
    rows = [(cid, g.system_id, g.rom_filename, now) for g in body.games]
    _conn.executemany(
        "INSERT OR IGNORE INTO collection_games"
        "(collection_id, system_id, rom_filename, added_epoch) VALUES (?,?,?,?)", rows)
    _touch(cid); _conn.commit()
    return _collection_detail(cid)


@app.post("/api/collections/{cid}/remove")
def remove_games(cid: int, body: GamesBody):
    _collection_or_404(cid)
    rows = [(cid, g.system_id, g.rom_filename) for g in body.games]
    _conn.executemany(
        "DELETE FROM collection_games "
        "WHERE collection_id=? AND system_id=? AND rom_filename=?", rows)
    _touch(cid); _conn.commit()
    return _collection_detail(cid)


@app.post("/api/collections/{cid}/add-query")
def add_by_query(cid: int, body: QueryAddBody):
    """Add every game matching a system + search + coverage filter at once."""
    _collection_or_404(cid)
    if not _conn.execute("SELECT 1 FROM systems WHERE id = ?", (body.system_id,)).fetchone():
        raise HTTPException(404, "Unknown system")
    where_sql, params = _games_where(body.system_id, body.search or "", body.coverage)
    now = int(time.time())
    _conn.execute(
        "INSERT OR IGNORE INTO collection_games(collection_id, system_id, rom_filename, added_epoch) "
        f"SELECT ?, g.system_id, g.rom_filename, ? FROM games g WHERE {where_sql}",
        [cid, now] + params,
    )
    _touch(cid); _conn.commit()
    return _collection_detail(cid)


@app.get("/api/collections/{cid}/games")
def collection_games(
    cid: int,
    search: str = "",
    coverage: Optional[str] = Query(None, pattern="^(complete|partial|missing)$"),
    sort: str = Query("name"),
    system: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    _collection_or_404(cid)
    where = ["cg.collection_id = ?"]
    params: list = [cid]
    if system:
        where.append("cg.system_id = ?"); params.append(system)
    if search.strip():
        where.append("(g.name LIKE ? OR g.rom_filename LIKE ?)")
        term = f"%{search.strip()}%"; params += [term, term]
    if coverage:
        where.append("g.coverage = ?"); params.append(coverage)
    where_sql = " AND ".join(where)
    order = _SORTS.get(sort, _SORTS["name"])
    join = ("FROM collection_games cg "
            "JOIN games g ON g.system_id=cg.system_id AND g.rom_filename=cg.rom_filename")
    total = _conn.execute(
        f"SELECT COUNT(*) n {join} WHERE {where_sql}", params
    ).fetchone()["n"]
    rows = _conn.execute(
        f"SELECT g.* {join} WHERE {where_sql} ORDER BY {order} LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()
    out = []
    for r in rows:
        d = _game_dict(r)
        d["selected"] = True
        out.append(d)
    return {"total": total, "limit": limit, "offset": offset, "games": out}


# --------------------------------------------------------------------------- #
# Export (Phase 3)
# --------------------------------------------------------------------------- #
class ExportBody(BaseModel):
    target: str
    assets: Optional[List[str]] = None
    mode: str = "copy"
    dry_run: bool = False


_export_lock = threading.Lock()
_export_progress = export.Progress()


def _run_export(plan: export.Plan, mode: str) -> None:
    try:
        export.run_export(plan, _export_progress, int(time.time()), mode=mode)
    except Exception as e:  # noqa: BLE001 — surface any failure to the UI
        _export_progress.state = "error"
        _export_progress.message = str(e)
    finally:
        _export_lock.release()


@app.post("/api/collections/{cid}/export")
def start_export(cid: int, body: ExportBody):
    _collection_or_404(cid)
    if body.target not in export.TARGETS:
        raise HTTPException(400, f"target must be one of {export.TARGETS}")
    try:
        plan = export.build_plan(_conn, cid, body.target, body.assets)
    except ValueError as e:
        raise HTTPException(400, str(e))

    if body.dry_run:
        return {"dry_run": True, "plan": plan.summary()}

    if body.mode not in export.MODES:
        raise HTTPException(400, f"mode must be one of {export.MODES}")
    if not _export_lock.acquire(blocking=False):
        return JSONResponse({"state": "running"}, status_code=409)
    # Reset progress for the new job.
    global _export_progress
    _export_progress = export.Progress()
    threading.Thread(target=_run_export, args=(plan, body.mode), daemon=True).start()
    return {"dry_run": False, "state": "started", "plan": plan.summary()}


@app.get("/api/export/status")
def export_status():
    return _export_progress.as_dict()


class PresetBody(BaseModel):
    name: str
    target: str
    assets: List[str]
    mode: str = "copy"


def _preset_dict(r) -> dict:
    return {"id": r["id"], "name": r["name"], "target": r["target"],
            "assets": [a for a in (r["assets"] or "").split(",") if a], "mode": r["mode"]}


@app.get("/api/presets")
def list_presets():
    rows = _conn.execute("SELECT * FROM export_presets ORDER BY name COLLATE NOCASE").fetchall()
    return [_preset_dict(r) for r in rows]


@app.post("/api/presets")
def create_preset(body: PresetBody):
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(400, "Name required")
    if body.target not in export.TARGETS:
        raise HTTPException(400, "Bad target")
    mode = body.mode if body.mode in export.MODES else "copy"
    assets = ",".join(a for a in body.assets if a in export.ALL_ASSETS)
    cur = _conn.execute(
        "INSERT INTO export_presets(name, target, assets, mode) VALUES (?,?,?,?)",
        (name, body.target, assets, mode),
    )
    _conn.commit()
    r = _conn.execute("SELECT * FROM export_presets WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _preset_dict(r)


@app.delete("/api/presets/{pid}")
def delete_preset(pid: int):
    _conn.execute("DELETE FROM export_presets WHERE id = ?", (pid,))
    _conn.commit()
    return {"deleted": pid}


# --------------------------------------------------------------------------- #
# Media
# --------------------------------------------------------------------------- #
def _system_folder(system_id: str) -> Optional[str]:
    row = _conn.execute("SELECT folder FROM systems WHERE id = ?", (system_id,)).fetchone()
    return row["folder"] if row else None


def _media_path(game_row, asset: str) -> Optional[Path]:
    """Locate the on-disk media file for a game + asset, honoring folder variants."""
    filename = game_row[f"{asset}_file"]
    if not filename:
        return None
    folder = _system_folder(game_row["system_id"])
    if not folder:
        return None
    media_root = config.ROMS_ROOT / folder / "media"
    for sub in config.MEDIA_CANDIDATES[asset]:
        candidate = media_root / sub / filename
        if candidate.is_file():
            return candidate
    return None


def _video_path(game_row) -> Optional[Path]:
    """Resolve a game's gameplay video, matched by stem (exact, then normalized
    for id-named disc games). Videos aren't in the index, so resolve on demand."""
    folder = _system_folder(game_row["system_id"])
    if not folder:
        return None
    media_root = config.ROMS_ROOT / folder / "media"
    stem = game_row["stem"]
    for sub in config.VIDEO_CANDIDATES:
        for ext in config.VIDEO_EXTENSIONS:
            p = media_root / sub / f"{stem}{ext}"
            if p.is_file():
                return p
    # Normalized fallback (region/id mismatches) over the videos folder(s).
    idx = scanner._index_media(media_root, config.VIDEO_CANDIDATES)
    fn = idx.get(stem)
    if fn:
        for sub in config.VIDEO_CANDIDATES:
            p = media_root / sub / fn
            if p.is_file():
                return p
    return None


@app.get("/api/games/{game_id}/media/{asset}")
def game_media(
    game_id: int,
    asset: str,
    w: int = Query(0, ge=0, le=1024),
):
    if asset not in config.MEDIA_CANDIDATES:
        raise HTTPException(404, "Unknown asset")
    r = _conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
    if not r:
        raise HTTPException(404, "Unknown game")
    src = _media_path(r, asset)
    if not src:
        raise HTTPException(404, "No such media")
    if w and w > 0:
        thumb = thumbnails.thumbnail(src, w)
        if thumb:
            return FileResponse(thumb, media_type="image/jpeg")
    return FileResponse(src)


@app.get("/api/games/{game_id}/video")
def game_video(game_id: int):
    r = _conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
    if not r:
        raise HTTPException(404, "Unknown game")
    src = _video_path(r)
    if not src:
        raise HTTPException(404, "No video")
    # FileResponse honors Range requests, so the player can seek/stream.
    media_type, _ = mimetypes.guess_type(str(src))
    return FileResponse(src, media_type=media_type or "video/mp4")


# --------------------------------------------------------------------------- #
# Static frontend (built React app). Mounted last so /api wins.
# --------------------------------------------------------------------------- #
_STATIC = Path(__file__).resolve().parent.parent / "static"
if _STATIC.is_dir():
    app.mount("/", StaticFiles(directory=str(_STATIC), html=True), name="static")
