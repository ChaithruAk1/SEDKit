"""Hashes used by the AI run lifecycle: packet and output digests, canonical JSON and the skill hash.

`skill_hash` identifies the exact instructions and configuration an AI run used: sha256 over the sorted
(relative path, bytes) pairs of `.claude/skills/<skill>/**` plus the canonical JSON of the handler's config inputs.
Line endings are normalised to LF first, so a checkout with CRLF conversion hashes the same as one without.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical_json(data: Any) -> str:
    """Stable JSON text (sorted keys, no whitespace variation) for hashing."""
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def skill_files(skill_dir: Path) -> list[tuple[str, bytes]]:
    """(posix relative path, LF-normalised bytes) for every file of a skill folder, sorted by path."""
    if not skill_dir.is_dir():
        return []
    out = []
    for path in skill_dir.rglob("*"):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        out.append((path.relative_to(skill_dir).as_posix(), path.read_bytes().replace(b"\r\n", b"\n")))
    return sorted(out)


def skill_hash(repo: Path, skill: str, config_inputs: dict[str, Any]) -> str:
    h = hashlib.sha256()
    for rel, data in skill_files(repo / ".claude" / "skills" / skill):
        h.update(rel.encode("utf-8"))
        h.update(b"\x00")
        h.update(len(data).to_bytes(8, "big"))
        h.update(data)
    h.update(b"\x00config\x00")
    h.update(canonical_json(config_inputs).encode("utf-8"))
    return h.hexdigest()
