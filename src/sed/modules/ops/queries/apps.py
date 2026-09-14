"""Filter options, the application portfolio grid and App 360.

Ticket-derived columns use the last three complete months before as-of; cost YTD uses the complete fiscal-year
months before the as-of month; license columns use the latest usage snapshot on or before as-of. Open risks and
App 360 findings are published findings (rule and approved AI) related to the application through its own id, its
contracts, licenses, primary vendor or app/category cost key.

In the grid, the app, family and vendor filters select applications (vendor = primary vendor); the group filter
narrows the ticket-derived columns.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace
from datetime import date
from typing import Any

from sed import metrics
from sed.api.models import FindingOut, Kpi
from sed.modules.ops.api_models import (
    App360Out,
    AppRow,
    AppsOut,
    ChangeRow,
    CostRow,
    DocRow,
    JiraRow,
    OpsAppOption,
    OpsFiltersOut,
    RenewalRow,
)
from sed.modules.ops.queries.commercial import latest_budget_version, license_dicts, license_row, under_used_idle_cost
from sed.modules.ops.queries.common import (
    Context,
    findings_by_app,
    kpi,
    last_full_period,
    last_months_window,
    marks,
    pct,
    ticket_filter_sql,
    trend_periods,
    where_sql,
    ytd_months,
)
from sed.modules.ops.queries.search import ticket_rows
from sed.modules.ops.queries.tickets import backlog_summary, mttr_stats, resolved_rows, volume_rows

OPEN_TICKETS_LIMIT = 20
CHANGES_LIMIT = 20
JIRA_LIMIT = 20
DOCS_LIMIT = 50
VOLUME_MONTHS = 12
INACTIVE_CONTRACT = ("non_renewing", "terminated", "expired")


def filter_options(conn: sqlite3.Connection) -> OpsFiltersOut:
    rows = conn.execute(
        "SELECT app_id, name, app_family FROM application WHERE is_deleted = 0 ORDER BY name, app_id"
    ).fetchall()
    return OpsFiltersOut(
        families=sorted({r["app_family"] for r in rows if r["app_family"]}),
        apps=[OpsAppOption(app_id=r["app_id"], name=r["name"], family=r["app_family"]) for r in rows],
    )


def ticket_scope(ctx: Context, app_ids: list[str] | None = None) -> Context:
    """Context whose ticket filters keep only the group filter (plus an optional app scope)."""
    return replace(ctx, filters=replace(ctx.filters, app=list(app_ids or []), family=None, vendor=None))


def _app_rows(ctx: Context, app_id: str | None = None) -> list[Any]:
    f = ctx.filters
    if app_id is not None:
        clauses, params = ["a.app_id = ?"], [app_id]
    else:
        clauses, params = ["a.is_deleted = 0"], []
        if f.app:
            clauses.append(f"a.app_id IN ({marks(f.app)})")
            params += list(f.app)
        if f.family:
            clauses.append("a.app_family = ?")
            params.append(f.family)
        if f.vendor:
            clauses.append("a.primary_vendor_id = ?")
            params.append(f.vendor)
    return ctx.conn.execute(
        "SELECT a.app_id, a.name, a.app_family, a.business_criticality, a.life_cycle_stage, "
        "COALESCE(v.name, a.vendor_raw) AS primary_vendor FROM application a "
        f"LEFT JOIN vendor v ON v.vendor_id = a.primary_vendor_id WHERE {where_sql(clauses)} ORDER BY a.name, a.app_id",
        params,
    ).fetchall()


def _incidents_opened(ctx: Context, start_iso: str, end_iso: str) -> dict[str, list[int]]:
    """app_id -> [incidents opened, P1/P2 opened] in [start, end) for the context's ticket filters."""
    clauses, params = ticket_filter_sql(ctx.filters)
    rows = ctx.conn.execute(
        f"SELECT t.app_id, COUNT(*), SUM(t.priority <= 2) FROM ticket t WHERE {where_sql(['t.kind = ?', *clauses])} "
        "AND t.opened_at >= ? AND t.opened_at < ? GROUP BY t.app_id",
        ["incident", *params, start_iso, end_iso],
    ).fetchall()
    return {r[0]: [int(r[1]), int(r[2] or 0)] for r in rows if r[0] is not None}


