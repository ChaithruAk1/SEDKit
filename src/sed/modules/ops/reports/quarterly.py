"""Quarterly Budget & Governance snapshot builder (IT leadership, plan A10).

Spend vs budget by application and category (quarter to date, QoQ, YTD); idle license cost and right-size
opportunities; renewals and notice deadlines in the next two quarters; vendor/renewal/license risks ordered by
severity x spend; portfolio health (criticality x life cycle, quiet applications).

Cost aggregates cover the calendar months of `req.window` that are complete by `req.as_of` and have imported actuals
(an open quarter is reported to date); renewal and notice windows, license utilization, quiet applications and rule
findings use `req.as_of`.
"""

from __future__ import annotations

from typing import Any

from sed import analytics, metrics
from sed.modules.ops.reports import queries
from sed.reports.snapshot import SnapshotParts, SnapshotRequest, fact, table

RENEWAL_WINDOW_DAYS = 182
DEFAULT_QUIET_MONTHS = 5
DEFAULT_QUIET_MIN_COST = 50_000.0


def _spend_rows(
    conn: Any, group_by: str, qtd: list[str], prev: list[str], ytd: list[str], unknown: str
) -> list[dict[str, Any]]:
    """Merge metrics.cost_vs_budget over quarter-to-date, previous-quarter (same months) and year-to-date months."""
    current = {r["key"]: r for r in metrics.cost_vs_budget(conn, qtd, group_by)}
    previous = {r["key"]: r for r in metrics.cost_vs_budget(conn, prev, group_by)}
    year = {r["key"]: r for r in metrics.cost_vs_budget(conn, ytd, group_by)}
    rows = []
    for key in {*current, *previous, *year}:
        c, p, y = current.get(key, {}), previous.get(key, {}), year.get(key, {})
        actual = c.get("actual", 0.0 if qtd else None)
        prev_actual = p.get("actual", 0.0 if prev else None)
        rows.append(
            {
                "key": key if key is not None else unknown,
                "actual_qtd": actual,
                "budget_qtd": c.get("budget"),
                "variance_pct": c.get("variance_pct"),
                "prev_actual": prev_actual,
                "qoq_pct": queries.variance_pct(actual, prev_actual),
                "actual_ytd": y.get("actual", 0.0 if ytd else None),
                "budget_ytd": y.get("budget"),
                "variance_ytd_pct": y.get("variance_pct"),
            }
        )
    rows.sort(key=lambda r: (-(r["actual_qtd"] or 0.0), -(r["actual_ytd"] or 0.0), str(r["key"])))
    return rows


def _spend_columns(label: str) -> list[tuple[str, str, str]]:
    return [
        ("key", label, "text"),
        ("actual_qtd", "Actual QTD", "eur"),
        ("budget_qtd", "Budget QTD", "eur"),
        ("variance_pct", "Variance QTD %", "pct"),
        ("prev_actual", "Previous quarter (same months)", "eur"),
        ("qoq_pct", "QoQ %", "pct"),
        ("actual_ytd", "Actual YTD", "eur"),
        ("budget_ytd", "Budget YTD", "eur"),
        ("variance_ytd_pct", "Variance YTD %", "pct"),
    ]


