"""Snapshot contract: request validation, as-of clamping, AI render views, provenance and module enablement."""

from __future__ import annotations

import json
import sqlite3
from datetime import date

import pytest

from sed import db
from sed.errors import PreconditionFailed, ValidationFailed
from sed.reports.build import build_report
from sed.reports.snapshot import Snapshot, create_snapshot, fact, format_provenance_line, render_view, table


def _run(**overrides) -> dict:
    run = {
        "run_id": "20260901T100000-triage-batch-ab12",
        "skill": "sed-triage-batch",
        "skill_hash": "0123456789abcdef" * 2,
        "status": "approved",
        "model_reported": "model-x",
        "reviewed_by": "reviewer",
        "reviewed_at": "2026-09-02T08:00:00Z",
        "sample_n": 30,
        "sample_accuracy": 0.88,
        "sample_ci_low": 0.791,
        "sample_ci_high": 0.942,
        "used_for": ["category_breakdown"],
    }
    return {**run, **overrides}


def test_format_provenance_line():
    line = format_provenance_line(_run())
    assert line.startswith("sed-triage-batch 0123456789ab run 20260901T100000-triage-batch-ab12")
    assert "approved by reviewer" in line and "88.0% (79.1–94.2%), n=30" in line
    assert "sample accuracy n/a" in format_provenance_line(_run(sample_accuracy=None))


def _hand_snapshot() -> Snapshot:
    return Snapshot(
        snapshot_id="snap-x",
        report_key="weekly",
        period="2026-W35",
        vendor_id=None,
        as_of="2026-08-31",
        data_class="synthetic",
        reporting_tz="Europe/Paris",
        sla_source="made_sla",
        facts={"inc.opened": fact(10, "count", "Opened"), "ai.category.sample_accuracy_pct": fact(88.0, "pct", "Acc")},
        tables={
            "top_apps": table("Top", [("app", "App", "text")], [{"app": "A"}]),
            "category_breakdown": table("Cat", [("am_category", "AI", "text")], [{"am_category": "access"}]),
        },
        freshness=[],
        input_batches=[],
        sha256="0" * 64,
        created_at="2026-09-01T00:00:00Z",
        git_commit=None,
        ai_runs=[_run()],
        ai_derived_tables=["category_breakdown"],
        ai_derived_facts=["ai.category.sample_accuracy_pct"],
    )


def test_render_view_none_removes_ai_facts_and_tables():
    snap = _hand_snapshot()
    none = render_view(snap, "none")
    assert "category_breakdown" not in none.tables and "ai.category.sample_accuracy_pct" not in none.facts
    assert none.excluded_tables == ["category_breakdown"] and none.ai_runs == [] and none.note
    approved = render_view(snap, "approved")
    assert "category_breakdown" in approved.tables and approved.ai_runs and approved.note is None


def test_request_validation_exits_2(ops_profile_rw):
    paths = ops_profile_rw.paths
    conn = db.connect(paths.db)
    try:
        with pytest.raises(ValidationFailed, match="week"):
            create_snapshot(conn, paths, "weekly", "2026-08")
        with pytest.raises(ValidationFailed, match="does not take --vendor"):
            create_snapshot(conn, paths, "weekly", "2026-W35", ops_profile_rw.ids["vendor_p2"])
        with pytest.raises(ValidationFailed, match="needs --vendor"):
            create_snapshot(conn, paths, "vendor", "2026-Q3")
        with pytest.raises(ValidationFailed, match="Unknown vendor"):
            create_snapshot(conn, paths, "vendor", "2026-Q3", "V-NOPE")
    finally:
        conn.close()


def test_open_period_is_clamped_to_the_data_date(ops_profile_rw):
    paths = ops_profile_rw.paths
    conn = db.connect(paths.db)
    try:
        snap = create_snapshot(conn, paths, "weekly", "2026-W36")
        assert snap.as_of == "2026-09-01" and snap.period_end == "2026-09-07" and snap.data_as_of == "2026-09-01"
        assert db.get_meta(conn, "ops.rule_findings_as_of") == "2026-09-01"
        row = conn.execute("SELECT provenance_json FROM report_snapshot WHERE snapshot_id = ?", (snap.snapshot_id,))
        provenance = json.loads(row.fetchone()[0])
        assert provenance["period_end"] == "2026-09-07" and provenance["ai_derived_tables"] == ["category_breakdown"]
    finally:
        conn.close()


def test_closed_period_keeps_m1_as_of(ops_profile_rw):
    paths = ops_profile_rw.paths
    conn = db.connect(paths.db)
    try:
        snap = create_snapshot(conn, paths, "weekly", "2026-W35")
    finally:
        conn.close()
    assert date.fromisoformat(snap.as_of) == date(2026, 8, 31)


def test_build_records_ai_runs_and_excludes_ai_sheet_content(ops_profile_rw):
    paths = ops_profile_rw.paths
    result = build_report(paths, "weekly", "2026-W35", ["xlsx", "md"], "none")
    assert result["ai_runs"] == [] and {a["format"] for a in result["artifacts"]} == {"xlsx", "md"}
    conn = sqlite3.connect(str(paths.db))
    try:
        stored = conn.execute("SELECT DISTINCT ai_run_ids_json FROM report_artifact").fetchall()
    finally:
        conn.close()
    assert stored == [("[]",)]


def test_disabled_module_refuses_report_build(ops_profile_rw):
    paths = ops_profile_rw.paths
    (paths.config / "modules.yaml").write_text("enabled: []\n", encoding="utf-8")
    with pytest.raises(PreconditionFailed):
        build_report(paths, "weekly", "2026-W35", ["xlsx"], "none")
