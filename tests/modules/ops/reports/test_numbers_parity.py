"""Report facts on edge cases agree with the dashboard and with sed.metrics: P1/P2 weeks above 50, unused licenses,
several budget versions, the calendar-hour SLA fallback, open weeks and cost lines without an FX rate."""

from __future__ import annotations

from datetime import date
from typing import Any

from sed import db, metrics
from sed.calendar import local_midnight_utc, parse_period
from sed.modules.ops.queries.commercial import under_used_idle_cost
from sed.modules.ops.reports import queries
from sed.reports.snapshot import create_snapshot
from sed.settings import load_settings
from tests.fixtures.api import api_client

AS_OF = date(2026, 9, 1)
TZ = "Europe/Paris"


def _kpis(paths: Any, **params: Any) -> dict[str, dict[str, Any]]:
    body = api_client(paths).get("/api/ops/overview", params=params).json()
    return {k["key"]: k for k in body["kpis"]}


def _write(conn: Any, *statements: str) -> None:
    with db.write_tx(conn):
        for sql in statements:
            conn.execute(sql)


def test_weekly_p1p2_fact_counts_every_ticket_like_the_overview(ops_profile_rw):
    paths = ops_profile_rw.paths
    week = parse_period("2026-W32", TZ)
    conn = db.connect(paths.db)
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM ticket WHERE kind = 'incident' AND priority <= 2 "
            "AND opened_at >= ? AND opened_at < ?",
            (week.start_iso, week.end_iso),
        ).fetchone()[0]
        snap = create_snapshot(conn, paths, "weekly", "2026-W32")
    finally:
        conn.close()
    assert count > 50, "the fixture's W32 spike is above the P1/P2 table limit"
    assert snap.facts["inc.p1p2.opened"]["value"] == count
    assert len(snap.tables["p1p2"]["rows"]) == 50
    assert _kpis(paths, period="2026-W32")["inc.p1p2.opened"]["value"] == count


def test_weekly_idle_cost_includes_licenses_nobody_uses(ops_profile_rw):
    paths = ops_profile_rw.paths
    conn = db.connect(paths.db)
    try:
        license_id = conn.execute("SELECT license_id FROM license_usage ORDER BY license_id LIMIT 1").fetchone()[0]
        _write(conn, f"UPDATE license_usage SET active_qty_90d = 0 WHERE license_id = '{license_id}'")
        snap = create_snapshot(conn, paths, "weekly", "2026-W35")
        lines = metrics.license_utilization(conn, date.fromisoformat(snap.as_of))
    finally:
        conn.close()
    unused = next(x for x in lines if x["license_id"] == license_id)
    assert unused["utilization"] == 0.0 and unused["idle_cost_base"] > 0
    expected = under_used_idle_cost(lines, queries.license_low_threshold(paths))
    assert snap.facts["license.idle_cost"]["value"] == expected
    assert _kpis(paths)["license.idle_cost"]["value"] == expected


def test_next_fiscal_years_budget_keeps_the_current_budgets(ops_profile_rw):
    paths = ops_profile_rw.paths
    months = ["2026-07", "2026-08"]
    conn = db.connect(paths.db)
    try:
        before_metrics = sum(r["budget"] or 0 for r in metrics.cost_vs_budget(conn, months, "app"))
        before_report = queries.cost_totals(conn, months)["budget"]
        before_kpi = _kpis(paths)["cost.budget.ytd"]["value"]
        columns = [r[1] for r in conn.execute("PRAGMA table_info(cost_line)")]
        replaced = {"cost_line_id": "cost_line_id || '-fy27'", "as_of": "'2027-01-01'", "period": "'2027-01'"}
        select = ", ".join(replaced.get(c, c) for c in columns)
        _write(
            conn,
            f"INSERT INTO cost_line ({', '.join(columns)}) SELECT {select} FROM cost_line "
            "WHERE line_type = 'budget' AND period = '2026-08'",
        )
        assert before_metrics > 0 and before_report
        assert sum(r["budget"] or 0 for r in metrics.cost_vs_budget(conn, months, "app")) == before_metrics
        assert queries.cost_totals(conn, months)["budget"] == before_report
        assert sum(r["budget"] or 0 for r in metrics.cost_vs_budget(conn, ["2027-01"], "app")) > 0
    finally:
        conn.close()
    assert _kpis(paths)["cost.budget.ytd"]["value"] == before_kpi


def test_vendor_sla_trend_applies_the_calendar_hour_targets(ops_profile_rw):
    paths = ops_profile_rw.paths
    vendor = ops_profile_rw.ids["vendor_p2"]
    conn = db.connect(paths.db)
    try:
        _write(conn, "UPDATE task_sla SET has_breached = NULL", "UPDATE ticket SET made_sla = NULL")
        assert metrics.sla_source(conn) == "targets"
        trend = next(v for v in metrics.vendor_sla_trend(conn, AS_OF, TZ) if v["vendor_id"] == vendor)
        expected = {
            p["period"]: metrics.sla(conn, metrics.Filters(vendor_id=vendor), parse_period(p["period"], TZ), "targets")[
                "pct"
            ]
            for p in trend["series"]
        }
    finally:
        conn.close()
    assert {p["period"]: p["sla_pct"] for p in trend["series"]} == expected
    assert any(pct is not None and pct < 100.0 for pct in expected.values())
    api = api_client(paths).get("/api/ops/vendors/sla-trend", params={"months": 6}).json()
    row = next(item for item in api["items"] if item["vendor_id"] == vendor)
    assert {p["period"]: p["sla_pct"] for p in row["series"]} == expected


def test_an_open_week_is_reported_to_date(ops_profile_rw):
    paths = ops_profile_rw.paths
    settings = load_settings(paths)
    conn = db.connect(paths.db)
    try:
        snap = create_snapshot(conn, paths, "weekly", "2026-W36")
        at = local_midnight_utc(AS_OF, settings.reporting_tz)
        attention = metrics.attention(conn, metrics.Filters(), at, settings.thresholds)
    finally:
        conn.close()
    facts = snap.facts
    assert facts["attention.count"]["value"] == attention["count"]
    assert facts["inc.opened"]["label"].endswith("(to date)")
    assert facts["inc.opened.delta_vs_avg4w_pct"]["value"] is None
    assert facts["inc.backlog"]["label"] == "Open backlog at the as-of date"


def test_cost_lines_without_an_fx_rate_are_flagged_not_reported_as_zero(ops_profile_rw):
    paths = ops_profile_rw.paths
    vendor = ops_profile_rw.ids["vendor_p2"]
    conn = db.connect(paths.db)
    try:
        _write(
            conn,
            "UPDATE cost_line SET amount_base = NULL "
            f"WHERE vendor_id = '{vendor}' AND period IN ('2026-07', '2026-08')",
        )
        missing = conn.execute(
            "SELECT COUNT(*) FROM cost_line WHERE amount IS NOT NULL AND amount_base IS NULL"
        ).fetchone()[0]
        snap = create_snapshot(conn, paths, "vendor", "2026-Q3", vendor)
        assert missing > 0
        assert snap.facts["vendor.spend.unconverted_lines.count"]["value"] == missing
        assert "without FX rate" in snap.facts["vendor.spend.period"]["label"]

        _write(conn, "UPDATE cost_line SET amount_base = NULL WHERE line_type = 'actual' AND period = '2026-08'")
        assert queries.cost_by_month(conn, ["2026-08"])[0]["actual"] is None
        assert queries.cost_totals(conn, ["2026-08"])["actual"] is None
    finally:
        conn.close()
