"""Runtime configuration, driven entirely by environment variables.

Nothing is hard-coded to a single machine: point ROMFORGE_ROMS_ROOT at any
library. Defaults are chosen so the app is also runnable straight from a dev
checkout against the real NAS mount.
"""
from __future__ import annotations

import os
from pathlib import Path


def _path(env: str, default: str) -> Path:
    return Path(os.environ.get(env, default)).expanduser()


# Source library — mounted read-only in Docker. Never written to.
ROMS_ROOT: Path = _path("ROMFORGE_ROMS_ROOT", "/media/jean/Plex3/ROMs")

# Where forged collections are written (Phase 3). Created on demand.
OUTPUT_ROOT: Path = _path("ROMFORGE_OUTPUT_ROOT", str(Path.cwd() / "output"))

# Persistent app state: the SQLite index, the thumbnail cache, settings.
CONFIG_DIR: Path = _path("ROMFORGE_CONFIG_DIR", str(Path.cwd() / "config"))

DB_PATH: Path = CONFIG_DIR / "romforge.db"
THUMB_DIR: Path = CONFIG_DIR / "thumbs"

# Folders directly under ROMS_ROOT that are never treated as a system.
IGNORED_SYSTEM_DIRS = {"Bios", "BIOS", "bios", "#recycle", "@eaDir"}

# File extensions under a system folder that are never treated as a ROM.
# ".part" is an incomplete download, not a game.
NON_ROM_EXTENSIONS = {
    ".txt", ".md", ".dat", ".xml", ".nfo", ".db", ".ini", ".json",
    ".log", ".bak", ".sav", ".srm", ".state", ".cfg", ".part", ".crdownload",
}

# Image extensions Skyscraper may emit for media, in preference order.
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")

# Internal asset key -> candidate media sub-folder names, in preference order.
# Skyscraper's folder naming varies across systems (screenshot vs screenshots,
# wheel vs wheels), so each asset resolves against a list, plural first.
# textual/ holds per-game metadata XML and is handled separately.
MEDIA_CANDIDATES = {
    "cover": ("covers", "cover"),
    "screenshot": ("screenshots", "screenshot"),
    "wheel": ("wheels", "wheel"),
    "marquee": ("marquees", "marquee"),
}

# Gameplay videos: folder candidates + recognized extensions. Resolved on
# demand (not part of the coverage index) and streamed to the detail view.
VIDEO_CANDIDATES = ("videos", "video")
VIDEO_EXTENSIONS = (".mp4", ".webm", ".m4v", ".mkv", ".avi", ".mov")

# Which assets must be present for a game to count as "complete". Wheels and
# marquees are tracked and shown as gap flags, but aren't required here —
# requiring all four misrepresents non-arcade systems. Tunable later.
COMPLETE_REQUIRES = ("cover", "screenshot", "textual")

HTTP_PORT: int = int(os.environ.get("ROMFORGE_PORT", "8096"))


def ensure_dirs() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    THUMB_DIR.mkdir(parents=True, exist_ok=True)
