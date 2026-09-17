"""sed-assess-risks on the ops profile: one packet of rule findings, vendors and contracts in the horizon; findings with
signals, annotations of rule findings, validation of subjects, signals and tokens, and carry-forward across runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from sed.ai.contract import StartParams
from sed.ai.ingest import ingest_file
from sed.ai.review import review_findings
from sed.ai.runs import finish_run, start_run
from sed.errors import ValidationFailed
from tests.fake_agent.flow import query
from tests.fake_agent.triage import write_output

SKILL = "sed-assess-risks"


def _start(paths: Any):
    plan = start_run(paths, SKILL, StartParams())
    assert plan.run_id and len(plan.inputs) == 1
    lines = [json.loads(line) for line in Path(plan.inputs[0].packet).read_text(encoding="utf-8").splitlines()]
    return plan, lines


def _ingest(paths: Any, plan: Any, findings: list[dict]) -> dict:
    write_output(plan.inputs[0].out, {"meta": {"model": "fake"}, "findings": findings})
    return ingest_file(paths, plan.run_id, plan.inputs[0].out)


def _truth_findings(profile: Any, lines: list[dict]) -> list[dict]:
    patterns = json.loads((profile.ground_truth / "patterns.json").read_text(encoding="utf-8"))
    vendor = next(line for line in lines if line["type"] == "vendor" and line["vendor"] == patterns["P2"]["vendor"])
    delta = f"vendor.{vendor['subject_id']}.sla_delta_pp"
    value = f"vendor.{vendor['subject_id']}.annual_contract_value_base"
    rule = next(
        line
        for line in lines
        if line["type"] == "rule_finding" and line["stable_key"].startswith("renewal_risk:notice")
    )
    notice_fact = next(k for k in rule["facts"] if k.endswith(".days_to_notice"))
    return [
        {
            "kind": "vendor_risk", "subject_type": "vendor", "subject_id": vendor["subject_id"], "severity": "high",
            "title": "Managed service degrading", "recommendation": "Open a service review.", "decision_due": None,
            "body_md": f"SLA moved by {{{{f:{delta}}}}} points on a portfolio worth {{{{f:{value}}}}}.",
            "signals": [{"fact_key": delta}, {"fact_key": value}], "annotates_rule_stable_key": None,
            "confidence": 0.8,
        },
        {
            "kind": "renewal_risk", "subject_type": rule["subject_type"], "subject_id": rule["subject_id"],
            "severity": "critical", "title": "Auto-renewal needs notice now", "recommendation": "Give notice.",
            "decision_due": "2026-09-15", "body_md": f"Notice due in {{{{f:{notice_fact}}}}} days.",
            "signals": [{"fact_key": notice_fact}], "annotates_rule_stable_key": rule["stable_key"], "confidence": 0.9,
        },
    ]  # fmt: skip


def test_packet_and_truth_findings(ops_profile_rw):
    paths = ops_profile_rw.paths
    plan, lines = _start(paths)
    assert {line["type"] for line in lines} == {"rule_finding", "vendor", "contract"}
    patterns = json.loads((ops_profile_rw.ground_truth / "patterns.json").read_text(encoding="utf-8"))
    p2 = next(line for line in lines if line["type"] == "vendor" and line["vendor"] == patterns["P2"]["vendor"])
    assert p2["facts"][f"vendor.{p2['subject_id']}.sla_delta_pp"] < 0
    result = _ingest(paths, plan, _truth_findings(ops_profile_rw, lines))
    assert result["status"] == "ingested" and result["items"] == 2
    summary = finish_run(paths, plan.run_id)
    assert summary.counts["findings_new"] == 2
    rows = {r["kind"]: r for r in query(paths, "SELECT * FROM finding WHERE run_id = ?", plan.run_id)}
    vendor = rows["vendor_risk"]
    assert vendor["stable_key"] == f"vendor_risk:ai:vendor:{p2['subject_id']}" and vendor["status"] == "draft"
    payload = json.loads(vendor["payload_json"])
    assert payload["signals"] == payload["evidence"] and all(s["value_at_run"] is not None for s in payload["signals"])
    renewal = rows["renewal_risk"]
    annotated = json.loads(renewal["payload_json"])["annotates_rule_stable_key"]
    assert renewal["stable_key"] == f"{annotated}:ai" and json.loads(renewal["payload_json"])["decision_due"]
    rule_status = query(paths, "SELECT status FROM finding WHERE origin = 'rule' AND stable_key = ?", annotated)
    assert [r[0] for r in rule_status] == ["active"]  # the annotation never touches the rule finding


@pytest.mark.parametrize(
    "change, fragment",
    [
        ({"subject_id": "V-NOPE"}, "is not on the packet"),
        ({"signals": [{"fact_key": "vendor.nope.sla_delta_pp"}]}, "is not a fact key"),
        ({"body_md": "{{f:vendor.nope.x}} broken"}, "is not one of the signals"),
    ],
)
def test_invalid_findings_are_refused(ops_profile_rw, change, fragment):
    paths = ops_profile_rw.paths
    plan, lines = _start(paths)
    finding = _truth_findings(ops_profile_rw, lines)[0]
    finding.update(change)
    with pytest.raises(ValidationFailed) as exc:
        _ingest(paths, plan, [finding])
    assert any(fragment in d["msg"] for d in exc.value.details), exc.value.details


def test_annotation_must_match_its_rule_subject_and_findings_carry_forward(ops_profile_rw):
    paths = ops_profile_rw.paths
    plan, lines = _start(paths)
    vendor_finding, renewal = _truth_findings(ops_profile_rw, lines)
    wrong = dict(vendor_finding, annotates_rule_stable_key=renewal["annotates_rule_stable_key"])
    with pytest.raises(ValidationFailed) as exc:
        _ingest(paths, plan, [wrong])
    assert any("about another subject" in d["msg"] for d in exc.value.details)
    _ingest(paths, plan, [vendor_finding, renewal])
    finish_run(paths, plan.run_id)
    ids = [r[0] for r in query(paths, "SELECT finding_id FROM finding WHERE run_id = ?", plan.run_id)]
    review_findings(paths, ids, "approve", "tester")
    second, lines = _start(paths)
    _ingest(paths, second, _truth_findings(ops_profile_rw, lines))
    assert finish_run(paths, second.run_id).counts["findings_unchanged_approved"] == 2
