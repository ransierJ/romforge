"""Library scanner: disk -> SQLite index.

Walks ROMS_ROOT one level deep for systems, then for each system reads the ROM
files and the Skyscraper media/ folder, matching art and metadata to each ROM by
filename stem. Pure standard library so it can run without the web stack.

Can be executed directly for a quick sanity check against a real library:

    python -m app.scanner            # scan configured ROMS_ROOT
    python -m app.scanner /path/roms # scan an explicit root
"""
from __future__ import annotations

import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

from . import config, db, platforms

# Metadata fields we lift out of each media/textual/<stem>.xml.
_TEXTUAL_FIELDS = (
    "name", "rating", "releasedate", "developer", "publisher", "genre", "players",
)


@dataclass
class ScanStats:
    systems: int = 0
    scanned: int = 0
    skipped: int = 0
    roms: int = 0
    complete: int = 0
    partial: int = 0
    missing: int = 0
    seconds: float = 0.0


def _refresh_mappings(conn) -> None:
    """Re-resolve every system's frontend ids from the current platform map,
    keyed by folder (not re-deriving the canonical id, so collections are safe).
    Runs on every scan so platform-map updates reach even skipped systems."""
    for row in conn.execute("SELECT id, folder FROM systems").fetchall():
        plat = platforms.resolve(row["folder"])
        conn.execute(
            "UPDATE systems SET display_name=?, es_id=?, batocera_id=?, retrobat_id=?, known=? "
            "WHERE id=?",
            (plat.display, plat.es, plat.batocera, plat.retrobat, int(plat.known), row["id"]),
        )


def _apply_overrides(conn) -> None:
    """Apply per-system mapping overrides on top of the built-in platform map."""
    for r in conn.execute("SELECT * FROM system_overrides"):
        sets, params = [], []
        for col in ("display_name", "es_id", "batocera_id", "retrobat_id"):
            if r[col] is not None:
                sets.append(f"{col} = ?")
                params.append(r[col])
        if sets:
            params.append(r["system_id"])
            conn.execute(f"UPDATE systems SET {', '.join(sets)} WHERE id = ?", params)


# Collapse "Bully - Scholarship Edition [RB7E54]" and
# "Bully - Scholarship Edition (USA) (En,Fr,Es)" to the same normalized key so a
# disc-folder game (named by id) can still find media (named by region).
_PAREN_BRACKET = re.compile(r"[\(\[][^\)\]]*[\)\]]")
_WS = re.compile(r"\s+")


def _normalize(stem: str) -> str:
    return _WS.sub(" ", _PAREN_BRACKET.sub("", stem)).strip().lower()


class MediaIndex:
    """Exact + normalized lookup over one media sub-folder (or merged folders)."""

    def __init__(self) -> None:
        self.exact: Dict[str, str] = {}
        self.norm: Dict[str, str] = {}

    def add_dir(self, path: Path) -> None:
        try:
            entries = os.scandir(path)
        except (FileNotFoundError, NotADirectoryError):
            return
        with entries:
            for e in entries:
                if not e.is_file():
                    continue
                stem = os.path.splitext(e.name)[0]
                self.exact.setdefault(stem.lower(), e.name)
                self.norm.setdefault(_normalize(stem), e.name)

    def get(self, stem: str) -> Optional[str]:
        hit = self.exact.get(stem.lower())
        if hit:
            return hit
        return self.norm.get(_normalize(stem))


def _index_media(media_root: Path, candidates: Iterable[str]) -> MediaIndex:
    """Merge one asset's candidate folders into a single tolerant index."""
    idx = MediaIndex()
    for folder in candidates:
        idx.add_dir(media_root / folder)
    return idx


def _parse_textual(path: Path) -> Dict[str, Optional[str]]:
    """Parse a Skyscraper textual XML into a flat dict. Tolerant of junk."""
    data: Dict[str, Optional[str]] = {f: None for f in _TEXTUAL_FIELDS}
    data["description"] = None
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return data
    for child in root:
        tag = child.tag.lower()
        text = (child.text or "").strip() or None
        if tag == "desc":
            data["description"] = text
        elif tag in data:
            data[tag] = text
    return data


def _coverage(assets: Dict[str, bool]) -> str:
    """complete = all required assets present; missing = none of them; else partial."""
    required = [assets.get(k, False) for k in config.COMPLETE_REQUIRES]
    if all(required):
        return "complete"
    if not any(required):
        return "missing"
    return "partial"


# The media sub-folders whose mtime/count we fingerprint (plural + singular
# variants, matching the scan's tolerant resolution).
_SIG_DIRS = ("covers", "cover", "screenshots", "screenshot",
             "wheels", "wheel", "marquees", "marquee", "textual")