def _sla_by_app(ctx: Context, start_iso: str, end_iso: str) -> dict[str, dict[str, Any]]:
    """app_id -> {total, met, hours} for incidents resolved in [start, end)."""
    out: dict[str, dict[str, Any]] = {}
    source = metrics.sla_source(ctx.conn)
    for _resolved_at, _priority, met, hours, app_id in resolved_rows(
        ctx, start_iso, end_iso, source=source, columns="t.app_id"
    ):
        if app_id is None:
            continue
        acc = out.setdefault(app_id, {"total": 0, "met": 0, "hours": []})
        acc["total"] += 1
        acc["met"] += 1 if met else 0
        if hours is not None:
            acc["hours"].append(hours)
    return out


def _cost_by_app(ctx: Context, months: list[str], app_id: str | None = None) -> dict[str, list[float | None]]:
    """app_id -> [actual, budget (latest version)] summed over the months."""
    if not months:
        return {}
    extra, params = ("AND c.app_id = ?", [app_id]) if app_id else ("AND c.app_id IS NOT NULL", [])
    rows = ctx.conn.execute(
        "SELECT c.app_id, SUM(CASE WHEN c.line_type = 'actual' THEN c.amount_base END), "
        "SUM(CASE WHEN c.line_type = 'budget' AND c.as_of IS ? THEN c.amount_base END) FROM cost_line c "
        f"WHERE c.period IN ({marks(months)}) {extra} GROUP BY c.app_id",
        [latest_budget_version(ctx), *months, *params],
    ).fetchall()
    return {r[0]: [None if r[1] is None else round(r[1], 2), None if r[2] is None else round(r[2], 2)] for r in rows}


def _license_by_app(ctx: Context) -> dict[str, dict[str, Any]]:
    app_of = {r[0]: r[1] for r in ctx.conn.execute("SELECT license_id, app_id FROM license WHERE app_id IS NOT NULL")}
    by_app: dict[str, dict[str, Any]] = {}
    for r in metrics.license_utilization(ctx.conn, ctx.as_of):
        app_id = app_of.get(r["license_id"])
        if not app_id:
            continue
        acc = by_app.setdefault(app_id, {"annual": 0.0, "active": 0.0, "entitled": 0.0, "measured": False})
        acc["annual"] += r["annual_cost_base"] or 0.0
        if r["active_qty_90d"] is not None and r["entitled_qty"]:
            acc["active"] += r["active_qty_90d"]
            acc["entitled"] += r["entitled_qty"]
            acc["measured"] = True
    return by_app


@dataclass
class GridData:
    """Per-application aggregates behind the grid columns (and the App 360 KPIs)."""

    opened: dict[str, list[int]]
    sla: dict[str, dict[str, Any]]
    costs: dict[str, list[float | None]]
    licenses: dict[str, dict[str, Any]]
    risks: dict[str, list[FindingOut]]

    @classmethod
    def load(cls, ctx: Context, app_id: str | None = None) -> GridData:
        tickets_ctx = ticket_scope(ctx, [app_id] if app_id else None)
        last3 = last_months_window(ctx, 3)
        return cls(
            opened=_incidents_opened(tickets_ctx, last3.start_iso, last3.end_iso),
            sla=_sla_by_app(tickets_ctx, last3.start_iso, last3.end_iso),
            costs=_cost_by_app(ctx, ytd_months(ctx), app_id),
            licenses=_license_by_app(ctx),
            risks=findings_by_app(ctx.conn, ctx.as_of),
        )

    def row(self, r: Any) -> AppRow:
        aid = r["app_id"]
        lic = self.licenses.get(aid)
        sla_row = self.sla.get(aid, {"total": 0, "met": 0})
        return AppRow(
            app_id=aid,
            name=r["name"],
            family=r["app_family"],
            criticality=r["business_criticality"],
            lifecycle=r["life_cycle_stage"],
            primary_vendor=r["primary_vendor"],
            annual_license_cost_base=round(lic["annual"], 2) if lic else None,
            cost_ytd_base=self.costs.get(aid, [None, None])[0],
            incidents_per_month_3m=round(self.opened.get(aid, [0, 0])[0] / 3, 2),
            sla_pct_3m=pct(sla_row["met"], sla_row["total"]),
            license_utilization=round(lic["active"] / lic["entitled"], 4) if lic and lic["measured"] else None,
            open_risks=len(self.risks.get(aid, [])),
        )


