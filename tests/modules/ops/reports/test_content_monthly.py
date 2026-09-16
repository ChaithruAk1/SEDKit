"""Monthly Service Review content: exact facts against hand-written SQL, tables, and window/as-of semantics."""

from __future__ import annotations

import json
import sqlite3
import statistics
from datetime import date
from typing import Any

from typer.testing import CliRunner

from sed import db
from sed.analytics import published_rule_findings
from sed.reports.snapshot import Snapshot, create_snapshot

# August 2026 in Europe/Paris (CEST, UTC+2) as half-open UTC bounds.
AUG = ("2026-07-31T22:00:00Z", "2026-08-31T22:00:00Z")
JUL = ("2026-06-30T22:00:00Z", "2026-07-31T22:00:00Z")
AUG_TO_15 = ("2026-07-31T22:00:00Z", "2026-08-14T22:00:00Z")


def _snapshot(profile: Any, report: str, period: str, vendor: str | None = None) -> Snapshot:
    conn = db.connect(profile.paths.db)
    try:
        return create_snapshot(conn, profile.paths, report, period, vendor)
    finally:
        conn.close()


def _one(profile: Any, sql: str, params: tuple = ()) -> Any:
    conn = sqlite3.connect(str(profile.paths.db))
    try:
        return conn.execute(sql, params).fetchone()
    finally:
        conn.close()


def _rows(profile: Any, sql: str, params: tuple = ()) -> list[tuple]:
    conn = sqlite3.connect(str(profile.paths.db))
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _cli(*args: str) -> tuple[int, dict]:
    from sed.cli import app

    result = CliRunner().invoke(app, [*args, "--json"])
    lines = [ln for ln in result.stdout.strip().splitlines() if ln.strip()]
    return result.exit_code, json.loads(lines[-1])


def _sla_pct(profile: Any, bounds: tuple[str, str]) -> float | None:
    total, met = _one(
        profile,
        "SELECT COUNT(*), SUM(CASE WHEN COALESCE((SELECT MAX(s.has_breached) FROM task_sla s WHERE s.ticket_id = "
        "t.ticket_id AND s.sla_type = 'resolution'), CASE WHEN t.made_sla = 0 THEN 1 ELSE 0 END) = 0 "
        "THEN 1 ELSE 0 END) FROM ticket t WHERE t.kind = 'incident' AND t.resolved_at >= ? AND t.resolved_at < ?",
        bounds,
    )
    return round(100.0 * met / total, 2) if total else None


def _hand_facts(profile: Any, bounds: tuple[str, str]) -> dict[str, Any]:
    start, end = bounds
    opened = _one(
        profile, "SELECT COUNT(*) FROM ticket WHERE kind = 'incident' AND opened_at >= ? AND opened_at < ?", bounds
    )
    resolved = _one(
        profile, "SELECT COUNT(*) FROM ticket WHERE kind = 'incident' AND resolved_at >= ? AND resolved_at < ?", bounds
    )
    p1p2 = _one(
        profile,
        "SELECT COUNT(*) FROM ticket WHERE kind = 'incident' AND priority <= 2 AND opened_at >= ? AND opened_at < ?",
        bounds,
    )
    hours = [
        r[0]
        for r in _rows(
            profile,
            "SELECT (julianday(resolved_at) - julianday(opened_at)) * 24.0 FROM ticket WHERE kind = 'incident' "
            "AND resolved_at >= ? AND resolved_at < ? AND opened_at IS NOT NULL",
            bounds,
        )
    ]
    backlog = _one(
        profile,
        "SELECT COUNT(*) FROM ticket WHERE kind = 'incident' AND opened_at < ? AND (resolved_at IS NULL OR "
        "resolved_at >= ?) AND (closed_at IS NULL OR closed_at >= ?) AND stale_open = 0",
        (end, end, end),
    )
    chg = _one(
        profile,
        "SELECT COUNT(*), SUM(LOWER(COALESCE(close_code, '')) = 'successful') FROM ticket "
        "WHERE kind = 'change_request' AND closed_at >= ? AND closed_at < ?",
        bounds,
    )
    work = _one(profile, "SELECT COUNT(*) FROM work_item WHERE resolved >= ? AND resolved < ?", (start, end))
    return {
        "inc.opened": opened[0],
        "inc.resolved": resolved[0],
        "inc.p1p2.opened": p1p2[0],
        "inc.sla.pct": _sla_pct(profile, bounds),
        "inc.mttr.median_h": round(statistics.median(hours), 2) if hours else None,
        "inc.backlog": backlog[0],
        "chg.count": chg[0],
        "chg.success.pct": round(100.0 * chg[1] / chg[0], 2) if chg[0] else None,
        "work.resolved.count": work[0],
    }


