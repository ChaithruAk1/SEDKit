"""AI findings lifecycle: idempotent drafts, carry-forward at finish-run, per-finding review, rule acknowledgements,
stale inputs when an input run is rejected, and the review queue."""

from __future__ import annotations

import json
from datetime import date

import pytest

from sed import db
from sed.ai import findings as F
from sed.ai.review import reject_run, review_findings
from sed.errors import PreconditionFailed, ValidationFailed
from tests.fake_agent.flow import finished_run, query, scalar


def _run(conn, run_id: str, *, skill: str = "sed-find-recurring", status: str = "completed", inputs=()) -> str:
    conn.execute(
        "INSERT INTO ai_run (run_id, skill, skill_hash, schema_version, invoked_via, profile, status, started_at, "
        "input_run_ids_json) VALUES (?, ?, 'h', 1, 'interactive', 'synthetic', ?, '2026-09-01T00:00:00Z', ?)",
        (run_id, skill, status, json.dumps(list(inputs))),
    )
    return run_id


def _draft(conn, run_id: str, key: str = "issue_cluster:app1:queue_stuck", **over) -> str:
    fields = {
        "kind": "issue_cluster",
        "title": "Queue stuck",
        "body_md": "Messages stuck in {{f:x}} queues.",
        "severity": "high",
        "confidence": 0.8,
        "payload": {"ticket_count": 40, "evidence": [{"fact_key": "cluster.count"}]},
        "subject_type": "app",
        "subject_id": "APM1",
    }
    fields.update(over)
    return F.upsert_draft(conn, run_id=run_id, stable_key=key, **fields)


@pytest.fixture
def conn(ops_profile_rw):
    c = db.connect(ops_profile_rw.paths.db)
    try:
        yield c
    finally:
        c.close()


def test_drafts_are_idempotent_per_run_and_key(conn):
    with db.write_tx(conn):
        _run(conn, "r1")
        first = _draft(conn, "r1")
        again = _draft(conn, "r1", title="Queue stuck again")
    assert first == again
    rows = conn.execute("SELECT title, status FROM finding WHERE run_id = 'r1'").fetchall()
    assert [tuple(r) for r in rows] == [("Queue stuck again", "draft")]


def test_carry_forward_keeps_published_text_and_queues_new_wording(conn):
    with db.write_tx(conn):
        _run(conn, "r1")
        old = _draft(conn, "r1")
        F.review_finding(conn, old, "approve", "tester")
        _run(conn, "r2")
        new = _draft(
            conn,
            "r2",
            body_md="Fresh wording.",
            payload={"ticket_count": 45, "evidence": [{"fact_key": "cluster.count"}]},
        )
        counts = F.carry_forward(conn, "r2")
    assert counts == {"carried_forward": 1, "unchanged_approved": 0, "material_change": 0, "new": 0}
    row = conn.execute("SELECT * FROM finding WHERE finding_id = ?", (new,)).fetchone()
    assert (row["status"], row["body_md"], row["pending_body_md"]) == (
        "update_pending", "Messages stuck in {{f:x}} queues.", "Fresh wording."
    )  # fmt: skip
    assert row["carried_forward_from"] == old and row["reviewed_by"] == "tester"
    assert conn.execute("SELECT status FROM finding WHERE finding_id = ?", (old,)).fetchone()[0] == "superseded"
    with db.write_tx(conn):
        F.review_finding(conn, new, "approve_update", "tester")
    row = conn.execute("SELECT status, body_md, pending_body_md FROM finding WHERE finding_id = ?", (new,)).fetchone()
    assert tuple(row) == ("approved", "Fresh wording.", None)


def test_identical_wording_is_approved_and_material_changes_return_to_review(conn):
    with db.write_tx(conn):
        _run(conn, "r1")
        F.review_finding(conn, _draft(conn, "r1"), "approve", "tester")
        _run(conn, "r2")
        same = _draft(conn, "r2")
        assert F.carry_forward(conn, "r2")["unchanged_approved"] == 1
        _run(conn, "r3")
        bigger = _draft(conn, "r3", payload={"ticket_count": 80, "evidence": [{"fact_key": "cluster.count"}]})
        _run(conn, "r4")
        worse = _draft(conn, "r4", key="issue_cluster:app1:other", severity="high")
        counts = F.carry_forward(conn, "r3")
    assert conn.execute("SELECT status FROM finding WHERE finding_id = ?", (same,)).fetchone()[0] == "approved"
    assert counts["material_change"] == 1
    row = conn.execute("SELECT status, payload_json FROM finding WHERE finding_id = ?", (bigger,)).fetchone()
    assert (
        row["status"] == "draft"
        and "ticket_count 40 -> 80" in json.loads(row["payload_json"])["material_change"]["reasons"]
    )
    assert conn.execute("SELECT status FROM finding WHERE finding_id = ?", (worse,)).fetchone()[0] == "draft"


def test_report_sections_are_never_carried_forward(conn):
    with db.write_tx(conn):
        _run(conn, "r1", skill="sed-draft-report")
        F.review_finding(
            conn,
            _draft(conn, "r1", key="report_section:weekly:2026-W35:headline", kind="report_section"),
            "approve",
            "t",
        )
        _run(conn, "r2", skill="sed-draft-report")
        new = _draft(conn, "r2", key="report_section:weekly:2026-W35:headline", kind="report_section")
        assert F.carry_forward(conn, "r2")["new"] == 1
    assert conn.execute("SELECT status FROM finding WHERE finding_id = ?", (new,)).fetchone()[0] == "draft"


