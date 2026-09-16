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


def truth_output(paths: Any, plan: Any, batch: Any, truth: dict[str, Any], *, wrong_every: int = 0) -> Path:
    """Write the output an agent that knows the SAP ground truth would write for one batch (SAP lines from the truth,
    other lines `other`), and return its path. Every `wrong_every`-th SAP line gets a wrong SAP subcategory."""
    from sed import db
    from tests.fake_agent.triage import write_output

    conn = db.connect(paths.db, readonly=True)
    try:
        numbers = dict(
            conn.execute(
                "SELECT i.ref, t.number FROM ai_batch_item i JOIN ticket t ON t.ticket_id = i.item_id "
                "WHERE i.batch_id = ?",
                (f"{plan.run_id}/{batch.batch}",),
            ).fetchall()
        )
    finally:
        conn.close()
    items, k = [], 0
    for line in Path(batch.packet).read_text(encoding="utf-8").splitlines():
        ref = json.loads(line)["ref"]
        row = truth["tickets"].get(numbers[ref])
        if row is None or not row.get("am_category"):
            items.append(_item(ref, "other", None, "none"))
            continue
        k += 1
        sub = row["am_subcategory"]
        if wrong_every and k % wrong_every == 0:
            sub = None  # the truth always has a SAP subcategory, so null is wrong
        items.append(_item(ref, row["am_category"], sub, row["misfiled_as"] or "none"))
    write_output(batch.out, {"meta": {"model": "truth-agent"}, "items": items})
    return Path(batch.out)


def _item(ref: str, category: str, sub: str | None, misfiled: str) -> dict[str, Any]:
    return {
        "ref": ref,
        "am_category": category,
        "am_subcategory": sub,
        "symptom_key": f"{category}_symptom",
        "misfiled_as": misfiled,
        "confidence": 0.9,
        "rationale": "Label taken from the synthetic ground truth.",
    }
