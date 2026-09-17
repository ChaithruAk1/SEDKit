"""Review routes on a writable ops profile: the review queue, finding decisions (single and bulk, token required), run
detail with its review sample, verdicts with corrections, run approval and rejection, and manual label corrections."""

from __future__ import annotations

import json

from sed import db
from sed.ai import findings as F
from sed.api.models import ReviewQueueOut, RunDetailOut
from tests.fake_agent.flow import finished_run, query, scalar
from tests.fake_agent.verdicts import sample_keys
from tests.fixtures.api import api_client
from tests.platform.api.conftest import assert_envelope


def _finding_run(paths, run_id: str, keys: list[str]) -> list[str]:
    conn = db.connect(paths.db)
    try:
        with db.write_tx(conn):
            conn.execute(
                "INSERT INTO ai_run (run_id, skill, skill_hash, schema_version, invoked_via, profile, status, "
                "started_at) VALUES (?, 'sed-find-recurring', 'h', 1, 'interactive', 'synthetic', 'completed', "
                "'2026-09-01T00:00:00Z')",
                (run_id,),
            )
            return [
                F.upsert_draft(
                    conn,
                    run_id=run_id,
                    stable_key=key,
                    kind="issue_cluster",
                    title=f"Queue stuck {key}",
                    body_md="Messages stuck in {{f:T001.tickets}} cases.",
                    severity="high",
                    confidence=0.8,
                    payload={
                        "ticket_count": 40,
                        "periodicity": "monthly",
                        "recommendation": "raise_problem",
                        "evidence": [{"fact_key": "T001.tickets", "value": 40}],
                    },
                    subject_type="app",
                    subject_id="APM1",
                )
                for key in keys
            ]
    finally:
        conn.close()


