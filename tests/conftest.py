from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every test gets its own DATA_ROOT, settings.local.json and CLAUDE.md copy.

    Tests must never touch the real %LOCALAPPDATA%\\amkit profiles or rewrite tracked repo files.
    """
    root = tmp_path / "amkit-data"
    monkeypatch.setenv("AMKIT_DATA_ROOT", str(root))
    monkeypatch.setenv("AMKIT_CLAUDE_SETTINGS_LOCAL", str(tmp_path / "settings.local.json"))
    claude_md = tmp_path / "CLAUDE.md"
    shutil.copyfile(REPO / "CLAUDE.md", claude_md)
    monkeypatch.setenv("AMKIT_CLAUDE_MD", str(claude_md))
    monkeypatch.delenv("AMKIT_PROFILE", raising=False)
    monkeypatch.delenv("AMKIT_GUARD_DENYLIST", raising=False)
    return root


@pytest.fixture
def data_root(_isolate: Path) -> Path:
    return _isolate


@pytest.fixture
def repo() -> Path:
    return REPO


def load_script(name: str):
    """Import a module from scripts/ (not a package)."""
    path = REPO / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"scripts_{name}", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
