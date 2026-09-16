"""The synthetic inbox manifest keeps one section per module: regenerating one module never unlists or deletes another
module's files, and the loader reads the union."""

from __future__ import annotations

import json
from pathlib import Path

from sed.ingest import manifest
from sed.ingest.loader import MANIFEST


def _touch(inbox: Path, name: str) -> str:
    (inbox / name).write_text("x", encoding="utf-8")
    return name


def _listed(inbox: Path) -> dict[str, str]:
    return json.loads((inbox / MANIFEST).read_text(encoding="utf-8"))["files"]


def test_module_sections_are_independent_and_the_loader_reads_their_union(tmp_path: Path):
    manifest.save_section(tmp_path, "ops", {"seed": 42, "files": {_touch(tmp_path, "incident_2026-08.csv"): "a"}})
    manifest.save_section(tmp_path, "sap", {"files": {_touch(tmp_path, "charm_changes.xlsx"): "b"}})
    data = json.loads((tmp_path / MANIFEST).read_text(encoding="utf-8"))
    assert data["data_class"] == "synthetic" and data["modules"]["ops"]["seed"] == 42
    assert _listed(tmp_path) == {"charm_changes.xlsx": "b", "incident_2026-08.csv": "a"}

    manifest.clean(tmp_path, "sap")
    assert not (tmp_path / "charm_changes.xlsx").exists() and (tmp_path / "incident_2026-08.csv").exists()
    assert _listed(tmp_path) == {"incident_2026-08.csv": "a"}

    manifest.save_section(tmp_path, "ops", {"files": {_touch(tmp_path, "incident_2026-09.csv"): "c"}})
    assert _listed(tmp_path) == {"incident_2026-09.csv": "c"}  # a section is replaced as a whole
    manifest.clean(tmp_path, "ops")
    assert not (tmp_path / MANIFEST).exists() and not (tmp_path / "incident_2026-09.csv").exists()


def test_a_manifest_from_before_sections_is_read_as_one_legacy_section(tmp_path: Path):
    old = {"data_class": "synthetic", "seed": 7, "files": {_touch(tmp_path, "incident_2026-08.csv"): "a"}}
    (tmp_path / MANIFEST).write_text(json.dumps(old), encoding="utf-8")
    assert manifest.load(tmp_path) == {"legacy": {"seed": 7, "files": {"incident_2026-08.csv": "a"}}}

    manifest.save_section(tmp_path, "sap", {"files": {_touch(tmp_path, "idoc_status.csv"): "b"}})
    assert _listed(tmp_path) == {"idoc_status.csv": "b", "incident_2026-08.csv": "a"}  # still importable
    manifest.clean(tmp_path, "ops", manifest.LEGACY)
    assert not (tmp_path / "incident_2026-08.csv").exists() and (tmp_path / "idoc_status.csv").exists()
    assert _listed(tmp_path) == {"idoc_status.csv": "b"}


def test_an_unreadable_manifest_counts_as_none(tmp_path: Path):
    (tmp_path / MANIFEST).write_text("{not json", encoding="utf-8")
    assert manifest.load(tmp_path) == {}
