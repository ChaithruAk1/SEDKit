"""Dependency direction: the core never imports module code directly (only through registry import references).

Core = kit files in src/sed/*.py, sed/ai, sed/api, sed/reports, sed/ingest (except targets.py) and the registry itself.
Forbidden from core: sed.modules.<key>..., sed.metrics, sed.analytics, sed.synth, sed.ingest.targets.

Every spelling counts: `import a.b`, `from a import b` (b a submodule), relative imports and
`importlib.import_module("...")` / `__import__("...")` with a literal name.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.conftest import REPO

SRC = REPO / "src" / "sed"
LEGACY_OPS = {"metrics.py", "analytics.py"}
FORBIDDEN_PREFIXES = ("sed.metrics", "sed.analytics", "sed.synth", "sed.ingest.targets")
REGISTRY_FILES = {"modules/__init__.py", "modules/contract.py", "modules/cli.py"}
# Specific imports legacy core files still make into ops code. Every entry is debt: remove it when the file migrates.
ALLOWLIST = {
    ("cli.py", "sed.metrics"),  # legacy top-level ops commands (metrics, attention)
    ("cli.py", "sed.analytics"),  # legacy top-level `sed analytics refresh`
}


def core_files() -> list[Path]:
    files = [p for p in SRC.glob("*.py") if p.name not in LEGACY_OPS]
    for sub in ("ai", "api", "reports", "ingest"):
        files += [p for p in (SRC / sub).rglob("*.py") if p.name != "targets.py" or sub != "ingest"]
    files += [SRC / rel for rel in REGISTRY_FILES]
    return [f for f in files if f.is_file()]


def _is_module(dotted: str) -> bool:
    path = SRC.parent.joinpath(*dotted.split("."))
    return path.with_suffix(".py").is_file() or (path / "__init__.py").is_file()


def _package_of(path: Path) -> list[str]:
    """Dotted parts of the package a file belongs to (for __init__.py, the package itself)."""
    return list(path.relative_to(SRC.parent).with_suffix("").parts)[:-1]


def imported_names(path: Path, source: str | None = None) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8") if source is None else source)
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                package = _package_of(path)
                anchor = package[: len(package) - (node.level - 1)]
                base = ".".join([*anchor, *([node.module] if node.module else [])])
            if not base:
                continue
            names.append(base)
            names += [f"{base}.{a.name}" for a in node.names if _is_module(f"{base}.{a.name}")]
        elif isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant):
            func = node.func
            called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if called in {"import_module", "__import__"} and isinstance(node.args[0].value, str):
                names.append(node.args[0].value)
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
        bad = sorted({n for n in imported_names(path) if _violations(n) and (rel, n) not in ALLOWLIST})
        if bad:
            problems.append(f"{rel}: {bad}")
    assert not problems, "\n".join(problems)


def test_allowlist_entries_still_need_it():
    stale = [(rel, name) for rel, name in ALLOWLIST if name not in imported_names(SRC / rel)]
    assert not stale, f"remove from ALLOWLIST: {stale}"


@pytest.mark.parametrize(
    "source",
    [
        "from sed.modules import ops",
        "from sed.ingest import targets",
        "from . import targets",
        "from .targets import TARGETS",
        "import importlib\nimportlib.import_module('sed.modules.ops.api')",
        "import sed.metrics",
        "from sed import analytics",
    ],
)
def test_every_import_spelling_is_seen(source):
    probe = SRC / "ingest" / "_boundary_probe.py"
    assert any(_violations(n) for n in imported_names(probe, source)), source


def test_registry_names_are_not_mistaken_for_module_packages():
    probe = SRC / "api" / "_boundary_probe.py"
    source = "from sed.modules import Module, EntityRef, contract\nfrom sed.ingest import loader"
    assert not [n for n in imported_names(probe, source) if _violations(n)]
