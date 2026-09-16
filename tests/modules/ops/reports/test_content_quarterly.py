"""Quarterly Budget & Governance content: exact facts against hand-written SQL, planted patterns, as-of semantics."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from typing import Any

from typer.testing import CliRunner

from sed import db
from sed.reports.snapshot import Snapshot, create_snapshot

INACTIVE = "('non_renewing', 'terminated', 'expired')"


def _snapshot(profile: Any, report: str, period: str, vendor: str | None = None) -> Snapshot:
    conn = db.connect(profile.paths.db)
    try:
        return create_snapshot(conn, profile.paths, report, period, vendor)
    finally:
        conn.close()


def _rows(profile: Any, sql: str, params: tuple = ()) -> list[tuple]:
    conn = sqlite3.connect(str(profile.paths.db))
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _one(profile: Any, sql: str, params: tuple = ()) -> Any:
    return _rows(profile, sql, params)[0]


def _cli(*args: str) -> tuple[int, dict]:
    from sed.cli import app

    result = CliRunner().invoke(app, [*args, "--json"])
    lines = [ln for ln in result.stdout.strip().splitlines() if ln.strip()]
    return result.exit_code, json.loads(lines[-1])


def _cost(profile: Any, months: list[str]) -> tuple[float, float | None]:
    marks = ", ".join("?" for _ in months)
    version = _one(profile, "SELECT MAX(as_of) FROM cost_line WHERE line_type = 'budget'")[0]
    actual = _one(
        profile,
        f"SELECT SUM(amount_base) FROM cost_line WHERE line_type = 'actual' AND period IN ({marks})",
        tuple(months),
    )[0]
    budget = _one(
        profile,
        f"SELECT SUM(amount_base) FROM cost_line WHERE line_type = 'budget' AND as_of = ? AND period IN ({marks})",
        (version, *months),
    )[0]
    return round(actual or 0.0, 2), round(budget, 2) if budget is not None else None


def _under_used(profile: Any, as_of: date) -> list[tuple[str, float]]:
    rows = _rows(
        profile,
        "SELECT l.license_id, l.entitled_qty, l.unit_cost_base, u.active_qty_90d FROM license l "
        "JOIN license_usage u ON u.license_id = l.license_id AND u.as_of_date = (SELECT MAX(as_of_date) "
        "FROM license_usage WHERE license_id = l.license_id AND as_of_date <= ?) WHERE l.is_deleted = 0",
        (as_of.isoformat(),),
    )
    out = []
    for license_id, entitled, unit_cost, active in rows:
        if entitled and active is not None and round(active / entitled, 4) < 0.70:
            out.append((license_id, round(max(entitled - active, 0) * (unit_cost or 0), 2)))
    return out


def _contract_count(profile: Any, column: str, as_of: date, days: int) -> int:
    return _one(
        profile,
        f"SELECT COUNT(*) FROM contract WHERE is_deleted = 0 AND COALESCE(renewal_status, 'active') NOT IN {INACTIVE} "
        f"AND {column} >= ? AND {column} < ?",
        (as_of.isoformat(), (as_of + timedelta(days=days)).isoformat()),
    )[0]


def test_quarterly_facts_match_hand_sql(ops_profile_rw):
    snap = _snapshot(ops_profile_rw, "quarterly", "2026-Q3")
    assert snap.as_of == "2026-09-01" and snap.period_end == "2026-10-01" and snap.data_as_of == "2026-09-01"
    facts = {k: v["value"] for k, v in snap.facts.items()}
    as_of = date(2026, 9, 1)

    actual_qtd, budget_qtd = _cost(ops_profile_rw, ["2026-07", "2026-08"])
    actual_ytd, budget_ytd = _cost(ops_profile_rw, [f"2026-{m:02d}" for m in range(1, 9)])
    assert facts["cost.actual.qtd"] == actual_qtd and facts["cost.budget.qtd"] == budget_qtd
    assert facts["cost.variance.qtd_pct"] == round(100.0 * (actual_qtd - budget_qtd) / budget_qtd, 2)
    assert facts["cost.actual.ytd"] == actual_ytd and facts["cost.budget.ytd"] == budget_ytd

    assert facts["renewals.2q.count"] == _contract_count(ops_profile_rw, "end_date", as_of, 182)
    assert facts["notice.2q.count"] == _contract_count(ops_profile_rw, "notice_deadline", as_of, 182)
    under = _under_used(ops_profile_rw, as_of)
    assert facts["license.idle_cost"] == round(sum(cost for _, cost in under), 2)

    quiet = _one(
        ops_profile_rw,
        "SELECT COUNT(*) FROM application a WHERE a.is_deleted = 0 AND NOT EXISTS (SELECT 1 FROM ticket t "
        "WHERE t.app_id = a.app_id AND t.opened_at >= '2026-04-01T00:00:00Z') AND (SELECT COALESCE(SUM(entitled_qty * "
        "unit_cost_base), 0) FROM license l WHERE l.app_id = a.app_id AND l.is_deleted = 0) >= 50000",
    )[0]
    assert facts["apps.quiet.count"] == quiet == len(snap.tables["quiet_apps"]["rows"])
    assert snap.sla_source is None and snap.ai_runs == []


def test_quarterly_tables_carry_the_planted_patterns(ops_profile_rw):
    snap = _snapshot(ops_profile_rw, "quarterly", "2026-Q3")
    truth = json.loads((ops_profile_rw.ground_truth / "patterns.json").read_text(encoding="utf-8"))
    t = snap.tables

    renewals = {r["contract_number"] for r in t["renewals_2q"]["rows"]}
    p4 = {c["contract"] for c in truth["P4"]}
    assert len(p4) == 6 and p4 <= renewals
    assert truth["controls"]["non_renewing_contract"] not in renewals
    assert len(t["renewals_2q"]["rows"]) == snap.facts["renewals.2q.count"]["value"]

    idle = {r["license_id"]: r for r in t["license_idle"]["rows"]}
    p3_under = [x for x in truth["P3"] if x["utilization"] < 0.70]
    assert p3_under and {x["license"] for x in p3_under} <= set(idle)
    for x in p3_under:
        assert idle[x["license"]]["idle_cost_base"] == x["idle_cost"]
    assert truth["controls"]["seasonal_license"] not in idle
    assert set(idle) == {license_id for license_id, _ in _under_used(ops_profile_rw, date(2026, 9, 1))}

    by_category = dict(
        _rows(
            ops_profile_rw,
            "SELECT cost_category, SUM(amount_base) FROM cost_line WHERE line_type = 'actual' "
            "AND period IN ('2026-07', '2026-08') GROUP BY cost_category",
        )
    )
    assert {r["key"]: r["actual_qtd"] for r in t["spend_by_category"]["rows"]} == {
        k: round(v, 2) for k, v in by_category.items()
    }
    prev = dict(
        _rows(
            ops_profile_rw,
            "SELECT cost_category, SUM(amount_base) FROM cost_line WHERE line_type = 'actual' "
            "AND period IN ('2026-04', '2026-05') GROUP BY cost_category",
        )
    )
    assert {r["key"]: r["prev_actual"] for r in t["spend_by_category"]["rows"]} == {
        k: round(v, 2) for k, v in prev.items()
    }
    apps = _one(ops_profile_rw, "SELECT COUNT(*) FROM application WHERE is_deleted = 0")[0]
    assert sum(r["apps"] for r in t["portfolio_health"]["rows"]) == apps
    assert sum(r["quiet_apps"] for r in t["portfolio_health"]["rows"]) == snap.facts["apps.quiet.count"]["value"]
    assert truth["P11"]["app"] in {r["name"] for r in t["quiet_apps"]["rows"]}

    risks = t["risks"]["rows"]
    kinds = {r["kind"] for r in risks}
    assert {"renewal_risk", "license_risk", "vendor_risk", "cost_risk", "rationalization"} <= kinds
    order = {"critical": 3, "high": 2, "medium": 1, "low": 0}
    keys = [(-order[r["severity"]], -(r["exposure_eur"] or 0.0)) for r in risks]
    assert keys == sorted(keys)
    assert _one(ops_profile_rw, "SELECT value FROM meta WHERE key = 'ops.rule_findings_as_of'")[0] == "2026-09-01"


def test_quarterly_mid_quarter_data_date_limits_cost_months_and_windows(ops_profile_rw):
    paths = ops_profile_rw.paths
    (paths.config / "settings.yaml").write_text("as_of: 2026-08-15\n", encoding="utf-8")
    snap = _snapshot(ops_profile_rw, "quarterly", "2026-Q3")
    as_of = date(2026, 8, 15)
    assert snap.as_of == "2026-08-15" and snap.period_end == "2026-10-01"
    facts = {k: v["value"] for k, v in snap.facts.items()}
    assert (facts["cost.actual.qtd"], facts["cost.budget.qtd"]) == _cost(ops_profile_rw, ["2026-07"])
    assert (facts["cost.actual.ytd"], facts["cost.budget.ytd"]) == _cost(
        ops_profile_rw, [f"2026-{m:02d}" for m in range(1, 8)]
    )
    assert facts["renewals.2q.count"] == _contract_count(ops_profile_rw, "end_date", as_of, 182)
    assert facts["notice.2q.count"] == _contract_count(ops_profile_rw, "notice_deadline", as_of, 182)
    assert snap.facts["cost.actual.qtd"]["label"].endswith("(to date)")
    assert _one(ops_profile_rw, "SELECT value FROM meta WHERE key = 'ops.rule_findings_as_of'")[0] == "2026-09-01"


def test_quarterly_cost_skips_months_without_imported_actuals(ops_profile_rw):
    conn = sqlite3.connect(str(ops_profile_rw.paths.db))
    try:
        with conn:
            conn.execute("DELETE FROM cost_line WHERE line_type = 'actual' AND period = '2026-08'")
    finally:
        conn.close()
    snap = _snapshot(ops_profile_rw, "quarterly", "2026-Q3")
    facts = {k: v["value"] for k, v in snap.facts.items()}
    # August has a budget but no actuals yet: it is left out of both sides instead of counting as zero spend.
    assert (facts["cost.actual.qtd"], facts["cost.budget.qtd"]) == _cost(ops_profile_rw, ["2026-07"])
    assert (facts["cost.actual.ytd"], facts["cost.budget.ytd"]) == _cost(
        ops_profile_rw, [f"2026-{m:02d}" for m in range(1, 8)]
    )
    april = dict(  # the like-for-like month of the previous quarter
        _rows(
            ops_profile_rw,
            "SELECT cost_category, SUM(amount_base) FROM cost_line WHERE line_type = 'actual' AND period = '2026-04' "
            "GROUP BY cost_category",
        )
    )
    assert {r["key"]: r["prev_actual"] for r in snap.tables["spend_by_category"]["rows"]} == {
        k: round(v, 2) for k, v in april.items()
    }
    assert "2026-07 to 2026-07" in snap.tables["spend_by_app"]["title"]


def test_quarterly_qoq_compares_the_same_months_of_the_previous_quarter(ops_profile_rw):
    # Actuals start in 2025-03, so 2025-Q2 (April-June) can only be compared with March, June's counterpart.
    snap = _snapshot(ops_profile_rw, "quarterly", "2025-Q2")
    march = dict(
        _rows(
            ops_profile_rw,
            "SELECT cost_category, SUM(amount_base) FROM cost_line WHERE line_type = 'actual' AND period = '2025-03' "
            "GROUP BY cost_category",
        )
    )
    assert march and {r["key"]: r["prev_actual"] for r in snap.tables["spend_by_category"]["rows"]} == {
        k: round(v, 2) for k, v in march.items()
    }
    facts = {k: v["value"] for k, v in snap.facts.items()}
    assert facts["cost.actual.qtd"] == _cost(ops_profile_rw, ["2025-04", "2025-05", "2025-06"])[0]
    assert facts["cost.budget.qtd"] is None and facts["cost.variance.qtd_pct"] is None  # no 2025 budget in the fixture
    assert snap.as_of == "2025-07-01" and not snap.facts["cost.actual.qtd"]["label"].endswith("(to date)")


def test_calendar_helpers():
    from sed.calendar import parse_period
    from sed.modules.ops.reports import queries

    assert queries.complete_months(date(2026, 7, 1), date(2026, 9, 1)) == ["2026-07", "2026-08"]
    assert queries.complete_months(date(2026, 7, 15), date(2026, 9, 1)) == ["2026-08"]
    assert queries.complete_months(date(2026, 7, 1), date(2026, 8, 31)) == ["2026-07"]
    assert queries.complete_months(date(2026, 10, 1), date(2026, 9, 1)) == []
    assert queries.complete_months(date(2026, 11, 1), date(2027, 2, 1)) == ["2026-11", "2026-12", "2027-01"]
    assert queries.months_ending(date(2026, 9, 1), 3) == ["2026-06", "2026-07", "2026-08"]
    assert queries.months_ending(date(2026, 8, 15), 2) == ["2026-06", "2026-07"]
    assert queries.months_ending(date(2027, 1, 1), 2) == ["2026-11", "2026-12"]
    assert queries.fiscal_year_start_date(date(2026, 2, 1), 4) == date(2025, 4, 1)
    assert queries.fiscal_year_start_date(date(2026, 8, 1), 1) == date(2026, 1, 1)
    # Fiscal years starting in April: FY2026-Q1 is April-June 2025, so the quarter before starts in January 2025.
    q1 = parse_period("2026-Q1", "Europe/Paris", 4)
    assert q1.start_local == date(2025, 4, 1)
    assert queries.shift_period(q1, -1, 4).start_local == date(2025, 1, 1)
    assert queries.variance_pct(110.0, 100.0) == 10.0 and queries.variance_pct(5.0, None) is None


def test_quarterly_after_the_data_date_has_no_cost_months(ops_profile_rw):
    snap = _snapshot(ops_profile_rw, "quarterly", "2026-Q4")
    facts = {k: v["value"] for k, v in snap.facts.items()}
    assert snap.as_of == "2026-09-01" and snap.period_end == "2027-01-01"
    assert facts["cost.actual.qtd"] is None and facts["cost.budget.qtd"] is None
    assert facts["cost.variance.qtd_pct"] is None
    assert (facts["cost.actual.ytd"], facts["cost.budget.ytd"]) == _cost(
        ops_profile_rw, [f"2026-{m:02d}" for m in range(1, 9)]
    )
    assert facts["renewals.2q.count"] == _contract_count(ops_profile_rw, "end_date", date(2026, 9, 1), 182)


def test_quarterly_rejects_a_month_period_with_exit_2(ops_profile_rw):
    code, out = _cli(
        "report", "snapshot", "quarterly", "--period", "2026-08", "--profile", "synthetic",
        "--data-dir", str(ops_profile_rw.paths.data_dir),
    )  # fmt: skip
    assert code == 2 and out["ok"] is False and out["error"]["kind"] == "validation"
