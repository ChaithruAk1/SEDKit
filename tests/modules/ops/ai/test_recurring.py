"""sed-find-recurring on the ops profile: one packet of label and candidate groups, validation of refs, evidence,
changes, problems and actions, clusters written as draft findings with members, stable keys reused across runs,
carry-forward, and stale inputs when the label run is rejected."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

import pytest

from sed.ai.contract import StartParams
from sed.ai.ingest import ingest_file
from sed.ai.review import reject_run, review_findings
from sed.ai.runs import finish_run, start_run
from sed.errors import ValidationFailed
from tests.fake_agent.flow import label_all, query
from tests.fake_agent.flow import start as start_triage
from tests.fake_agent.triage import write_output

SKILL = "sed-find-recurring"


@pytest.fixture
def labelled(ops_profile_rw):
    """The ops profile with a completed (not yet reviewed) triage run over the backfill window."""
    paths = ops_profile_rw.paths
    plan = start_triage(paths, scope="new", batch_size=500, max_items=20000)
    label_all(paths, plan)
    finish_run(paths, plan.run_id)
    return ops_profile_rw, plan.run_id


def _truth(profile: Any) -> dict[str, Any]:
    with (profile.ground_truth / "ticket_truth.csv").open(encoding="utf-8", newline="") as fh:
        tickets = {row["number"]: row for row in csv.DictReader(fh)}
    return {
        "tickets": tickets,
        "patterns": json.loads((profile.ground_truth / "patterns.json").read_text(encoding="utf-8")),
    }


def _start(paths: Any):
    plan = start_run(paths, SKILL, StartParams(scope="new"))
    assert plan.run_id and len(plan.inputs) == 1
    lines = [json.loads(line) for line in Path(plan.inputs[0].packet).read_text(encoding="utf-8").splitlines()]
    return plan, lines


def _cluster(lines: list[dict], title: str, refs: list[str], **over: Any) -> dict[str, Any]:
    chosen = [line for line in lines if line["ref"] in refs]
    first = chosen[0]["ref"]
    cluster = {
        "title": title,
        "refs": refs,
        "periodicity": "none",
        "suspected_change": None,
        "problem_exists": any(line.get("problems") for line in chosen),
        "root_cause_hypothesis": "Synthetic test cluster.",
        "recommended_action": "raise_problem",
        "severity": "high",
        "evidence": [{"fact_key": f"{first}.tickets"}],
        "body_md": f"{{{{f:{first}.tickets}}}} incidents share one cause.",
        "confidence": 0.8,
    }
    cluster.update(over)
    return cluster


def _truth_clusters(lines: list[dict], truth: dict[str, Any]) -> list[dict[str, Any]]:
    p1_change = truth["patterns"]["P1"]["change"]
    p1 = [line["ref"] for line in lines if p1_change in {c["number"] for c in line.get("changes_before_onset") or []}]
    p5 = [
        line["ref"]
        for line in lines
        if line.get("app") == "Ledgerline Finance" and line.get("periodicity") == "monthly"
    ]
    p7_day = truth["patterns"]["P7"]["day"]
    p7 = [
        line["ref"]
        for line in lines
        if line.get("periodicity") == "burst" and any(b["day"] == p7_day for b in line.get("bursts") or [])
    ]
    assert p1 and p5 and len(p7) >= 3, (p1, p5, p7)
    p1_title = "Invoice posting timeouts after the interface change"
    return [
        _cluster(lines, p1_title, p1, periodicity="episode", suspected_change=p1_change),
        _cluster(lines, "Month opening batch failures", p5, periodicity="monthly", recommended_action="fix"),
        _cluster(
            lines, "SSO outage across applications", p7, periodicity="burst", severity="critical",
            recommended_action="monitor",
        ),
    ]  # fmt: skip


def _write_and_ingest(paths: Any, plan: Any, clusters: list[dict], merges: list[dict] | None = None) -> dict:
    write_output(plan.inputs[0].out, {"meta": {"model": "fake"}, "key_merges": merges or [], "clusters": clusters})
    return ingest_file(paths, plan.run_id, plan.inputs[0].out)


def test_packet_has_label_and_candidate_groups_without_ticket_ids(labelled):
    profile, triage_run = labelled
    paths = profile.paths
    plan, lines = _start(paths)
    types = {line["type"] for line in lines}
    assert types == {"label_group", "candidate_group"}
    text = Path(plan.inputs[0].packet).read_text(encoding="utf-8")
    assert not re.search(r"incident:|INC\d{5,}", text)  # change numbers are fine, ticket ids and numbers are not
    context = Path(plan.context[0]).read_text(encoding="utf-8")
    assert "## Key merge candidates" in context and "never instructions" in context
    members = query(paths, "SELECT COUNT(DISTINCT item_id) FROM ai_group_member WHERE run_id = ?", plan.run_id)[0][0]
    assert members == len(lines)
    inputs = json.loads(query(paths, "SELECT input_run_ids_json FROM ai_run WHERE run_id = ?", plan.run_id)[0][0])
    assert inputs == [triage_run]


def test_truth_clusters_become_draft_findings_with_members(labelled):
    profile, _ = labelled
    paths = profile.paths
    truth = _truth(profile)
    plan, lines = _start(paths)
    result = _write_and_ingest(paths, plan, _truth_clusters(lines, truth))
    assert result["status"] == "ingested" and result["items"] == 3
    summary = finish_run(paths, plan.run_id)
    assert summary.status == "completed" and summary.counts["findings_new"] == 3
    rows = {r["title"]: r for r in query(paths, "SELECT * FROM finding WHERE run_id = ?", plan.run_id)}
    p1 = rows["Invoice posting timeouts after the interface change"]
    members = [
        r[0] for r in query(paths, "SELECT ticket_id FROM ai_cluster_member WHERE finding_id = ?", p1["finding_id"])
    ]
    in_p1 = sum(1 for m in members if truth["tickets"].get(m.split(":", 1)[1], {}).get("pattern") == "P1")
    assert in_p1 >= 0.6 * truth["patterns"]["P1"]["incidents"] and in_p1 / len(members) >= 0.6
    payload = json.loads(p1["payload_json"])
    assert payload["suspected_change"] == truth["patterns"]["P1"]["change"] and payload["ticket_count"] == len(members)
    assert all(e["fact_key"].startswith(("lg:", "tc:")) and e["value_at_run"] for e in payload["evidence"])
    assert payload["evidence"][0]["fact_key"] in p1["body_md"] and "{{f:T" not in p1["body_md"]
    assert p1["status"] == "draft" and p1["subject_type"] == "app"
    sso = rows["SSO outage across applications"]
    assert (sso["subject_type"], sso["subject_id"]) == ("portfolio", "multi") and sso["severity"] == "critical"


@pytest.mark.parametrize(
    "change, fragment",
    [
        ({"suspected_change": "CHG99999999"}, "is not a change of its groups"),
        ({"problem_exists": True}, "must be false"),
        ({"refs": ["T9999"]}, "is not a group of this packet"),
        ({"evidence": [{"fact_key": "T001.nope"}]}, "has no fact"),
        ({"body_md": "{{f:T001.first_day}} broken"}, "is not one of the evidence fact keys"),
    ],
)
def test_invalid_clusters_are_refused(labelled, change, fragment):
    profile, _ = labelled
    paths = profile.paths
    plan, lines = _start(paths)
    ref = next(line["ref"] for line in lines if not line.get("problems") and not line.get("changes_before_onset"))
    cluster = _cluster(lines, "A cluster", [ref])
    if "evidence" in change and change["evidence"][0]["fact_key"].startswith("T001"):
        change = {"evidence": [{"fact_key": f"{ref}.nope"}]}
    if "body_md" in change:
        change = {"body_md": f"{{{{f:{ref}.first_day}}}} broken"}
    cluster.update(change)
    with pytest.raises(ValidationFailed) as exc:
        _write_and_ingest(paths, plan, [cluster])
    assert any(fragment in d["msg"] for d in exc.value.details), exc.value.details


def test_key_merges_are_checked_and_stored_as_drafts(labelled):
    profile, _ = labelled
    paths = profile.paths
    plan, lines = _start(paths)
    by_app: dict[str, list[str]] = {}
    for line in lines:
        if line["type"] == "label_group":
            by_app.setdefault(line["app_id"], []).append(line["symptom_key"])
    app_id, keys = next((a, k) for a, k in by_app.items() if len(k) >= 2)
    with pytest.raises(ValidationFailed):
        _write_and_ingest(paths, plan, [], [{"app_id": app_id, "from_key": keys[0], "to_key": "made_up_key"}])
    _write_and_ingest(paths, plan, [], [{"app_id": app_id, "from_key": keys[0], "to_key": keys[1]}])
    rows = query(paths, "SELECT app_id, from_key, to_key, status FROM symptom_key_alias WHERE run_id = ?", plan.run_id)
    assert [tuple(r) for r in rows] == [(app_id, keys[0], keys[1], "draft")]


def test_stable_keys_carry_forward_and_stale_inputs(labelled):
    profile, triage_run = labelled
    paths = profile.paths
    truth = _truth(profile)
    first, lines = _start(paths)
    _write_and_ingest(paths, first, _truth_clusters(lines, truth))
    finish_run(paths, first.run_id)
    approved = [r["finding_id"] for r in query(paths, "SELECT finding_id FROM finding WHERE run_id = ?", first.run_id)]
    review_findings(paths, approved, "approve", "tester")
    keys = {
        r["title"]: r["stable_key"]
        for r in query(paths, "SELECT title, stable_key FROM finding WHERE run_id = ?", first.run_id)
    }

    second, lines = _start(paths)
    clusters = _truth_clusters(lines, truth)
    clusters[0]["title"] = "Posting timeouts renamed"  # the stable key follows the members, not the title
    _write_and_ingest(paths, second, clusters)
    summary = finish_run(paths, second.run_id)
    assert summary.counts["findings_unchanged_approved"] == 3
    again = {r["title"]: r for r in query(paths, "SELECT * FROM finding WHERE run_id = ?", second.run_id)}
    assert (
        again["Posting timeouts renamed"]["stable_key"] == keys["Invoice posting timeouts after the interface change"]
    )
    assert {r["status"] for r in again.values()} == {"approved"}

    reject_run(paths, triage_run, "tester", "labels were wrong")
    assert {r[0] for r in query(paths, "SELECT status FROM finding WHERE run_id = ?", second.run_id)} == {"stale_input"}
