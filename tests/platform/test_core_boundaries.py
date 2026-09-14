"""Dependency direction: the core never imports module code directly (only through registry import references).

Core = kit files in src/sed/*.py, sed/ai, sed/api, sed/reports, sed/ingest (except targets.py) and the registry itself.
Forbidden from core: sed.modules.<key>..., sed.metrics, sed.analytics, sed.synth, sed.ingest.targets.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.conftest import REPO

SRC = REPO / "src" / "sed"
LEGACY_OPS = {"metrics.py", "analytics.py"}
FORBIDDEN_PREFIXES = ("sed.metrics", "sed.analytics", "sed.synth", "sed.ingest.targets")
REGISTRY_FILES = {"modules/__init__.py", "modules/contract.py", "modules/cli.py"}
# Legacy files that still reach ops code directly. Every entry is debt: remove it when the file is migrated.
ALLOWLIST = {
    "cli.py",  # legacy top-level ops commands (metrics, attention, analytics)
    "ingest/loader.py",  # registry-driven ingest lands with ws7-ingest
    "reports/snapshot.py",  # removed in P0.5 (weekly builder moves to the ops module)
    "reports/xlsx_builder.py",  # removed in P0.5 (definitions come from the registry)
}


def core_files() -> list[Path]:
    files = [p for p in SRC.glob("*.py") if p.name not in LEGACY_OPS]
    for sub in ("ai", "api", "reports", "ingest"):
        files += [p for p in (SRC / sub).rglob("*.py") if p.name != "targets.py" or sub != "ingest"]
    files += [SRC / rel for rel in REGISTRY_FILES]
    return [f for f in files if f.is_file()]


def imported_names(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
            if node.module == "sed":
                names += [f"sed.{a.name}" for a in node.names]
    return names


def _violations(name: str) -> bool:
    if name.startswith(FORBIDDEN_PREFIXES):
        return True
    parts = name.split(".")
    return len(parts) >= 3 and parts[:2] == ["sed", "modules"] and parts[2] not in {"contract", "cli"}


def test_core_does_not_import_module_code():
    problems = []
    for path in core_files():
        rel = path.relative_to(SRC).as_posix()
        if rel in ALLOWLIST:
            continue
        bad = sorted({n for n in imported_names(path) if _violations(n)})
        if bad:
            problems.append(f"{rel}: {bad}")
    assert not problems, "\n".join(problems)


def test_allowlist_entries_still_need_it():
    stale = [rel for rel in ALLOWLIST if not any(_violations(n) for n in imported_names(SRC / rel))]
    assert not stale, f"remove from ALLOWLIST: {stale}"