def build(req: SnapshotRequest) -> SnapshotParts:
    conn, paths, settings, period, window, as_of = (
        req.conn,
        req.paths,
        req.settings,
        req.period,
        req.window,
        req.as_of,
    )
    fys = settings.fiscal_year_start
    cutoff = min(window.end_local, as_of)  # a period after the data date has no cost months at all

    qtd_months = queries.cost_months(conn, window.start_local, cutoff)
    prev_period = queries.shift_period(period, -1, fys)
    prev_months = queries.like_for_like_months(conn, qtd_months, period)
    ytd_months = queries.cost_months(conn, queries.fiscal_year_start_date(period.start_local, fys), cutoff)
    qtd = queries.cost_totals(conn, qtd_months)
    ytd = queries.cost_totals(conn, ytd_months)
    full_quarter = queries.complete_months(period.start_local, period.end_local)
    suffix = "" if qtd_months == full_quarter else " (to date)"

    low = queries.license_low_threshold(paths)
    idle = [
        {**x, "idle_qty": max((x["entitled_qty"] or 0) - (x["active_qty_90d"] or 0), 0)}
        for x in metrics.license_utilization(conn, as_of)
        if x["utilization"] is not None and x["utilization"] < low
    ]
    idle.sort(key=lambda x: (-(x["idle_cost_base"] or 0.0), x["license_id"]))

    horizon = metrics.renewals(conn, as_of, RENEWAL_WINDOW_DAYS)
    renewals = [
        {**r, "auto_renew": None if r["auto_renew"] is None else ("yes" if r["auto_renew"] else "no")}
        for r in horizon
        if r["days_to_end"] is not None and 0 <= r["days_to_end"] < RENEWAL_WINDOW_DAYS
    ]
    notices = [r for r in horizon if r["days_to_notice"] is not None and 0 <= r["days_to_notice"] < RENEWAL_WINDOW_DAYS]

    quiet_rule = queries.risk_rule(paths, "quiet_app")
    quiet_months = int(quiet_rule.get("months_without_tickets", DEFAULT_QUIET_MONTHS))
    quiet = metrics.quiet_apps(
        conn, as_of, quiet_months, float(quiet_rule.get("min_annual_cost_base", DEFAULT_QUIET_MIN_COST))
    )
    quiet_cells: dict[tuple[str, str], int] = {}
    for app in quiet:
        cell = (app["business_criticality"] or "(unknown)", app["life_cycle_stage"] or "(unknown)")
        quiet_cells[cell] = quiet_cells.get(cell, 0) + 1
    portfolio = [
        {**row, "quiet_apps": quiet_cells.get((row["criticality"], row["lifecycle"]), 0)}
        for row in queries.portfolio_health(conn, window, qtd_months)
    ]
    risks = queries.risk_rows(analytics.findings_as_of(conn, paths, as_of))
    months_note = f"{qtd_months[0]} to {qtd_months[-1]}" if qtd_months else "no month with actuals yet"

    facts = {
        "period.label": fact(period.label, "text", "Period"),
        "period.start": fact(period.start_local.isoformat(), "date", "Period start"),
        "period.end": fact(period.last_day.isoformat(), "date", "Period end"),
        "cost.actual.qtd": fact(qtd["actual"], "eur", f"Actual spend, quarter{suffix}", "cost.actual.qtd"),
        "cost.budget.qtd": fact(qtd["budget"], "eur", f"Budget, quarter{suffix}", "cost.budget.qtd"),
        "cost.variance.qtd_pct": fact(
            queries.variance_pct(qtd["actual"], qtd["budget"]), "pct", "Variance vs budget", "cost.variance.qtd_pct"
        ),
        "cost.actual.ytd": fact(ytd["actual"], "eur", "Actual spend, year to date", "cost.actual.ytd"),
        "cost.budget.ytd": fact(ytd["budget"], "eur", "Budget, year to date", "cost.budget.ytd"),
        "license.idle_cost": fact(
            round(sum((x["idle_cost_base"] or 0.0 for x in idle), 0.0), 2),
            "eur",
            "Idle license cost (under-used lines)",
            "license.idle_cost",
        ),
        "renewals.2q.count": fact(
            len(renewals), "count", "Contracts ending in the next 2 quarters", "renewals.2q.count"
        ),
        "notice.2q.count": fact(len(notices), "count", "Notice deadlines in the next 2 quarters", "notice.2q.count"),
        "apps.quiet.count": fact(len(quiet), "count", "Quiet applications with license cost", "apps.quiet.count"),
    }

    tables = {
        "spend_by_app": table(
            f"Spend vs budget by application ({months_note}; QoQ vs the same months of {prev_period.label})",
            _spend_columns("Application"),
            _spend_rows(conn, "app", qtd_months, prev_months, ytd_months, "(unattributed)"),
        ),
        "spend_by_category": table(
            f"Spend vs budget by cost category ({months_note})",
            _spend_columns("Category"),
            _spend_rows(conn, "category", qtd_months, prev_months, ytd_months, "(uncategorised)"),
        ),
        "license_idle": table(
            f"Idle license cost: under-used lines (utilization below {low:.0%}) and right-size opportunities",
            [
                ("license_id", "License", "text"),
                ("app", "Application", "text"),
                ("vendor", "Vendor", "text"),
                ("product", "Product", "text"),
                ("entitled_qty", "Entitled", "number"),
                ("active_qty_90d", "Active 90d", "number"),
                ("idle_qty", "Idle quantity", "number"),
                ("utilization", "Utilization", "ratio"),
                ("annual_cost_base", "Annual cost", "eur"),
                ("idle_cost_base", "Idle cost / yr", "eur"),
            ],
            idle,
        ),
        "renewals_2q": table(
            f"Renewals in the next 2 quarters (end date from {as_of.isoformat()}, {RENEWAL_WINDOW_DAYS} days)",
            [
                ("contract_number", "Contract", "text"),
                ("vendor", "Vendor", "text"),
                ("app", "Application", "text"),
                ("product", "Product", "text"),
                ("end_date", "End date", "date"),
                ("days_to_end", "Days to end", "count"),
                ("notice_deadline", "Notice deadline", "date"),
                ("days_to_notice", "Days to notice", "count"),
                ("auto_renew", "Auto-renew", "text"),
                ("annual_value_base", "Annual value", "eur"),
            ],
            renewals,
        ),
        "risks": table(
            "Vendor, renewal, license and cost risks (system-detected, severity x spend)",
            [
                ("severity", "Severity", "text"),
                ("kind", "Kind", "text"),
                ("title", "Finding", "text"),
                ("subject_id", "Subject", "text"),
                ("exposure_eur", "Exposure", "eur"),
            ],
            risks,
        ),
        "portfolio_health": table(
            "Portfolio health: business criticality x life cycle",
            [
                ("criticality", "Criticality", "text"),
                ("lifecycle", "Life cycle", "text"),
                ("apps", "Applications", "count"),
                ("incidents", "Incidents this quarter", "count"),
                ("p1p2", "P1/P2", "count"),
                ("license_cost", "License cost / yr", "eur"),
                ("spend", "Actual spend QTD", "eur"),
                ("quiet_apps", "Quiet apps", "count"),
            ],
            portfolio,
        ),
        "quiet_apps": table(
            f"Quiet applications: no tickets for {quiet_months} months but license cost",
            [
                ("app_id", "App id", "text"),
                ("name", "Application", "text"),
                ("business_criticality", "Criticality", "text"),
                ("life_cycle_stage", "Life cycle", "text"),
                ("last_ticket", "Last ticket (UTC)", "datetime"),
                ("license_cost", "License cost / yr", "eur"),
            ],
            quiet,
        ),
    }
    return SnapshotParts(facts=facts, tables=tables, sla_source=None, freshness=metrics.freshness(conn))