def test_review_actions_and_their_preconditions(conn):
    with db.write_tx(conn):
        _run(conn, "r1")
        a = _draft(conn, "r1", key="k:a")
        b = _draft(conn, "r1", key="k:b")
        c = _draft(conn, "r1", key="k:c")
    with db.write_tx(conn):
        with pytest.raises(ValidationFailed, match="note"):
            F.review_finding(conn, a, "reject", "tester")
        F.review_finding(conn, a, "reject", "tester", note="not a real cluster")
        result = F.review_finding(conn, b, "edit", "tester", body_md="Better text.", note="tightened")
        assert result["status"] == "approved"
        with pytest.raises(PreconditionFailed):
            F.review_finding(conn, a, "approve", "tester")  # rejected findings stay rejected
        with pytest.raises(PreconditionFailed):
            F.review_finding(conn, c, "acknowledge", "tester", note="n")  # acknowledge is for rule findings
        with pytest.raises(PreconditionFailed):
            F.review_finding(conn, c, "approve_update", "tester")
        with pytest.raises(PreconditionFailed):
            F.review_finding(conn, "f-missing", "approve", "tester")
    row = conn.execute("SELECT body_md, edited, payload_json FROM finding WHERE finding_id = ?", (b,)).fetchone()
    assert row["body_md"] == "Better text." and row["edited"] == 1
    assert json.loads(row["payload_json"])["original_body_md"] == "Messages stuck in {{f:x}} queues."
    decisions = conn.execute("SELECT decision FROM review_decision WHERE target_type = 'finding' ORDER BY decision_id")
    assert [d[0] for d in decisions] == ["reject", "edit"]


def test_rule_findings_take_acknowledge_and_suppress(conn):
    with db.write_tx(conn):
        conn.execute(
            "INSERT INTO finding (finding_id, origin, stable_key, kind, title, status, created_at, payload_json) "
            "VALUES ('rule-1', 'rule', 'renewal_risk:C1', 'renewal_risk', 'Notice soon', 'active', "
            "'2026-09-01T00:00:00Z', '{}')"
        )
        with pytest.raises(PreconditionFailed):
            F.review_finding(conn, "rule-1", "approve", "tester")
        with pytest.raises(ValidationFailed):
            F.review_finding(conn, "rule-1", "suppress_until", "tester", note="renegotiating")
        F.review_finding(conn, "rule-1", "suppress_until", "tester", note="renegotiating", until=date(2026, 12, 1))
    row = conn.execute("SELECT status, suppress_until, review_note FROM finding WHERE finding_id = 'rule-1'").fetchone()
    assert tuple(row) == ("active", "2026-12-01", "renegotiating")
    with db.write_tx(conn):
        F.review_finding(conn, "rule-1", "acknowledge", "tester", note="accepted risk")
    assert conn.execute("SELECT status FROM finding WHERE finding_id = 'rule-1'").fetchone()[0] == "acknowledged"


def test_rejecting_an_input_run_marks_dependent_findings_stale(ops_profile_rw):
    paths = ops_profile_rw.paths
    plan, _ = finished_run(paths, limit=20, batch_size=20)
    conn = db.connect(paths.db)
    try:
        with db.write_tx(conn):
            _run(conn, "rec-1", inputs=[plan.run_id])
            approved = _draft(conn, "rec-1", key="k:approved")
            F.review_finding(conn, approved, "approve", "tester")
            draft = _draft(conn, "rec-1", key="k:draft")
            _run(conn, "rec-2", inputs=["some-other-run"])
            other = _draft(conn, "rec-2", key="k:other")
    finally:
        conn.close()
    result = reject_run(paths, plan.run_id, "tester", "bad labels")
    assert result["dependent_findings_stale"] == 2
    statuses = dict(query(paths, "SELECT finding_id, status FROM finding WHERE run_id IN ('rec-1', 'rec-2')"))
    assert statuses == {approved: "stale_input", draft: "stale_input", other: "draft"}
    queued = {f["finding_id"]: f["status"] for f in _queue(paths)}
    assert queued[approved] == "stale_input" and queued[other] == "draft"


def _queue(paths):
    conn = db.connect(paths.db, readonly=True)
    try:
        return F.queue(conn, include_rule=False)
    finally:
        conn.close()


def test_rejecting_a_finding_run_rejects_its_open_findings_and_bulk_review_is_atomic(ops_profile_rw):
    paths = ops_profile_rw.paths
    conn = db.connect(paths.db)
    try:
        with db.write_tx(conn):
            _run(conn, "rec-1")
            one = _draft(conn, "rec-1", key="k:1")
            two = _draft(conn, "rec-1", key="k:2")
    finally:
        conn.close()
    with pytest.raises(PreconditionFailed):
        review_findings(paths, [one, "f-missing"], "approve", "tester")
    assert scalar(paths, "SELECT COUNT(*) FROM finding WHERE status = 'approved'") == 0  # nothing applied
    review_findings(paths, [one], "approve", "tester")
    result = reject_run(paths, "rec-1", "tester", "run was noise")
    assert result["findings_rejected"] == 1
    assert dict(query(paths, "SELECT finding_id, status FROM finding WHERE run_id = 'rec-1'")) == {
        one: "approved", two: "rejected"
    }  # fmt: skip
