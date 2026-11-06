"""On-the-fly thumbnail generation with an on-disk cache.

A boxart wall of ~1,700 full PNGs would be brutal to ship to a browser, so media
is resized on first request and cached under CONFIG_DIR/thumbs. Full-size images
are served straight from the source when width=0 (the detail view).
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional

from PIL import Image

from . import config

# Allowed thumbnail widths — clamped to a small set so the cache can't be
# ballooned by arbitrary ?w= values.
_ALLOWED_WIDTHS = (120, 200, 320, 480)


def clamp_width(w: int) -> int:
    return min(_ALLOWED_WIDTHS, key=lambda x: abs(x - w))


def _cache_path(src: Path, width: int, mtime: float) -> Path:
    h = hashlib.sha1(f"{src}|{width}|{int(mtime)}".encode()).hexdigest()
    return config.THUMB_DIR / f"{h}.jpg"


def thumbnail(src: Path, width: int) -> Optional[Path]:
    """Return a cached JPEG thumbnail of `src` at `width`, generating if needed."""
    try:
        mtime = src.stat().st_mtime
    except OSError:
        return None

    width = clamp_width(width)
    out = _cache_path(src, width, mtime)
    if out.exists():
        return out

    try:
        with Image.open(src) as im:
            im = im.convert("RGB")
            if im.width > width:
                height = round(im.height * width / im.width)
                im = im.resize((width, height), Image.LANCZOS)
            config.THUMB_DIR.mkdir(parents=True, exist_ok=True)
            im.save(out, "JPEG", quality=82, optimize=True)
    except Exception:
        return None
    return out
