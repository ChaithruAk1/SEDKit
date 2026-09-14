"""The generic ingest engine knows no module: no ops imports, no ops table or target names.

loader.py and resolve.py (plus the generic target.py and hooks.py) reach module targets and hooks only through the
registry, so loader.py can leave the core-boundary allowlist.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from sed import modules
from tests.conftest import REPO

INGEST = REPO / "src" / "sed" / "ingest"
GENERIC = ("loader.py", "resolve.py", "target.py", "hooks.py")
FORBIDDEN_IMPORTS = ("sed.ingest.targets", "sed.modules.ops")
FORBIDDEN_NAMES = ("assignment_group", "cost_line", "doc_page", "TARGET_ORDER", "KIND_ORDER")


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{a.name}" for a in node.names)
    return names


def _ops_only_names() -> set[str]:
    """Tables the ops module declares plus its target names that are not shared portfolio tables."""
    from sed.ingest.targets import TARGETS

    ops = modules.get("ops")
    return (set(ops.tables) | set(TARGETS)) - set(modules.PORTFOLIO_TABLES) | set(FORBIDDEN_NAMES)


@pytest.mark.parametrize("name", GENERIC)
def test_generic_ingest_files_do_not_import_module_code(name: str):
    bad = sorted(n for n in _imports(INGEST / name) if n.startswith(FORBIDDEN_IMPORTS))
    assert not bad, f"{name} imports {bad}"


@pytest.mark.parametrize("name", GENERIC)
def test_generic_ingest_files_contain_no_ops_names(name: str):
    text = (INGEST / name).read_text(encoding="utf-8")
    found = sorted(word for word in _ops_only_names() if re.search(rf"\b{re.escape(word)}\b", text))
    assert not found, f"{name} mentions {found}"


def test_loader_and_resolver_use_the_registry():
    for name in ("loader.py", "resolve.py"):
        assert "sed.modules" in _imports(INGEST / name), name


def test_only_the_manifest_refers_to_the_legacy_targets_module():
    ops = modules.get("ops")
    assert ops.ingest_targets == "sed.ingest.targets:TARGETS"
    src = REPO / "src" / "sed"
    core = [p for p in src.glob("*.py") if p.name not in {"metrics.py", "analytics.py"}]
    for sub in ("ai", "api", "reports", "ingest"):
        core += [p for p in (src / sub).rglob("*.py") if not (sub == "ingest" and p.name == "targets.py")]
    core += [src / "modules" / f for f in ("__init__.py", "contract.py", "cli.py")]
    referring = sorted(p.relative_to(src).as_posix() for p in core if "sed.ingest.targets" in p.read_text("utf-8"))
    assert referring == [], referring