def test_queue_and_finding_decisions(ops_profile_rw):
    paths = ops_profile_rw.paths
    one, two, three = _finding_run(paths, "rec-api", ["k:1", "k:2", "k:3"])
    client = api_client(paths)

    body = ReviewQueueOut.model_validate(client.get("/api/review/queue?include_rule=false").json())
    assert [i.finding_id for i in body.items] == [one, two, three] and body.counts == {"draft": 3}
    first = body.items[0]
    assert (first.periodicity, first.ticket_count, first.recommendation) == ("monthly", 40, "raise_problem")
    assert [(e.fact_key, e.value) for e in first.evidence] == [("T001.tickets", 40)]
    assert client.get("/api/review/queue").json()["counts"].get("rule_active", 0) >= 1  # the profile's rule findings

    no_token = api_client(paths, send_token=False)
    assert_envelope(no_token.post(f"/api/findings/{one}/review", json={"action": "approve"}), 403, "forbidden")
    ok = client.post(f"/api/findings/{one}/review", json={"action": "approve", "note": "checked"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["results"] == [{"finding_id": one, "action": "approve", "status": "approved"}]
    assert_envelope(client.post(f"/api/findings/{one}/review", json={"action": "approve"}), 412, "precondition")
    assert_envelope(client.post(f"/api/findings/{two}/review", json={"action": "edit"}), 422, "validation")
    assert_envelope(client.post(f"/api/findings/{two}/review", json={"action": "delete"}), 422, "validation")

    edited = client.post(f"/api/findings/{two}/review", json={"action": "edit", "body_md": "Reworded."})
    assert edited.status_code == 200, edited.text
    # Bulk is all or nothing: one finding that cannot take the action leaves the other untouched.
    bulk = {"finding_ids": [three, one], "action": "approve"}
    assert_envelope(client.post("/api/findings/bulk-review", json=bulk), 412, "precondition")
    assert scalar(paths, "SELECT status FROM finding WHERE finding_id = ?", three) == "draft"
    rejected = client.post(
        "/api/findings/bulk-review", json={"finding_ids": [three], "action": "reject", "note": "noise"}
    )
    assert rejected.status_code == 200, rejected.text
    assert dict(query(paths, "SELECT finding_id, status FROM finding WHERE run_id = 'rec-api'")) == {
        one: "approved", two: "approved", three: "rejected"
    }  # fmt: skip
    reviewers = {r[0] for r in query(paths, "SELECT reviewer FROM review_decision WHERE target_type = 'finding'")}
    assert len(reviewers) == 1 and None not in reviewers
    assert client.get("/api/review/queue?include_rule=false").json()["items"] == []

    rates = client.get("/api/ai/review-rates?skill=sed-find-recurring").json()["rows"]
    assert len(rates) == 1 and rates[0]["skill_hash"] == "h" and rates[0]["month"] == "2026-09"
    counts = [rates[0][k] for k in ("findings_drafted", "findings_approved", "findings_edited", "findings_rejected")]
    assert counts == [3, 1, 1, 1] and rates[0]["approval_rate"] == round(1 / 3, 4) and rates[0]["findings_open"] == 0


def test_rule_findings_take_suppress_until_through_the_api(ops_profile_rw):
    paths = ops_profile_rw.paths
    client = api_client(paths)
    rule = next(i for i in client.get("/api/review/queue").json()["items"] if i["origin"] == "rule")
    bad = client.post(f"/api/findings/{rule['finding_id']}/review", json={"action": "approve"})
    assert_envelope(bad, 412, "precondition")
    response = client.post(
        f"/api/findings/{rule['finding_id']}/review",
        json={"action": "suppress_until", "until": "2099-12-31", "note": "renegotiated"},
    )
    assert response.status_code == 200, response.text
    assert scalar(paths, "SELECT suppress_until FROM finding WHERE finding_id = ?", rule["finding_id"]) == "2099-12-31"
    remaining = [i["finding_id"] for i in client.get("/api/review/queue").json()["items"]]
    assert rule["finding_id"] not in remaining  # suppressed rule findings leave the queue until the date passes


def test_run_detail_verdicts_and_approval(ops_profile_rw):
    paths = ops_profile_rw.paths
    plan, _ = finished_run(paths)
    run_id = plan.run_id
    client = api_client(paths)
    assert_envelope(client.get("/api/runs/no-such-run"), 412, "precondition")

    detail = RunDetailOut.model_validate(client.get(f"/api/runs/{run_id}").json())
    assert detail.run.status == "completed" and detail.random and detail.matrix
    assert all(c.verdict is None and c.label.am_category and c.ticket.number for c in detail.random)
    codes = {c.code: c for c in detail.categories}
    assert {"access", "integration", "other"} <= set(codes) and "request" in detail.misfiled_as
    assert all(s.only in (None, "sap") for c in detail.categories for s in c.subcategories)
    assert any(s.only == "sap" for s in codes["integration"].subcategories)
    text = json.dumps(client.get(f"/api/runs/{run_id}").json())
    assert "@example" not in text  # scrubbed display fields only

    assert_envelope(client.post(f"/api/runs/{run_id}/review", json={"action": "approve"}), 412, "precondition")
    keys = sample_keys({"random": [c.model_dump() for c in detail.random]})
    wrong = keys[0]
    verdicts = {k: ("incorrect" if k == wrong else "correct") for k in keys}
    unknown = client.post(f"/api/runs/{run_id}/verdicts", json={"verdicts": {"incident:X|open": "correct"}})
    assert_envelope(unknown, 422, "validation")
    recorded = client.post(
        f"/api/runs/{run_id}/verdicts",
        json={"verdicts": verdicts, "corrections": {wrong: {"category": "other", "subcategory": None}}},
    )
    assert recorded.status_code == 200, recorded.text
    assert recorded.json()["recorded"] == len(keys) and recorded.json()["random_missing"] == 0

    again = RunDetailOut.model_validate(client.get(f"/api/runs/{run_id}").json())
    card = next(c for c in again.random if c.key == wrong)
    assert card.verdict == "incorrect" and card.correction is not None and card.correction.category == "other"

    assert_envelope(client.post(f"/api/runs/{run_id}/review", json={"action": "reject"}), 422, "validation")
    approved = client.post(f"/api/runs/{run_id}/review", json={"action": "approve", "note": "sample ok"})
    assert approved.status_code == 200, approved.text
    out = approved.json()
    assert out["status"] == "approved" and out["corrections_applied"] == 1
    assert 0 < out["sample_accuracy"] < 1 and out["sample_ci_low"] < out["sample_accuracy"]
    assert client.get(f"/api/runs/{run_id}").json()["run"]["status"] == "approved"


def test_label_correction_through_the_api(ops_profile_rw):
    paths = ops_profile_rw.paths
    plan, _ = finished_run(paths)
    ticket_id, stage = query(
        paths, "SELECT ticket_id, stage FROM ai_ticket_label WHERE run_id = ? ORDER BY ticket_id LIMIT 1", plan.run_id
    )[0]
    client = api_client(paths)
    bad = client.post("/api/labels/correct", json={"ticket_id": ticket_id, "stage": stage, "category": "nonsense"})
    assert_envelope(bad, 422, "validation")
    response = client.post(
        "/api/labels/correct",
        json={"ticket_id": ticket_id, "stage": stage, "category": "access", "misfiled_as": "request"},
    )
    assert response.status_code == 200, response.text
    run_id = response.json()["run_id"]
    assert run_id.startswith("manual-")
    assert scalar(paths, "SELECT status FROM ai_run WHERE run_id = ?", run_id) == "approved"
    manual = client.get(f"/api/runs/{run_id}").json()
    assert manual["random"] == [] and manual["run"]["skill"] == "manual" and manual["categories"] == []
