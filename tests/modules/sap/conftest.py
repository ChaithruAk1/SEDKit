"""Shared fixtures for the SAP module tests: a read-only connection, the scope and the ground truth of `sap_profile`."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import pytest
import yaml


@pytest.fixture
def ro_conn(sap_profile: Any):
    """A query_only connection on the shared session SAP profile."""
    from sed import db

    conn = db.connect(sap_profile.paths.db, readonly=True)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture(scope="session")
def sap_truth(sap_profile: Any) -> dict[str, Any]:
    """Ground truth written by the SAP generator: `tickets` (number -> row), `changes` (change id -> row), `patterns`
    and `pii`."""
    folder: Path = sap_profile.ground_truth
    with (folder / "ticket_truth.csv").open(encoding="utf-8", newline="") as fh:
        tickets = {row["number"]: row for row in csv.DictReader(fh)}
    with (folder / "change_truth.csv").open(encoding="utf-8", newline="") as fh:
        changes = {row["change_id"]: row for row in csv.DictReader(fh)}
    return {
        "tickets": tickets,
        "changes": changes,
        "patterns": json.loads((folder / "patterns.json").read_text(encoding="utf-8")),
        "pii": json.loads((folder / "pii_injections.json").read_text(encoding="utf-8")),
    }


def write_sap_config(paths: Any, name: str, data: dict[str, Any]) -> None:
    """Write a DATA_DIR override for config/sap/<name>."""
    target = paths.config / "sap" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
