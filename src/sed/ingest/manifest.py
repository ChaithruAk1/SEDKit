"""The synthetic inbox manifest (`_manifest.json`): which generated files each module wrote.

On the synthetic profile the loader imports only files the manifest lists (`files`, the union over all modules). Each
generator owns one section, `modules.<key>`, holding its metadata and its own `files`, so regenerating one module
never unlists or deletes another module's files. A manifest written before per-module sections is read as a single
section named `legacy`.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from sed.ingest.loader import MANIFEST

LEGACY = "legacy"


def load(inbox: Path) -> dict[str, dict[str, Any]]:
    """Module key -> section of the inbox manifest; {} when there is no readable manifest."""
    path = inbox / MANIFEST
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if "modules" in data:
        return dict(data["modules"])
    return {LEGACY: {k: v for k, v in data.items() if k != "data_class"}}


def _write(inbox: Path, sections: dict[str, dict[str, Any]]) -> None:
    path = inbox / MANIFEST
    if not sections:
        path.unlink(missing_ok=True)
        return
    files = {name: sha for section in sections.values() for name, sha in section.get("files", {}).items()}
    manifest = {"data_class": "synthetic", "files": dict(sorted(files.items())), "modules": sections}
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def clean(inbox: Path, *keys: str) -> None:
    """Delete the files the given sections listed and drop those sections; other modules' files stay."""
    sections = load(inbox)
    for key in keys:
        for name in sections.pop(key, {}).get("files", {}):
            target = inbox / name
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()
    _write(inbox, sections)


def save_section(inbox: Path, key: str, section: dict[str, Any]) -> None:
    """Write one module's section (metadata plus `files`: name -> sha256), replacing its previous section."""
    sections = load(inbox)
    sections[key] = section
    _write(inbox, sections)
