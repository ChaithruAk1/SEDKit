"""`sed ai packet`: audit exactly what text a run hands to the agents (the run files under runs/<run_id>/in/).

Lists the context files and, per batch, the packet and auxiliary files with their size and whether they still match
the sha256 recorded in manifest.json at start-run; `text=True` includes their content. Reads the run folder only (the
same files the agents read), never the database.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sed.ai.hashing import sha256_bytes
from sed.errors import PreconditionFailed
from sed.paths import Paths


def _file(run_dir: Path, rel: str, expected: str | None, text: bool) -> dict[str, Any]:
    path = run_dir / rel
    entry: dict[str, Any] = {"file": rel, "exists": path.is_file()}
    if not path.is_file():
        return entry
    data = path.read_bytes()
    content = data.decode("utf-8", errors="replace")
    entry.update(
        {
            "chars": len(content),
            "lines": content.count("\n") + (0 if content.endswith("\n") or not content else 1),
            "sha256_ok": None if expected is None else sha256_bytes(data) == expected,
        }
    )
    if text:
        entry["text"] = content
    return entry


def packet_audit(paths: Paths, run_id: str, batch: str | None = None, *, text: bool = False) -> dict[str, Any]:
    run_dir = paths.runs / run_id
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.is_file():
        raise PreconditionFailed(f"No run folder with a manifest for '{run_id}' under {paths.runs.as_posix()}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    batches = manifest.get("batches") or []
    if batch is not None:
        batches = [b for b in batches if b.get("batch") == batch]
        if not batches:
            known = ", ".join(b.get("batch", "") for b in manifest.get("batches") or [])
            raise PreconditionFailed(f"Run {run_id} has no batch '{batch}' (batches: {known})")
    context = [_file(run_dir, c["file"], c.get("sha256"), text) for c in manifest.get("context") or []]
    out_batches = []
    for b in batches:
        files = [_file(run_dir, b["packet"], b.get("packet_sha"), text)]
        files += [_file(run_dir, a["file"], a.get("sha256"), text) for a in b.get("aux") or []]
        out_batches.append({"batch": b["batch"], "items": b.get("items"), "files": files})
    every = context + [f for b in out_batches for f in b["files"]]
    return {
        "run_id": run_id,
        "skill": manifest.get("skill"),
        "profile": manifest.get("profile"),
        "context": context,
        "batches": out_batches,
        "total_chars": sum(f.get("chars", 0) for f in every),
        "all_files_match_manifest": all(f.get("exists") and f.get("sha256_ok") is not False for f in every),
        "note": "These files are the complete text the agents receive for this run (plus the skill folder).",
    }
