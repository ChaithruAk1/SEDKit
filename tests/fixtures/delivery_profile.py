"""Deterministic delivery profile: the ops profile plus the delivery synthetic data (as of 2026-09-01).

Fixtures:
* `delivery_profile` (session): read-only use; a copy of `ops_profile` with the delivery files imported and delivery
  rule findings refreshed. Teardown fails if a test wrote to it.
* `delivery_profile_rw` (function): a private copy for tests that write.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from tests.fixtures.ops_profile import AS_OF, REPO, OpsProfile, _state
from tests.fixtures.sap_profile import _copy_profile


@dataclass
class DeliveryProfile:
    paths: Any
    ground_truth: Path  # ground_truth/delivery
    root: Path  # the data root holding the profile (SED_DATA_ROOT for CLI calls)


def build_delivery_profile(root: Path, ops: OpsProfile) -> DeliveryProfile:
    from sed import db, rule_findings
    from sed.ingest.loader import ImportOptions, run_import
    from sed.modules.contract import SynthRequest
    from sed.modules.delivery.synth import generate

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
            raise RuntimeError(f"delivery_profile import failed: {result['files']}")
        conn = db.connect(paths.db)
        try:
            rule_findings.refresh(conn, paths, AS_OF, "delivery")
        finally:
            conn.close()
    return DeliveryProfile(paths=paths, ground_truth=paths.ground_truth / "delivery", root=data_root)


@pytest.fixture(scope="session")
def delivery_profile(ops_profile: OpsProfile, tmp_path_factory: pytest.TempPathFactory) -> Iterator[DeliveryProfile]:
    profile = build_delivery_profile(tmp_path_factory.mktemp("delivery_profile"), ops_profile)
    before = _state(profile.paths)
    yield profile
    if _state(profile.paths) != before:
        pytest.fail("A test wrote to the shared session delivery_profile; use delivery_profile_rw for writes.")


@pytest.fixture
def delivery_profile_rw(delivery_profile: DeliveryProfile, tmp_path: Path) -> DeliveryProfile:
    root = tmp_path / "delivery-rw"
    paths = _copy_profile(delivery_profile.paths, root)
    return DeliveryProfile(paths=paths, ground_truth=paths.ground_truth / "delivery", root=root)