def _dir_fingerprint(path: str) -> str:
    """A directory's mtime — a single stat, NAS-cheap. The mtime bumps whenever
    an entry is added, removed, or renamed in that directory."""
    try:
        return str(os.stat(path).st_mtime_ns)
    except OSError:
        return "-"


def _system_signature(sys_path: Path) -> str:
    """Directory-level fingerprint of a system: the ROM root + each media
    sub-folder's (mtime, entry count). ~10 readdirs per system instead of
    statting 13k files, so an incremental rescan is near-instant over a NAS.

    This catches the common changes (ROMs added/removed, new games scraped). It
    can miss an in-place overwrite of an existing file with no rename — use a
    full (forced) rescan after a bulk re-scrape to be certain.
    """
    parts = ["root:" + _dir_fingerprint(str(sys_path))]
    media = str(sys_path / "media")
    for sub in _SIG_DIRS:
        parts.append(f"{sub}:" + _dir_fingerprint(os.path.join(media, sub)))
    return "|".join(parts)


def _iter_system_dirs(root: Path) -> Iterable[os.DirEntry]:
    try:
        entries = list(os.scandir(root))
    except FileNotFoundError:
        raise FileNotFoundError(f"ROMS_ROOT does not exist: {root}")
    for e in sorted(entries, key=lambda x: x.name.lower()):
        if not e.is_dir():
            continue
        if e.name.startswith(".") or e.name in config.IGNORED_SYSTEM_DIRS:
            continue
        yield e


@dataclass
class RomEntry:
    stem: str          # media/metadata join key
    filename: str      # what the user selects (file name or disc-folder name)
    ext: str           # lowercased, no dot
    size: int


def _disc_primary(folder: Path) -> Optional[Tuple[str, int]]:
    """For a disc-style game folder, return (ext, size) of its largest ROM file."""
    best: Optional[Tuple[str, int]] = None
    try:
        for e in os.scandir(folder):
            if not e.is_file() or e.name.startswith("."):
                continue
            ext = os.path.splitext(e.name)[1].lower()
            if ext in config.NON_ROM_EXTENSIONS:
                continue
            try:
                size = e.stat().st_size
            except OSError:
                size = 0
            if best is None or size > best[1]:
                best = (ext.lstrip("."), size)
    except OSError:
        return None
    return best


def _enumerate_roms(sys_path: Path) -> Iterable[RomEntry]:
    """File-based ROMs in the system root, plus disc-style game subfolders."""
    for e in os.scandir(sys_path):
        name = e.name
        if name.startswith(".") or name in config.IGNORED_SYSTEM_DIRS:
            continue
        if e.is_file():
            stem, ext = os.path.splitext(name)
            if ext.lower() in config.NON_ROM_EXTENSIONS:
                continue
            try:
                size = e.stat().st_size
            except OSError:
                size = 0
            yield RomEntry(stem, name, ext.lstrip(".").lower(), size)
        elif e.is_dir() and name != "media":
            primary = _disc_primary(Path(e.path))
            if primary is None:
                continue  # empty / media-only dir, not a game
            ext, size = primary
            yield RomEntry(name, name, ext, size)


def _scan_system(conn, sys_dir: os.DirEntry, signature: str = "") -> tuple[int, int]:
    """Index one system folder. Returns (rom_count, complete_count)."""
    plat = platforms.resolve(sys_dir.name)
    sys_path = Path(sys_dir.path)
    media_root = sys_path / "media"

    media = {key: _index_media(media_root, cands)
             for key, cands in config.MEDIA_CANDIDATES.items()}
    textual = _index_media(media_root, ("textual",))

    conn.execute(
        "INSERT OR REPLACE INTO systems"
        "(id, folder, display_name, known, es_id, batocera_id, retrobat_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (plat.canonical, sys_dir.name, plat.display, int(plat.known),
         plat.es, plat.batocera, plat.retrobat),
    )
    conn.execute("DELETE FROM games WHERE system_id = ?", (plat.canonical,))

    rom_count = 0
    complete_count = 0
    rows = []
    for rom in _enumerate_roms(sys_path):
        cover = media["cover"].get(rom.stem)
        shot = media["screenshot"].get(rom.stem)
        wheel = media["wheel"].get(rom.stem)
        marquee = media["marquee"].get(rom.stem)
        textual_name = textual.get(rom.stem)
        has_textual = textual_name is not None

        meta = {f: None for f in _TEXTUAL_FIELDS}
        meta["description"] = None
        if has_textual:
            meta = _parse_textual(media_root / "textual" / textual_name)

        rating = None
        if meta.get("rating"):
            try:
                rating = float(meta["rating"])
            except ValueError:
                rating = None

        cov = _coverage({
            "cover": bool(cover), "screenshot": bool(shot),
            "wheel": bool(wheel), "marquee": bool(marquee),
            "textual": has_textual,
        })
        if cov == "complete":
            complete_count += 1
        rom_count += 1

        rows.append((
            plat.canonical, rom.filename, rom.stem, rom.ext, rom.size,
            meta.get("name") or rom.stem, meta.get("description"), rating,
            meta.get("releasedate"), meta.get("developer"), meta.get("publisher"),
            meta.get("genre"), meta.get("players"),
            cover, shot, wheel, marquee, int(has_textual), cov,
        ))

    conn.executemany(
        "INSERT INTO games"
        "(system_id, rom_filename, stem, ext, size_bytes,"
        " name, description, rating, releasedate, developer, publisher, genre, players,"
        " cover_file, screenshot_file, wheel_file, marquee_file, has_textual, coverage) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.execute(
        "UPDATE systems SET rom_count = ?, complete_count = ?, scan_sig = ? WHERE id = ?",
        (rom_count, complete_count, signature, plat.canonical),
    )
    return rom_count, complete_count


