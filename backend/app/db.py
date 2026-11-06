"""SQLite index: connection helper and schema.

The index is a disposable cache of what's on disk — it can always be rebuilt by
re-scanning, so the schema favors fast reads (systems list, per-system paging,
coverage filters) over normalization.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS systems (
    id            TEXT PRIMARY KEY,   -- canonical platform id (e.g. "snes")
    folder        TEXT NOT NULL,      -- source folder name under ROMS_ROOT
    display_name  TEXT NOT NULL,
    known         INTEGER NOT NULL DEFAULT 1,
    es_id         TEXT,
    batocera_id   TEXT,
    retrobat_id   TEXT,
    rom_count     INTEGER NOT NULL DEFAULT 0,
    complete_count INTEGER NOT NULL DEFAULT 0,
    scan_sig      TEXT               -- content signature for incremental rescans
);

CREATE TABLE IF NOT EXISTS games (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id     TEXT NOT NULL REFERENCES systems(id) ON DELETE CASCADE,
    rom_filename  TEXT NOT NULL,      -- e.g. "ActRaiser (USA).zip"
    stem          TEXT NOT NULL,      -- join key, e.g. "ActRaiser (USA)"
    ext           TEXT NOT NULL,
    size_bytes    INTEGER NOT NULL DEFAULT 0,

    -- metadata parsed from media/textual/<stem>.xml
    name          TEXT,
    description   TEXT,
    rating        REAL,
    releasedate   TEXT,
    developer     TEXT,
    publisher     TEXT,
    genre         TEXT,
    players       TEXT,

    -- media presence; value is the on-disk filename or NULL if absent
    cover_file      TEXT,
    screenshot_file TEXT,
    wheel_file      TEXT,
    marquee_file    TEXT,
    has_textual     INTEGER NOT NULL DEFAULT 0,

    -- derived: "complete" | "partial" | "missing"
    coverage      TEXT NOT NULL DEFAULT 'missing'
);

CREATE INDEX IF NOT EXISTS idx_games_system   ON games(system_id);
CREATE INDEX IF NOT EXISTS idx_games_coverage ON games(system_id, coverage);
CREATE INDEX IF NOT EXISTS idx_games_name     ON games(system_id, name);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- Curated collections. Membership is keyed by the stable natural key
-- (system_id, rom_filename) rather than games.id, because the scanner
-- deletes and re-inserts game rows on every rescan (ids are volatile).
-- This also means a collection survives a ROM temporarily going missing.
CREATE TABLE IF NOT EXISTS collections (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    note          TEXT,
    created_epoch INTEGER NOT NULL DEFAULT 0,
    updated_epoch INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS collection_games (
    collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    system_id     TEXT NOT NULL,
    rom_filename  TEXT NOT NULL,
    added_epoch   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (collection_id, system_id, rom_filename)
);

CREATE INDEX IF NOT EXISTS idx_cg_collection ON collection_games(collection_id);
CREATE INDEX IF NOT EXISTS idx_cg_system     ON collection_games(collection_id, system_id);

-- Per-system mapping overrides, applied after each scan so they survive the
-- scanner rewriting the systems table from the built-in platform map.
CREATE TABLE IF NOT EXISTS system_overrides (
    system_id    TEXT PRIMARY KEY,
    display_name TEXT,
    es_id        TEXT,
    batocera_id  TEXT,
    retrobat_id  TEXT
);

-- Reusable export presets (target + media selection + copy/link mode).
CREATE TABLE IF NOT EXISTS export_presets (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name    TEXT NOT NULL,
    target  TEXT NOT NULL,
    assets  TEXT NOT NULL,          -- comma-separated asset keys
    mode    TEXT NOT NULL DEFAULT 'copy'
);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    """Additive, idempotent column migrations for existing databases."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(systems)")}
    if cols and "scan_sig" not in cols:
        conn.execute("ALTER TABLE systems ADD COLUMN scan_sig TEXT")


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()


def get_meta(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()
