"""Deterministic SAP profile: the ops profile plus the SAP synthetic data (scale 0.02, pinned salt, as of 2026-09-01).

Fixtures:
* `sap_profile` (session): read-only use; a copy of `ops_profile` with the SAP files imported and SAP rule findings
  refreshed. Teardown fails if a test wrote to it.
* `sap_profile_rw` (function): a private copy for tests that write (snapshots, acknowledgements).
"""

from __future__ import annotations

import shutil
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from tests.fixtures.ops_profile import AS_OF, REPO, OpsProfile, _state


@dataclass
class SapProfile:
    paths: Any
    ground_truth: Path  # ground_truth/sap
    root: Path  # the data root holding the profile (SED_DATA_ROOT for CLI calls)


def _copy_profile(src: Any, root: Path) -> Any:
    from sed import db
    from sed.paths import Paths

    dst = Paths(profile=src.profile, data_dir=root / src.profile)
    dst.ensure()
    source = sqlite3.connect(str(src.db))
    target = sqlite3.connect(str(dst.db))
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    conn = db.connect(dst.db)
    try:
        db.enable_wal(conn)
    finally:
        conn.close()
    shutil.copyfile(src.salt_file, dst.salt_file)
    if src.config.is_dir():
        shutil.copytree(src.config, dst.config, dirs_exist_ok=True)
    shutil.copytree(src.ground_truth, dst.ground_truth, dirs_exist_ok=True)
    return dst


def build_sap_profile(root: Path, ops: OpsProfile) -> SapProfile:
    from sed import db, rule_findings
    from sed.ingest.loader import ImportOptions, run_import
    from sed.modules.contract import SynthRequest
    from sed.modules.sap.synth import generate

    data_root = root / "sed-data"
    paths = _copy_profile(ops.paths, data_root)
    claude_md = root / "CLAUDE.md"
    shutil.copyfile(REPO / "CLAUDE.md", claude_md)
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("SED_DATA_ROOT", str(data_root))
        mp.setenv("SED_CLAUDE_SETTINGS_LOCAL", str(root / "settings.local.json"))
        mp.setenv("SED_CLAUDE_MD", str(claude_md))
        generate(paths, SynthRequest(seed=42, as_of=AS_OF, scale=0.02))
        result = run_import(paths, ImportOptions(inbox=True))
        if result["summary"]["errors"]:
            raise RuntimeError(f"sap_profile import failed: {result['files']}")
        conn = db.connect(paths.db)
        try:
            rule_findings.refresh(conn, paths, AS_OF, "sap")
        finally:
            conn.close()
    return SapProfile(paths=paths, ground_truth=paths.ground_truth / "sap", root=data_root)


@pytest.fixture(scope="session")
def sap_profile(ops_profile: OpsProfile, tmp_path_factory: pytest.TempPathFactory) -> Iterator[SapProfile]:
    profile = build_sap_profile(tmp_path_factory.mktemp("sap_profile"), ops_profile)
    before = _state(profile.paths)
    yield profile
    if _state(profile.paths) != before:
        pytest.fail("A test wrote to the shared session sap_profile; use sap_profile_rw for writes.")


@pytest.fixture
def sap_profile_rw(sap_profile: SapProfile, tmp_path: Path) -> SapProfile:
    root = tmp_path / "sap-rw"
    paths = _copy_profile(sap_profile.paths, root)
    return SapProfile(paths=paths, ground_truth=paths.ground_truth / "sap", root=root)
