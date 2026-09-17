"""Branding kept on this machine (never in the repository): the images the dashboard shows in its sidebar.

Company material, so it lives in `<data root>\\branding\\` next to the other machine-level files:
- `logo`: the logo at the top of the sidebar (`sed branding logo <file>`);
- `watermark` and `watermark-dark`: a brand mark drawn faintly behind the sidebar's pages, one per look
  (`sed branding watermark <file> [--dark]`; the dark look falls back to `watermark` when no dark one is set);
- the title in the top strip, such as the organisation's name (`sed branding title "<text>"`, kept in `branding.json`).
They are served by `GET /api/branding/<asset>`; without them the dashboard shows the plain SED wordmark and no mark.
Only raster images are accepted (PNG, JPEG, WebP): an SVG can carry script, and the images are served from the
dashboard's own origin.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from sed.errors import ValidationFailed
from sed.paths import data_root

ASSETS = ("logo", "watermark", "watermark-dark")
MAX_BYTES = 2 * 1024 * 1024
MAX_TITLE = 80
SETTINGS_FILE = "branding.json"
MEDIA_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


def branding_root() -> Path:
    return data_root() / "branding"


def _check_asset(asset: str) -> None:
    if asset not in ASSETS:
        raise ValidationFailed(f"Unknown branding asset '{asset}' (available: {', '.join(ASSETS)})")


def find_asset(asset: str, root: Path | None = None) -> Path | None:
    """The stored file of one asset (`<asset>.<ext>`), if any."""
    _check_asset(asset)
    folder = root or branding_root()
    for suffix in MEDIA_TYPES:
        candidate = folder / f"{asset}{suffix}"
        if candidate.is_file():
            return candidate
    return None


def _kind(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


def set_asset(asset: str, file: Path, root: Path | None = None) -> dict[str, Any]:
    """Validate an image and store it as `asset`, replacing any previous file of that asset."""
    _check_asset(asset)
    if not file.is_file():
        raise ValidationFailed(f"File not found: {file}")
    size = file.stat().st_size
    if size == 0 or size > MAX_BYTES:
        raise ValidationFailed(f"The image must be between 1 byte and {MAX_BYTES // (1024 * 1024)} MB")
    suffix = file.suffix.lower()
    if suffix not in MEDIA_TYPES:
        raise ValidationFailed(f"The image must be a PNG, JPEG or WebP file, not '{suffix or 'no extension'}'")
    with file.open("rb") as handle:
        kind = _kind(handle.read(16))
    if kind is None or MEDIA_TYPES[kind] != MEDIA_TYPES[suffix]:
        raise ValidationFailed(f"{file.name} is not a valid {suffix} image")
    folder = root or branding_root()
    folder.mkdir(parents=True, exist_ok=True)
    for old in MEDIA_TYPES:
        (folder / f"{asset}{old}").unlink(missing_ok=True)
    target = folder / f"{asset}{kind}"
    shutil.copyfile(file, target)
    return {"asset": asset, "file": str(target), "bytes": size, "media_type": MEDIA_TYPES[kind]}


def clear_asset(asset: str, root: Path | None = None) -> dict[str, Any]:
    _check_asset(asset)
    folder = root or branding_root()
    removed = [str(folder / f"{asset}{s}") for s in MEDIA_TYPES if (folder / f"{asset}{s}").is_file()]
    for suffix in MEDIA_TYPES:
        (folder / f"{asset}{suffix}").unlink(missing_ok=True)
    return {"asset": asset, "removed": removed}


def title(root: Path | None = None) -> str | None:
    """The strip title set on this machine, or None."""
    path = (root or branding_root()) / SETTINGS_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    value = data.get("title") if isinstance(data, dict) else None
    return value if isinstance(value, str) and value.strip() else None


def set_title(text: str, root: Path | None = None) -> dict[str, Any]:
    """Set the strip title (plain text on one line); an empty text removes it."""
    clean = " ".join(text.split())
    if len(clean) > MAX_TITLE:
        raise ValidationFailed(f"The title may be at most {MAX_TITLE} characters")
    if any(ch in clean for ch in "<>{}"):
        raise ValidationFailed("The title must be plain text")
    folder = root or branding_root()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / SETTINGS_FILE
    if clean:
        path.write_text(json.dumps({"title": clean}, ensure_ascii=False) + "\n", encoding="utf-8")
    else:
        path.unlink(missing_ok=True)
    return {"title": clean or None}


def status(root: Path | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"branding_folder": str(root or branding_root()), "title": title(root)}
    for asset in ASSETS:
        found = find_asset(asset, root)
        out[asset] = str(found) if found else None
    return out
