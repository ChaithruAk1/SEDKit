"""Ops AI evals: triage, recurring and risks runs scored against the ground truth, scores only, eval.json written and
compared with the previous eval of the same skill, and refusal without ground truth."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sed.ai.runs import finish_run
from sed.errors import PreconditionFailed
from sed.modules.ops.evals import evaluate_recurring, evaluate_risks, evaluate_triage
from tests.fake_agent.flow import finished_run
from tests.modules.ops.ai.test_recurring import _start as start_recurring
from tests.modules.ops.ai.test_recurring import _truth as recurring_truth
from tests.modules.ops.ai.test_recurring import _truth_clusters, _write_and_ingest, labelled  # noqa: F401
from tests.modules.ops.ai.test_risks import _ingest as ingest_risks
from tests.modules.ops.ai.test_risks import _start as start_risks
from tests.modules.ops.ai.test_risks import _truth_findings


def test_triage_eval_scores_only_and_compares_with_the_previous_eval(ops_profile_rw):
    paths = ops_profile_rw.paths
    first, _ = finished_run(paths, scope="period:2026-08", limit=200, batch_size=100)
    result = evaluate_triage(paths, first.run_id)
    assert result["scored"] > 50 and 0 <= result["category"]["rate"] <= 1
    assert {"min_scored", "category_accuracy"} <= set(result["checks"]) and "previous" not in result
    text = json.dumps(result)
    assert "INC" not in text and "incident:" not in text
    saved = json.loads((paths.runs / first.run_id / "eval.json").read_text(encoding="utf-8"))
    assert saved["checks"] == result["checks"]
    second, _ = finished_run(paths, scope="period:2026-07", limit=100, batch_size=100)
    again = evaluate_triage(paths, second.run_id)
    assert again["previous"]["run_id"] == first.run_id


def test_recurring_eval_passes_for_the_truth_clusters(labelled):  # noqa: F811
    profile, _ = labelled
    paths = profile.paths
    plan, lines = start_recurring(paths)
    _write_and_ingest(paths, plan, _truth_clusters(lines, recurring_truth(profile)))
    finish_run(paths, plan.run_id)
    result = evaluate_recurring(paths, plan.run_id)
    assert result["passed"], result["checks"]
    assert result["patterns"]["P1"]["names_change"] and result["patterns"]["P5"]["periodicity"] == "monthly"
    with pytest.raises(PreconditionFailed, match="not sed-triage"):
        evaluate_triage(paths, plan.run_id)


def test_risks_eval_and_missing_ground_truth(ops_profile_rw):
    paths = ops_profile_rw.paths
    plan, lines = start_risks(paths)
    findings = _truth_findings(ops_profile_rw, lines)
    ingest_risks(paths, plan, findings)
    finish_run(paths, plan.run_id)
    result = evaluate_risks(paths, plan.run_id)
    assert result["checks"]["P2_vendor_risk"] and result["checks"]["noisy_vendor_control_quiet"]
    assert result["p4_auto_renew_covered"] >= 1
    for name in ("patterns.json", "ticket_truth.csv"):
        Path(paths.ground_truth / name).unlink()
    with pytest.raises(PreconditionFailed, match="No ops ground truth"):
        evaluate_risks(paths, plan.run_id)
