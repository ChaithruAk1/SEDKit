"""Fixtures for the registry-driven ingest engine tests: fresh profiles, a CSV writer and a synthetic inbox."""

from __future__ import annotations

import csv
import shutil
from collections.abc import Callable
from datetime import date
from pathlib import Path

import pytest

from tests.conftest import REPO

CsvWriter = Callable[[Path, list[str], list[list[object]]], Path]


@pytest.fixture
def fresh_profile(data_root: Path):
    """An initialised, empty synthetic profile under the test's own DATA_ROOT."""
    from sed import bootstrap
    from sed.paths import get_paths

    paths = get_paths("synthetic")
    bootstrap.init_profile(paths, new_salt=True, write_claude_settings=False)
    return paths


@pytest.fixture
def write_csv() -> CsvWriter:
    def _write(path: Path, header: list[str], rows: list[list[object]]) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(header)
            writer.writerows(rows)
        return path

    return _write


@pytest.fixture(scope="module")
def synthetic_inbox(tmp_path_factory: pytest.TempPathFactory):
    """A profile whose inbox holds the seed-42, scale-0.02 synthetic exports (generated, not imported)."""
    from sed import bootstrap
    from sed.paths import get_paths
    from sed.synth.generate import SynthOptions, generate

    root = tmp_path_factory.mktemp("ingest_inbox")
    claude_md = root / "CLAUDE.md"
    shutil.copyfile(REPO / "CLAUDE.md", claude_md)
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("SED_DATA_ROOT", str(root / "sed-data"))
        mp.setenv("SED_CLAUDE_SETTINGS_LOCAL", str(root / "settings.local.json"))
        mp.setenv("SED_CLAUDE_MD", str(claude_md))
        mp.delenv("SED_PROFILE", raising=False)
        paths = get_paths("synthetic")
        bootstrap.init_profile(paths, new_salt=True, write_claude_settings=False)
        generate(paths, SynthOptions(seed=42, as_of=date(2026, 9, 1), scale=0.02))
    return paths