def apps(ctx: Context) -> AppsOut:
    found = _app_rows(ctx)
    if not found:
        return AppsOut(items=[])
    data = GridData.load(ctx)
    return AppsOut(items=[data.row(r) for r in found])


# ---------------------------------------------------------------------------
# App 360
# ---------------------------------------------------------------------------


def _open_ticket_ids(ctx: Context, app_id: str) -> list[int]:
    return [
        r[0]
        for r in ctx.conn.execute(
            "SELECT t.rowid FROM ticket t WHERE t.app_id = ? AND t.is_open = 1 AND t.stale_open = 0 "
            "AND t.kind != 'change_request' ORDER BY t.priority IS NULL, t.priority, t.opened_at DESC, t.rowid DESC "
            "LIMIT ?",
            (app_id, OPEN_TICKETS_LIMIT),
        )
    ]


def _changes(ctx: Context, app_id: str) -> list[ChangeRow]:
    rows = ctx.conn.execute(
        "SELECT number, change_type, close_code, start_date, closed_at, short_description FROM ticket "
        "WHERE app_id = ? AND kind = 'change_request' "
        "ORDER BY COALESCE(start_date, opened_at) DESC, number DESC LIMIT ?",
        (app_id, CHANGES_LIMIT),
    ).fetchall()
    return [ChangeRow(**dict(r)) for r in rows]


def _monthly_cost(ctx: Context, app_id: str, months: list[str]) -> list[CostRow]:
    rows = {
        r["period"]: r
        for r in ctx.conn.execute(
            "SELECT c.period, SUM(CASE WHEN c.line_type = 'actual' THEN c.amount_base END) AS actual, "
            "SUM(CASE WHEN c.line_type = 'budget' AND c.as_of IS ? THEN c.amount_base END) AS budget "
            f"FROM cost_line c WHERE c.app_id = ? AND c.period IN ({marks(months)}) GROUP BY c.period",
            [latest_budget_version(ctx), app_id, *months],
        )
    }
    out = []
    for month in months:
        r = rows.get(month)
        actual = round(r["actual"], 2) if r is not None and r["actual"] is not None else None
        budget = round(r["budget"], 2) if r is not None and r["budget"] else None
        variance = round(100.0 * ((actual or 0.0) - budget) / budget, 2) if budget else None
        out.append(CostRow(key=month, label=month, actual=actual, budget=budget, variance_pct=variance))
    return out


def _days(value: str | None, as_of: date) -> int | None:
    return (date.fromisoformat(value) - as_of).days if value else None


def _contracts(ctx: Context, app_id: str) -> list[RenewalRow]:
    """Every non-deleted contract of the application, with days to end and to notice as of as-of."""
    rows = ctx.conn.execute(
        "SELECT c.contract_id, c.contract_number, COALESCE(v.name, c.vendor_raw) AS vendor, "
        "COALESCE(a.name, c.app_raw) AS app, c.product, c.end_date, c.notice_deadline, c.auto_renew, "
        "c.renewal_status, c.annual_value_base FROM contract c LEFT JOIN vendor v ON v.vendor_id = c.vendor_id "
        "LEFT JOIN application a ON a.app_id = c.app_id WHERE c.app_id = ? AND c.is_deleted = 0 "
        "ORDER BY c.end_date IS NULL, c.end_date, c.contract_id",
        (app_id,),
    ).fetchall()
    return [
        RenewalRow(
            contract_id=r["contract_id"],
            contract_number=r["contract_number"],
            vendor=r["vendor"],
            app=r["app"],
            product=r["product"],
            end_date=r["end_date"],
            days_to_end=_days(r["end_date"], ctx.as_of),
            notice_deadline=r["notice_deadline"],
            days_to_notice=_days(r["notice_deadline"], ctx.as_of),
            auto_renew=None if r["auto_renew"] is None else bool(r["auto_renew"]),
            renewal_status=r["renewal_status"],
            annual_value_base=r["annual_value_base"],
        )
        for r in rows
    ]


