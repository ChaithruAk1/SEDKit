"""AI walking skeleton on the deterministic ops profile.

100 synthetic tickets go through start-run -> fake agent -> ingest x2 -> finish-run -> verdicts -> approve-run, and the
weekly 2026-W35 workbook then shows AI categories with a sample-accuracy provenance line. The same flow runs once more
through `python -m sed ... --json` subprocesses (one JSON line per call, exit codes 0/2/4).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import openpyxl

from sed import db
from sed.ai.provenance import run_provenance
from sed.ai.runs import finish_run
from sed.reports.build import build_report
from sed.reports.snapshot import format_provenance_line
from tests.conftest import REPO
from tests.fake_agent.flow import SKILL, fake_output, label_all, query, review_and_approve, start
from tests.fake_agent.verdicts import verdicts_for

AI_FACT_KEYS = {
    "ai.category.labelled_pct",
    "ai.category.sample_accuracy_pct",
    "ai.category.sample_ci_low_pct",
    "ai.category.sample_ci_high_pct",
}
EXCLUDED_NOTE = "AI-derived content excluded (--ai none)."


def _sheet_rows(path: Path, sheet: str) -> list[tuple]:
    wb = openpyxl.load_workbook(path, read_only=True)
    try:
        return [tuple(row) for row in wb[sheet].iter_rows(values_only=True)]
    finally:
        wb.close()


def _category_rows(path: Path) -> list[tuple]:
    rows = _sheet_rows(path, "Categories")
    header = next(i for i, r in enumerate(rows) if r and r[0] == "ServiceNow category")
    return [r for r in rows[header + 1 :] if r and r[0] is not None]


def _cells(rows: list[tuple]) -> list[str]:
    return [str(c) for r in rows for c in r if c is not None]


def _artifact_runs(paths, fmt_path: str, ai_mode: str) -> list[str]:
    rows = query(paths, "SELECT ai_run_ids_json FROM report_artifact WHERE path = ? AND ai_mode = ?", fmt_path, ai_mode)
    assert len(rows) == 1
    return json.loads(rows[0][0])


def _provenance_line(paths, run_id: str) -> str:
    conn = db.connect(paths.db, readonly=True)
    try:
        runs = run_provenance(conn, [run_id])
    finally:
        conn.close()
    return format_provenance_line(runs[0])


def test_walking_skeleton_in_process(ops_profile_rw, tmp_path):
    paths = ops_profile_rw.paths
    plan = start(paths, scope="period:2026-08", limit=100, batch_size=50, invoked_via="headless")
    assert plan.plan["items"] == 100 and [b.batch for b in plan.inputs] == ["batch_0001", "batch_0002"]
    ingested = label_all(paths, plan)
    assert [r["status"] for r in ingested] == ["ingested", "ingested"] and sum(r["items"] for r in ingested) == 100
    summary = finish_run(paths, plan.run_id)
    assert summary.status == "completed" and summary.failed_batches == [] and summary.counts["labelled"] == 100
    approval = review_and_approve(paths, plan.run_id, tmp_path)
    assert approval["status"] == "approved" and approval["sample_n"] == 30

    result = build_report(paths, "weekly", "2026-W35", ["xlsx"], "approved")
    assert result["ai_runs"] == [plan.run_id]
    workbook = Path(result["artifacts"][0]["path"])
    categories = _category_rows(workbook)
    assert any(r[1] not in (None, "(not labelled)") for r in categories), categories
    run = query(paths, "SELECT skill_hash FROM ai_run WHERE run_id = ?", plan.run_id)[0]
    line = _provenance_line(paths, plan.run_id)
    assert run["skill_hash"][:12] in line and "sample accuracy" in line and "n=30" in line
    assert line in _cells(_sheet_rows(workbook, "Provenance"))
    definitions = {r[0] for r in _sheet_rows(workbook, "Definitions") if r and r[0]}
    assert definitions >= AI_FACT_KEYS
    assert _artifact_runs(paths, str(workbook), "approved") == [plan.run_id]
    snapshot = query(paths, "SELECT facts_json FROM report_snapshot WHERE snapshot_id = ?", result["snapshot_id"])[0]
    facts = json.loads(snapshot[0])
    assert facts["ai.category.sample_accuracy_pct"]["value"] == round(100 * approval["sample_accuracy"], 1)
    assert 0 < facts["ai.category.labelled_pct"]["value"] <= 100

    excluded = build_report(paths, "weekly", "2026-W35", ["xlsx"], "none")
    assert excluded["ai_runs"] == []
    workbook = Path(excluded["artifacts"][0]["path"])
    assert EXCLUDED_NOTE in _cells(_sheet_rows(workbook, "Categories"))
    definitions = {r[0] for r in _sheet_rows(workbook, "Definitions") if r and r[0]}
    assert not {d for d in definitions if str(d).startswith("ai.category.")}
    provenance = _cells(_sheet_rows(workbook, "Provenance"))
    assert line not in provenance and "excluded (--ai none)" in provenance
    assert _artifact_runs(paths, str(workbook), "none") == []


def _sed(data_dir: Path, *args: str, expect: int = 0) -> dict:
    argv = [sys.executable, "-m", "sed", *args, "--data-dir", str(data_dir), "--profile", "synthetic", "--json"]
    proc = subprocess.run(argv, cwd=REPO, capture_output=True, text=True, encoding="utf-8", check=False)
    assert proc.returncode == expect, (args, proc.stdout, proc.stderr)
    lines = proc.stdout.strip().splitlines()
    assert len(lines) == 1, (args, proc.stdout)
    payload = json.loads(lines[0])
    assert payload["ok"] is (expect == 0)
    return payload


def test_walking_skeleton_via_cli_subprocesses(ops_profile_rw, tmp_path):
    data_dir = ops_profile_rw.paths.data_dir
    plan_json = _sed(
        data_dir,
        *("ai", "start-run", SKILL, "--scope", "period:2026-08", "--limit", "100", "--batch-size", "50"),
        *("--invoked-via", "headless", "--claude-version", "0.0.0 (test)"),
    )
    run_id = plan_json["run_id"]
    assert plan_json["plan"]["items"] == 100 and len(plan_json["inputs"]) == 2

    from sed.ai.contract import RunPlan

    plan = RunPlan.model_validate({k: v for k, v in plan_json.items() if k != "ok"})
    first, second = plan.inputs
    rejected = _sed(data_dir, "ai", "ingest", run_id, str(fake_output(plan, first, mode="bad_category")), expect=2)
    assert rejected["error"]["kind"] == "validation" and rejected["error"]["details"][0]["ref"] == "T001"
    assert _sed(data_dir, "ai", "ingest", run_id, str(fake_output(plan, first)))["status"] == "ingested"
    backslashed = str(fake_output(plan, second)).replace("/", "\\")
    assert _sed(data_dir, "ai", "ingest", run_id, backslashed)["status"] == "ingested"
    assert _sed(data_dir, "ai", "ingest", run_id, first.out)["status"] == "unchanged"

    finished = _sed(data_dir, "ai", "finish-run", run_id)
    assert finished["status"] == "completed" and finished["counts"]["claims_released"] == 100
    assert finished["review"]["next"] == f"uv run sed review sample {run_id} --profile synthetic --json"
    assert _sed(data_dir, "review", "approve-run", run_id, expect=4)["error"]["kind"] == "precondition"

    template = tmp_path / "verdicts.json"
    cards = _sed(data_dir, "review", "sample", run_id, "--template", str(template))
    assert all(v is None for v in json.loads(template.read_text(encoding="utf-8")).values())
    template.write_text(json.dumps(verdicts_for(cards)), encoding="utf-8")
    recorded = _sed(data_dir, "review", "verdicts", run_id, "--file", str(template))
    assert recorded["random_missing"] == 0
    approved = _sed(data_dir, "review", "approve-run", run_id, "--note", "cli walking skeleton")
    assert approved["status"] == "approved" and approved["sample_n"] == 30

    runs = _sed(data_dir, "ai", "runs", "--skill", SKILL)["runs"]
    assert runs[0]["run_id"] == run_id and runs[0]["status"] == "approved"
    assert runs[0]["claude_version"] == "0.0.0 (test)" and runs[0]["invoked_via"] == "headless"
    built = _sed(data_dir, "report", "build", "weekly", "--period", "2026-W35", "--format", "xlsx", "--ai", "approved")
    assert built["ai_runs"] == [run_id]
    assert any(r[1] not in (None, "(not labelled)") for r in _category_rows(Path(built["artifacts"][0]["path"])))
    assert _sed(data_dir, "ai", "finish-run", run_id, expect=4)["error"]["kind"] == "precondition"