def test_monthly_facts_match_hand_sql(ops_profile_rw):
    snap = _snapshot(ops_profile_rw, "monthly", "2026-08")
    assert snap.as_of == "2026-09-01" and snap.period_end == "2026-09-01" and snap.sla_source == "task_sla"
    expected = _hand_facts(ops_profile_rw, AUG)
    assert {k: snap.facts[k]["value"] for k in expected} == expected
    delta = round(expected["inc.sla.pct"] - _sla_pct(ops_profile_rw, JUL), 2)
    assert snap.facts["inc.sla.delta_pp_vs_prev_month"]["value"] == delta
    assert snap.facts["inc.sla.pct.prev_month"]["value"] == _sla_pct(ops_profile_rw, JUL)
    assert snap.facts["inc.opened"]["label"] == "Incidents opened"  # closed month: no "(to date)"
    assert snap.ai_runs == [] and snap.ai_derived_tables == [] and snap.ai_derived_facts == []


def test_monthly_tables(ops_profile_rw):
    snap = _snapshot(ops_profile_rw, "monthly", "2026-08")
    t = snap.tables
    assert t["trend_6m_by_family"]["rows"] and t["improvements"]["rows"]
    assert [r["period"] for r in t["trend_6m"]["rows"]] == [
        "2026-03",
        "2026-04",
        "2026-05",
        "2026-06",
        "2026-07",
        "2026-08",
    ]
    assert t["trend_6m"]["rows"][-1]["opened"] == snap.facts["inc.opened"]["value"]
    assert [c["label"] for c in t["sla_6m_by_family"]["columns"][1:]] == [r["period"] for r in t["trend_6m"]["rows"]]

    families = {r[0] for r in _rows(ops_profile_rw, "SELECT DISTINCT app_family FROM application WHERE is_deleted = 0")}
    assert {r["family"] for r in t["trend_6m_by_family"]["rows"]} == families - {None}
    fam = t["trend_6m_by_family"]["rows"][-1]
    fam_opened = _one(
        ops_profile_rw,
        "SELECT COUNT(*) FROM ticket t JOIN application a ON a.app_id = t.app_id WHERE t.kind = 'incident' "
        "AND a.app_family = ? AND t.opened_at >= ? AND t.opened_at < ?",
        (fam["family"], *AUG),
    )[0]
    assert fam["period"] == "2026-08" and fam["opened"] == fam_opened

    resolved_by_project = dict(
        _rows(
            ops_profile_rw,
            "SELECT project_key, COUNT(*) FROM work_item WHERE resolved >= ? AND resolved < ? GROUP BY project_key",
            AUG,
        )
    )
    assert {r["project"]: r["resolved"] for r in t["improvements"]["rows"]} == resolved_by_project
    assert sum(resolved_by_project.values()) == snap.facts["work.resolved.count"]["value"]

    upcoming = [
        r[0]
        for r in _rows(
            ops_profile_rw,
            "SELECT number FROM ticket WHERE kind = 'change_request' AND start_date >= ? AND start_date < ? "
            "ORDER BY start_date, number",
            ("2026-08-31T22:00:00Z", "2026-09-30T22:00:00Z"),
        )
    ]
    assert upcoming and [r["number"] for r in t["upcoming_changes"]["rows"]] == upcoming

    open_problems = {
        r[0]
        for r in _rows(
            ops_profile_rw,
            "SELECT number FROM ticket WHERE kind = 'problem' AND opened_at < ? AND (resolved_at IS NULL OR "
            "resolved_at >= ?)",
            (AUG[1], AUG[1]),
        )
    }
    assert {r["number"] for r in t["problems"]["rows"] if r["status"] == "open"} == open_problems
    assert all(r["incidents"] > 0 and r["months_seen"] >= 2 for r in t["top_recurring"]["rows"])
    assert len(t["top_recurring"]["rows"]) <= 10

    conn = db.connect(ops_profile_rw.paths.db, readonly=True)
    try:
        published = published_rule_findings(conn, date(2026, 9, 1))
        state = db.get_meta(conn, "ops.rule_findings_as_of")
    finally:
        conn.close()
    assert [r["title"] for r in t["findings"]["rows"]] == [r["title"] for r in published]
    assert state == "2026-09-01"


