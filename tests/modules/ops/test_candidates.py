"""Recurring-issue text candidates on the ops profile: the planted P1 (incidents after a change), P5 (monthly batch
failures) and P7 (one-day SSO outage across apps) show up with the right periodicity, background symptoms stay
coherent, and the result is deterministic."""

from __future__ import annotations

import csv
import json
from datetime import date
from typing import Any

import pytest

from sed import db
from sed.modules.ops.candidates import _pairs, normalise, text_candidates
from sed.settings import load_settings

AS_OF = date(2026, 9, 1)


@pytest.fixture(scope="module")
def groups(ops_profile: Any) -> list:
    settings = load_settings(ops_profile.paths)
    conn = db.connect(ops_profile.paths.db, readonly=True)
    try:
        return text_candidates(conn, AS_OF, settings.reporting_tz, months=12)
    finally:
        conn.close()


@pytest.fixture(scope="module")
def truth(ops_profile: Any) -> dict[str, Any]:
    folder = ops_profile.ground_truth
    with (folder / "ticket_truth.csv").open(encoding="utf-8", newline="") as fh:
        tickets = {row["number"]: row for row in csv.DictReader(fh)}
    return {"tickets": tickets, "patterns": json.loads((folder / "patterns.json").read_text(encoding="utf-8"))}


def _pattern_share(group, truth, pattern: str) -> float:
    numbers = [tid.split(":", 1)[1] for tid in group.ticket_ids]
    return sum(1 for n in numbers if truth["tickets"].get(n, {}).get("pattern") == pattern) / len(numbers)


def test_p1_incidents_after_the_change_form_episode_groups_naming_the_change(groups, truth):
    p1 = [g for g in groups if _pattern_share(g, truth, "P1") >= 0.8]
    assert p1, "P1 incidents must form at least one candidate group"
    covered = sum(len(g.ticket_ids) for g in p1)
    assert covered >= 0.6 * truth["patterns"]["P1"]["incidents"]
    change = truth["patterns"]["P1"]["change"]
    assert all(g.periodicity in {"episode", "burst"} for g in p1)
    assert all(change in {c["number"] for c in g.changes_before_onset} for g in p1)


def test_p5_monthly_batch_failures_are_periodic(groups, truth):
    p5 = [g for g in groups if _pattern_share(g, truth, "P5") >= 0.8]
    assert p5 and all(g.periodicity == "monthly" and g.months_active >= 10 for g in p5)
    assert {d for g in p5 for d in g.day_of_month} <= set(range(1, 6))  # business days 1-2 of each month


def test_p7_outage_day_bursts_on_several_apps(groups, truth):
    day = truth["patterns"]["P7"]["day"]
    p7 = [g for g in groups if _pattern_share(g, truth, "P7") >= 0.8]
    assert len({g.app_id for g in p7}) >= 3
    assert all(g.first_day == g.last_day == day and g.periodicity == "burst" for g in p7)
    assert all(g.bursts and g.bursts[0]["day"] == day for g in p7)


def test_groups_are_symptom_coherent_and_deterministic(groups, ops_profile, truth):
    # Most groups hold one planted symptom: at least 80% of their tickets share the most common truth symptom key.
    coherent = 0
    for g in groups:
        keys = [truth["tickets"].get(t.split(":", 1)[1], {}).get("symptom_key") for t in g.ticket_ids]
        top = max(keys.count(k) for k in set(keys))
        coherent += top / len(keys) >= 0.8
    assert coherent / len(groups) >= 0.9
    settings = load_settings(ops_profile.paths)
    conn = db.connect(ops_profile.paths.db, readonly=True)
    try:
        again = text_candidates(conn, AS_OF, settings.reporting_tz, months=12)
    finally:
        conn.close()
    assert [(g.group_id, g.ticket_ids) for g in again] == [(g.group_id, g.ticket_ids) for g in groups]


def test_normalise_and_mutual_pairs():
    assert normalise("Batch job 78 aborted in Orion-ERP (DOC-1234)!") == "batch job aborted in orion erp doc"
    from scipy.sparse import csr_matrix

    rows = csr_matrix([[1.0, 0.0], [0.8, 0.6], [0.0, 1.0]])
    assert _pairs(rows, threshold=0.5, top_k=1) == [(0, 1)]  # 1-2 is not mutual (2's nearest is 1, 1's nearest is 0)
    assert _pairs(rows, threshold=0.99, top_k=1) == []