def scan(conn, roms_root: Path | None = None, force: bool = False) -> ScanStats:
    root = roms_root or config.ROMS_ROOT
    started = time.monotonic()
    db.init_db(conn)
    db.set_meta(conn, "scan_state", "running")

    # Drop systems that no longer exist on disk.
    present = {platforms.resolve(e.name).canonical for e in _iter_system_dirs(root)}
    existing = {r["id"] for r in conn.execute("SELECT id FROM systems")}
    for gone in existing - present:
        conn.execute("DELETE FROM systems WHERE id = ?", (gone,))

    # Existing signatures, to decide what can be skipped.
    old_sigs = {r["id"]: r["scan_sig"]
                for r in conn.execute("SELECT id, scan_sig FROM systems")}

    stats = ScanStats()
    for sys_dir in _iter_system_dirs(root):
        canonical = platforms.resolve(sys_dir.name).canonical
        sig = _system_signature(Path(sys_dir.path))
        if (not force and old_sigs.get(canonical) == sig
                and conn.execute("SELECT 1 FROM games WHERE system_id = ? LIMIT 1",
                                 (canonical,)).fetchone()):
            stats.skipped += 1
        else:
            _scan_system(conn, sys_dir, sig)
            stats.scanned += 1
        stats.systems += 1
        conn.commit()

    _refresh_mappings(conn)
    _apply_overrides(conn)

    agg = conn.execute(
        "SELECT COUNT(*) n,"
        " SUM(coverage='complete') c,"
        " SUM(coverage='partial') p,"
        " SUM(coverage='missing') m FROM games"
    ).fetchone()
    stats.roms = agg["n"] or 0
    stats.complete = agg["c"] or 0
    stats.partial = agg["p"] or 0
    stats.missing = agg["m"] or 0
    stats.seconds = round(time.monotonic() - started, 2)

    db.set_meta(conn, "scan_state", "idle")
    db.set_meta(conn, "last_scan_epoch", str(int(time.time())))
    db.set_meta(conn, "last_scan_seconds", str(stats.seconds))
    db.set_meta(conn, "last_scan_scanned", str(stats.scanned))
    db.set_meta(conn, "last_scan_skipped", str(stats.skipped))
    db.set_meta(conn, "roms_root", str(root))
    conn.commit()
    return stats


def _main(argv: list[str]) -> int:
    root = Path(argv[1]).expanduser() if len(argv) > 1 else config.ROMS_ROOT
    force = "--force" in argv[1:]
    args = [a for a in argv[1:] if not a.startswith("--")]
    if args:
        root = Path(args[0]).expanduser()
    config.ensure_dirs()
    conn = db.connect()
    print(f"Scanning: {root}" + ("  (forced full)" if force else ""))
    stats = scan(conn, root, force=force)
    print(f"  systems : {stats.systems}  (scanned {stats.scanned}, skipped {stats.skipped})")
    print(f"  roms    : {stats.roms}")
    print(f"  complete: {stats.complete}")
    print(f"  partial : {stats.partial}")
    print(f"  missing : {stats.missing}")
    print(f"  time    : {stats.seconds}s")
    print("\nPer system:")
    for r in conn.execute(
        "SELECT display_name, rom_count, complete_count, known FROM systems "
        "ORDER BY display_name"
    ):
        flag = "" if r["known"] else "  (unmapped)"
        print(f"  {r['display_name']:<34} {r['rom_count']:>5} roms"
              f"  {r['complete_count']:>5} complete{flag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