def test_monthly_open_month_is_reported_to_date(ops_profile_rw):
    snap = _snapshot(ops_profile_rw, "monthly", "2026-09")
    assert snap.as_of == "2026-09-01" and snap.data_as_of == "2026-09-01" and snap.period_end == "2026-10-01"
    assert snap.facts["inc.opened"]["value"] == 0 and snap.facts["inc.resolved"]["value"] == 0
    assert snap.facts["inc.opened"]["label"].endswith("(to date)")
    trend = snap.tables["trend_6m"]["rows"]
    assert trend[-1]["period"] == "2026-09" and trend[-2]["period"] == "2026-08"
    assert trend[-2]["opened"] == _hand_facts(ops_profile_rw, AUG)["inc.opened"]
    # Upcoming changes still start at the data date, not at the (future) month end.
    assert snap.tables["upcoming_changes"]["rows"]


def test_monthly_uses_window_and_as_of_when_data_date_is_mid_month(ops_profile_rw):
    paths = ops_profile_rw.paths
    (paths.config / "settings.yaml").write_text("as_of: 2026-08-15\n", encoding="utf-8")
    snap = _snapshot(ops_profile_rw, "monthly", "2026-08")
    assert snap.as_of == "2026-08-15" and snap.data_as_of == "2026-08-15" and snap.period_end == "2026-09-01"
    expected = _hand_facts(ops_profile_rw, AUG_TO_15)
    assert {k: snap.facts[k]["value"] for k in expected} == expected
    assert snap.facts["inc.sla.pct"]["label"].endswith("(to date)")
    upcoming = [
        r[0]
        for r in _rows(
            ops_profile_rw,
            "SELECT number FROM ticket WHERE kind = 'change_request' AND start_date >= ? AND start_date < ? "
            "ORDER BY start_date, number",
            ("2026-08-14T22:00:00Z", "2026-09-13T22:00:00Z"),
        )
    ]
    assert [r["number"] for r in snap.tables["upcoming_changes"]["rows"]] == upcoming
    # An older as-of computes findings read-only: the persisted rule state never moves.
    assert _one(ops_profile_rw, "SELECT value FROM meta WHERE key = 'ops.rule_findings_as_of'")[0] == "2026-09-01"


def test_monthly_rejects_a_week_period_with_exit_2(ops_profile_rw):
    code, out = _cli(
        "report", "snapshot", "monthly", "--period", "2026-W35", "--profile", "synthetic",
        "--data-dir", str(ops_profile_rw.paths.data_dir),
    )  # fmt: skip
    assert code == 2 and out["ok"] is False and out["error"]["kind"] == "validation"
    assert _one(ops_profile_rw, "SELECT COUNT(*) FROM report_snapshot")[0] == 0