def _jira(ctx: Context, app_id: str) -> list[JiraRow]:
    rows = ctx.conn.execute(
        "SELECT issue_key, issue_type, status, priority, created, resolved, summary FROM work_item WHERE app_id = ? "
        "ORDER BY updated DESC, issue_key LIMIT ?",
        (app_id, JIRA_LIMIT),
    ).fetchall()
    return [JiraRow(**dict(r)) for r in rows]


def _docs(ctx: Context, app_id: str) -> list[DocRow]:
    rows = ctx.conn.execute(
        "SELECT page_id, space_key, title, page_type, last_updated FROM doc_page WHERE app_id = ? AND is_deleted = 0 "
        "ORDER BY last_updated DESC, page_id LIMIT ?",
        (app_id, DOCS_LIMIT),
    ).fetchall()
    return [DocRow(**dict(r)) for r in rows]


def _app_kpis(
    ctx: Context, app: AppRow, data: GridData, contracts: list[RenewalRow], licenses: list[dict[str, Any]]
) -> list[Kpi]:
    app_id = app.app_id
    opened, p1p2 = data.opened.get(app_id, [0, 0])
    sla_row = data.sla.get(app_id, {"total": 0, "met": 0, "hours": []})
    backlog = backlog_summary(ticket_scope(ctx, [app_id]), ctx.as_of_end_iso)
    actual_ytd, budget_ytd = data.costs.get(app_id, [None, None])
    active = [c for c in contracts if (c.renewal_status or "active") not in INACTIVE_CONTRACT]
    horizon = sum(
        1
        for c in active
        if (c.days_to_end is not None and 0 <= c.days_to_end <= 180)
        or (c.days_to_notice is not None and 0 <= c.days_to_notice <= 180)
    )
    currency = ctx.settings.base_currency.lower()
    return [
        kpi("inc.backlog", "Open incident backlog", backlog["total"], "count"),
        kpi("inc.opened.3m", "Incidents opened (3 months)", opened, "count"),
        kpi("inc.p1p2.opened.3m", "P1/P2 opened (3 months)", p1p2, "count"),
        kpi("inc.sla.pct.3m", "SLA met (3 months)", pct(sla_row["met"], sla_row["total"]), "pct"),
        kpi("inc.mttr.median_h.3m", "MTTR median, hours (3 months)", mttr_stats(sla_row["hours"])["median_h"], "hours"),
        kpi("cost.actual.ytd", "Spend YTD", actual_ytd, currency, compare=budget_ytd),
        kpi("cost.budget.ytd", "Budget YTD", budget_ytd, currency),
        kpi("license.annual_cost", "License cost per year", app.annual_license_cost_base, currency),
        kpi("license.utilization", "License utilization", app.license_utilization, "ratio"),
        kpi("license.idle_cost", "Idle license cost (under-used lines)", under_used_idle_cost(licenses), currency),
        kpi(
            "contracts.annual_value",
            "Active contract value per year",
            round(sum(c.annual_value_base or 0 for c in active), 2),
            currency,
        ),
        kpi("renewals.180d.count", "Renewals or notice deadlines within 180 days", horizon, "count"),
    ]


def app_360(ctx: Context, app_id: str) -> App360Out | None:
    found = _app_rows(ctx, app_id)
    if not found:
        return None
    data = GridData.load(ctx, app_id)
    app = data.row(found[0])
    license_ids = {r[0] for r in ctx.conn.execute("SELECT license_id FROM license WHERE app_id = ?", (app_id,))}
    licenses = license_dicts(ctx, license_ids)
    contracts = _contracts(ctx, app_id)
    months = trend_periods(last_full_period(ctx, "month"), VOLUME_MONTHS)
    return App360Out(
        app=app,
        kpis=_app_kpis(ctx, app, data, contracts, licenses),
        volumes=volume_rows(ticket_scope(ctx, [app_id]), "incident", months),
        open_tickets=ticket_rows(ctx.conn, _open_ticket_ids(ctx, app_id), ctx.filters.include_drafts),
        changes=_changes(ctx, app_id),
        cost=_monthly_cost(ctx, app_id, [p.label for p in months]),
        contracts=contracts,
        licenses=[license_row(r) for r in licenses],
        jira=_jira(ctx, app_id),
        docs=_docs(ctx, app_id),
        findings=data.risks.get(app_id, []),
    )
