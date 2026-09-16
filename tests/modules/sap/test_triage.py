"""SAP subcategories in AI triage through the ops.triage extension point: packet context for SAP tickets, the context
file, ingest rules for SAP subcategories, `--only sap`, the skill hash, taxonomy overrides and the eval scorer."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from sed import db
from sed.ai.ingest import ingest_file
from sed.ai.runs import finish_run
from sed.errors import PreconditionFailed, ValidationFailed
from sed.modules.ops.ai.extensions import POINT, load_extensions
from sed.modules.ops.ai.taxonomy import load_taxonomy
from sed.modules.ops.ai.triage import TriageBatchHandler
from sed.modules.sap import synth
from sed.modules.sap.evals import evaluate_triage
from sed.modules.sap.scope import load_scope
from sed.modules.sap.taxonomy import load_sap_taxonomy
from tests.fake_agent.flow import context_text, fake_output, query, review_and_approve, start
from tests.fake_agent.triage import parse_taxonomy, write_output
from tests.modules.sap.conftest import truth_output, write_sap_config


def _rows(plan: Any) -> list[dict[str, Any]]:
    rows = []
    for batch in plan.inputs:
        rows += [json.loads(line) for line in Path(batch.packet).read_text(encoding="utf-8").splitlines()]
    return rows


def _numbers(paths: Any, run_id: str) -> dict[str, str]:
    """ref|batch -> ticket number (test-only lookup)."""
    return {
        f"{r[0]}|{r[1]}": r[2]
        for r in query(
            paths,
            "SELECT i.ref, i.batch_id, t.number FROM ai_batch_item i JOIN ticket t ON t.ticket_id = i.item_id "
            "WHERE i.batch_id LIKE ?",
            f"{run_id}/%",
        )
    }


def test_the_sap_module_contributes_to_the_ops_triage_point(sap_profile):
    from sed import modules

    assert [key for key, _ in modules.extensions(POINT, sap_profile.paths)] == ["sap"]
    (ext,) = load_extensions(sap_profile.paths, load_taxonomy(sap_profile.paths))
    assert ext.key == "sap" and len(ext.subcategories) == 12
    assert ext.by_category() == {
        "integration": ("sap_idoc_error", "sap_interface"),
        "batch_job": ("sap_job_failure", "sap_month_end_close"),
        "access": ("sap_authorisation", "sap_role_request"),
        "data_quality": ("sap_master_data",),
        "defect": ("sap_custom_code_dump", "sap_transport_issue"),
        "infrastructure": ("sap_basis",),
        "performance": ("sap_performance",),
        "how_to": ("sap_how_to",),
    }


def test_sap_lines_carry_area_and_landscape_and_other_lines_do_not(sap_profile_rw, sap_truth):
    paths = sap_profile_rw.paths
    plan = start(paths, scope="period:2026-08", batch_size=200)
    rows = _rows(plan)
    numbers = _numbers(paths, plan.run_id)
    refs = {
        f"{r['ref']}|{plan.run_id}/{b.batch}": r
        for b in plan.inputs
        for r in map(json.loads, Path(b.packet).read_text(encoding="utf-8").splitlines())
    }
    scope = load_scope(paths)
    sap_lines = 0
    for key, row in refs.items():
        truth = sap_truth["tickets"].get(numbers[key])
        if truth is None:
            assert "sap" not in row
            continue
        sap_lines += 1
        expected: dict[str, str] = {}
        if truth["area"] != "unassigned":
            expected["area"] = scope.area_labels[truth["area"]]
        expected["landscape"] = scope.landscape_labels[truth["landscape"]]
        assert row["sap"] == expected
    assert sap_lines > 50 and len(rows) > sap_lines  # SAP and ops tickets in one run
    context = context_text(plan)
    assert "## SAP tickets (`sap` field)" in context and "`sap.area`" in context and "`sap.landscape`" in context
    assert "  - SAP tickets only (lines with a `sap` field): `sap_idoc_error`, `sap_interface`" in context
    assert parse_taxonomy(context) == {c.code: list(c.subcategories) for c in load_taxonomy(paths).categories.values()}


def test_only_sap_selects_sap_tickets(sap_profile_rw, sap_truth):
    paths = sap_profile_rw.paths
    plan = start(paths, scope="period:2026-08", only="sap", batch_size=200)
    rows = _rows(plan)
    numbers = set(_numbers(paths, plan.run_id).values())
    assert rows and all("sap" in r for r in rows)
    assert numbers <= set(sap_truth["tickets"])
    with pytest.raises(ValidationFailed, match="not an enabled triage extension"):
        start(paths, scope="period:2026-07", only="hr")


def test_runs_without_sap_tickets_have_no_sap_section(sap_profile_rw):
    paths = sap_profile_rw.paths
    write_sap_config(paths, "scope.yaml", {"groups": [], "categories": [], "custom_fields": []})
    plan = start(paths, scope="period:2026-08", limit=40, batch_size=40)
    assert all("sap" not in r for r in _rows(plan))
    assert "SAP" not in context_text(plan)
    with pytest.raises(ValidationFailed) as exc:  # without SAP lines, SAP subcategories are refused
        _ingest_with(paths, plan, "integration", "sap_idoc_error")
    assert "only for lines with a `sap` field" in exc.value.details[0]["msg"]


def _ingest_with(paths: Any, plan: Any, category: str, sub: str | None, *, line: int = 0) -> dict[str, Any]:
    batch = plan.inputs[0]
    document = json.loads(fake_output(plan, batch).read_text(encoding="utf-8"))
    document["items"][line].update(am_category=category, am_subcategory=sub)
    write_output(batch.out, document)
    return ingest_file(paths, plan.run_id, batch.out)


def test_ingest_accepts_sap_subcategories_only_on_sap_lines_under_their_category(sap_profile_rw):
    paths = sap_profile_rw.paths
    plan = start(paths, scope="period:2026-08", batch_size=300)
    rows = [json.loads(line) for line in Path(plan.inputs[0].packet).read_text(encoding="utf-8").splitlines()]
    sap_line = next(i for i, r in enumerate(rows) if "sap" in r)
    ops_line = next(i for i, r in enumerate(rows) if "sap" not in r)

    with pytest.raises(ValidationFailed) as exc:
        _ingest_with(paths, plan, "access", "sap_idoc_error", line=sap_line)
    assert exc.value.details[0]["msg"] == "'sap_idoc_error' is a SAP subcategory of 'integration', not of 'access'"
    with pytest.raises(ValidationFailed) as exc:
        _ingest_with(paths, plan, "integration", "sap_idoc_error", line=ops_line)
    assert exc.value.details[0]["msg"] == "'sap_idoc_error' is a SAP subcategory: only for lines with a `sap` field"
    with pytest.raises(ValidationFailed) as exc:
        _ingest_with(paths, plan, "integration", "made_up", line=sap_line)
    assert "sap_idoc_error, sap_interface" in exc.value.details[0]["msg"]  # the allowed list names SAP codes

    result = _ingest_with(paths, plan, "integration", "sap_idoc_error", line=sap_line)
    assert result["status"] == "ingested"
    (label,) = query(
        paths,
        "SELECT l.am_subcategory FROM ai_ticket_label l JOIN ai_batch_item i ON i.item_id = l.ticket_id "
        "AND i.stage = l.stage WHERE l.run_id = ? AND i.batch_id = ? AND i.ref = ?",
        plan.run_id,
        f"{plan.run_id}/{plan.inputs[0].batch}",
        rows[sap_line]["ref"],
    )
    assert label[0] == "sap_idoc_error"


def test_skill_hash_covers_the_sap_taxonomy_and_not_an_ops_only_install(sap_profile_rw):
    paths = sap_profile_rw.paths
    handler = TriageBatchHandler()
    before = handler.config_inputs(paths)
    assert set(before["extensions"]) == {"sap"}
    data = yaml.safe_load(Path("config/sap/taxonomy.yaml").read_text(encoding="utf-8"))
    data["guide"] = data["guide"] + "- A new rule.\n"
    write_sap_config(paths, "taxonomy.yaml", data)
    assert handler.config_inputs(paths)["extensions"] != before["extensions"]
    (paths.config / "modules.yaml").write_text("enabled: [ops]\n", encoding="utf-8")
    assert "extensions" not in handler.config_inputs(paths)


def test_sap_subcategories_of_a_removed_ops_category_are_left_out(sap_profile_rw):
    from sed.modules.sap.doctor import checks

    paths = sap_profile_rw.paths
    override = paths.config / "ops" / "taxonomy.yaml"
    override.parent.mkdir(parents=True, exist_ok=True)
    override.write_text("categories:\n  how_to: ~delete\n", encoding="utf-8")
    (ext,) = load_extensions(paths, load_taxonomy(paths))
    assert "sap_how_to" not in {s.code for s in ext.subcategories} and len(ext.subcategories) == 11
    plan = start(paths, scope="period:2026-08", only="sap", limit=20, batch_size=20)
    assert "- `sap_how_to` (`how_to`)" not in context_text(plan)  # the guide text may still mention it
    status = {c.name: (c.status, c.detail) for c in checks(paths)}["sap.taxonomy_categories_known"]
    assert status == ("warn", status[1]) and "sap_how_to (how_to)" in status[1]


def test_invalid_sap_taxonomy_is_rejected(sap_profile_rw):
    paths = sap_profile_rw.paths
    write_sap_config(
        paths, "taxonomy.yaml", {"subcategories": [{"code": "idoc", "category": "integration", "description": "x"}]}
    )
    with pytest.raises(ValidationFailed, match=r"Invalid sap/taxonomy\.yaml"):
        load_sap_taxonomy(paths)
    dup = {"code": "sap_x", "category": "integration", "description": "x"}
    write_sap_config(paths, "taxonomy.yaml", {"subcategories": [dup, dup]})
    with pytest.raises(ValidationFailed):
        load_sap_taxonomy(paths)
    write_sap_config(
        paths, "taxonomy.yaml", {"subcategories": [{**dup, "code": "sap_timeout", "category": "performance"}]}
    )
    override = paths.config / "ops" / "taxonomy.yaml"
    override.parent.mkdir(parents=True, exist_ok=True)
    override.write_text("categories:\n  performance:\n    subcategories: [sap_timeout]\n", encoding="utf-8")
    with pytest.raises(ValidationFailed) as exc:
        load_extensions(paths, load_taxonomy(paths))
    assert exc.value.details == ["subcategory 'sap_timeout' is already a subcategory of 'performance'"]


def test_synthetic_templates_use_exactly_the_sap_taxonomy():
    taxonomy = load_sap_taxonomy(None)
    configured = {(s.category, s.code) for s in taxonomy.subcategories}
    templates = [t for _, _, _, ts in synth.AREAS.values() for t in ts] + [synth.MONTH_END, synth.AFTER_IMPORT]
    assert {(t.category, t.subcategory) for t in templates} == configured
    assert {t.misfiled for t in templates} == {"none", "request"}


# -- eval ------------------------------------------------------------------------------------------------------------


def _truth_run(paths: Any, truth: dict[str, Any], *, wrong_every: int = 0) -> Any:
    plan = start(paths, scope="period:2026-08", only="sap", batch_size=100)
    for batch in plan.inputs:
        ingest_file(paths, plan.run_id, truth_output(paths, plan, batch, truth, wrong_every=wrong_every))
    finish_run(paths, plan.run_id)
    return plan


def test_eval_scores_a_perfect_run_and_passes(sap_profile_rw, sap_truth):
    paths = sap_profile_rw.paths
    plan = _truth_run(paths, sap_truth)
    result = evaluate_triage(paths, plan.run_id)
    assert result["scored"] == result["labels"] > 50
    assert result["category"]["accuracy"] == result["subcategory"]["accuracy"] == 1.0
    assert result["misfiled_as"]["accuracy"] == 1.0 and result["passed"] and result["confusions"] == []
    assert result["sap_subcategory_share"] == 1.0
    assert {"sap_month_end_close", "sap_transport_issue"} <= set(result["by_subcategory"])
    text = json.dumps(result)
    assert "INC" not in text and "Billing document" not in text  # scores only


def test_eval_counts_mistakes_and_fails_below_the_threshold(sap_profile_rw, sap_truth):
    paths = sap_profile_rw.paths
    plan = _truth_run(paths, sap_truth, wrong_every=4)
    result = evaluate_triage(paths, plan.run_id)
    n = result["scored"]
    assert result["category"]["accuracy"] == 1.0
    assert result["subcategory"]["accuracy"] == round((n - n // 4) / n, 4)
    assert result["subcategory"]["ci_low"] < result["subcategory"]["accuracy"] < result["subcategory"]["ci_high"]
    assert not result["passed"] and result["checks"]["min_subcategory_accuracy"] is False
    assert sum(c["count"] for c in result["confusions"]) <= n // 4 and result["confusions"][0]["labelled"].endswith(
        "/null"
    )


def test_eval_needs_synthetic_ground_truth_and_a_triage_run(sap_profile_rw, tmp_path):
    paths = sap_profile_rw.paths
    with pytest.raises(PreconditionFailed, match="Unknown AI run"):
        evaluate_triage(paths, "20260901T000000-sed-triage-batch-0000")
    truth = paths.ground_truth / "sap" / "ticket_truth.csv"
    truth.unlink()
    with pytest.raises(PreconditionFailed, match="No SAP ground truth"):
        evaluate_triage(paths, "any")


def test_eval_triage_cli_prints_json(sap_profile_rw, sap_truth, monkeypatch):
    from typer.testing import CliRunner

    from sed.cli import app

    paths = sap_profile_rw.paths
    plan = _truth_run(paths, sap_truth)
    monkeypatch.setenv("SED_DATA_ROOT", str(sap_profile_rw.root))
    result = CliRunner().invoke(app, ["sap", "eval-triage", plan.run_id, "--profile", paths.profile, "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] and payload["passed"] and payload["run_id"] == plan.run_id


def test_approved_sap_labels_reach_the_breakdown(sap_profile_rw, sap_truth, tmp_path):
    from sed.modules.sap.queries import ai_labels

    paths = sap_profile_rw.paths
    plan = _truth_run(paths, sap_truth)
    conn = db.connect(paths.db, readonly=True)
    try:
        scope = load_scope(paths).resolve(conn)
        taxonomy = load_sap_taxonomy(paths)
        args = (conn, scope, taxonomy, "2026-08-01T00:00:00Z", "2026-09-01T00:00:00Z")
        before = ai_labels.breakdown(*args)
        drafts = ai_labels.breakdown(*args, include_drafts=True)
    finally:
        conn.close()
    assert before["labelled"] == 0 and before["rows"] == [] and before["tickets"] > 50
    assert drafts["labelled"] > 0 and drafts["unapproved_labels"] == drafts["labelled"]
    assert drafts["sample_accuracy"] is None

    review_and_approve(paths, plan.run_id, tmp_path, incorrect_every=0)
    conn = db.connect(paths.db, readonly=True)
    try:
        after = ai_labels.breakdown(conn, *args[1:])
    finally:
        conn.close()
    assert after["labelled"] > 0 and after["unapproved_labels"] == 0 and after["sample_accuracy"] == 1.0
    assert sum(r["tickets"] for r in after["rows"]) == after["labelled"]
    assert all(r["sap"] and r["label"] for r in after["rows"])
    assert {r["subcategory"] for r in after["rows"]} >= {"sap_month_end_close", "sap_transport_issue"}
    assert [r["run_id"] for r in after["runs"]] == [plan.run_id]


def test_approved_sap_labels_reach_the_dashboard_and_the_report(sap_profile_rw, sap_truth, tmp_path):
    from python_calamine import CalamineWorkbook

    from sed.modules.sap import api_models as m
    from sed.reports.build import build_report
    from tests.fixtures.api import api_client

    paths = sap_profile_rw.paths
    client = api_client(paths)
    empty = client.get("/api/sap/l3").json()["ai_subcategories"]
    assert empty["labelled"] == 0 and empty["rows"] == [] and empty["runs"] == [] and empty["tickets"] > 0

    plan = _truth_run(paths, sap_truth)
    drafts = client.get("/api/sap/l3", params={"include_drafts": "true"}).json()["ai_subcategories"]
    assert drafts["include_drafts"] and drafts["unapproved_labels"] == drafts["labelled"] > 0
    review_and_approve(paths, plan.run_id, tmp_path, incorrect_every=0)
    body = client.get("/api/sap/l3").json()
    m.SapL3Out.model_validate(body)
    ai = body["ai_subcategories"]
    assert ai["labelled"] > 0 and ai["sample_accuracy"] == 1.0 and ai["runs"][0]["run_id"] == plan.run_id
    assert sum(r["tickets"] for r in ai["rows"]) == ai["labelled"]
    ewm = client.get("/api/sap/l3", params={"area": "ewm"}).json()["ai_subcategories"]
    assert 0 < ewm["labelled"] < ai["labelled"]

    approved = build_report(paths, "sap-weekly", "2026-W35", None, "approved")
    files = {a["format"]: Path(a["path"]) for a in approved["artifacts"]}
    book = CalamineWorkbook.from_path(str(files["xlsx"]))
    assert "AI subcategories" in book.sheet_names
    sheet = "\n".join(str(v) for row in book.get_sheet_by_name("AI subcategories").to_python() for v in row)
    assert "SAP subcategories (AI-assisted, sample accuracy 100%, 95% CI" in sheet and "Transport issue" in sheet
    md = files["md"].read_text(encoding="utf-8")
    assert "**SAP subcategories (AI-assisted, sample accuracy 100.0%):**" in md

    none = build_report(paths, "sap-weekly", "2026-W35", None, "none")
    files = {a["format"]: Path(a["path"]) for a in none["artifacts"]}
    rows = CalamineWorkbook.from_path(str(files["xlsx"])).get_sheet_by_name("AI subcategories").to_python()
    sheet = "\n".join(str(v) for row in rows for v in row)
    assert "AI-derived content excluded (--ai none)." in sheet and "Transport issue" not in sheet
    assert "AI-assisted" not in files["md"].read_text(encoding="utf-8")
