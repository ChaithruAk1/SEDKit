"""Costs, contract renewals, license utilization and vendor SLA trends.

Renewals and license utilization reuse `sed.metrics` (the same numbers as the reports and rule findings) and narrow
the result to the ids matching the entity filters. Cost and vendor-trend SQL lives here because the dashboard filters
(app, family, vendor, group) must apply inside the aggregate; the semantics mirror metrics.cost_vs_budget and
metrics.vendor_sla_trend.
"""

from __future__ import annotations

import statistics
from datetime import date
from typing import Any

from sed import metrics
from sed.calendar import parse_period, shift_label
from sed.modules.ops.api_models import (
    CostRow,
    CostsOut,
    LicenseRow,
    LicensesOut,
    RenewalRow,
    RenewalsOut,
    VendorPoint,
    VendorTrendRow,
    VendorTrendsOut,
)
from sed.modules.ops.queries.common import (
    Bucketer,
    Context,
    allowed_ids,
    entity_filter_sql,
    marks,
    months_before,
    pct,
    period_months,
    ticket_filter_sql,
    where_sql,
)

UNDER_USED = 0.70
GROUP_COLUMNS = {
    "app": (
        "COALESCE(c.app_id, c.app_raw, '(unattributed)')",
        "COALESCE(a.name, c.app_raw, '(unattributed)')",
    ),
    "vendor": (
        "COALESCE(c.vendor_id, c.vendor_raw, '(no vendor)')",
        "COALESCE(v.name, c.vendor_raw, '(no vendor)')",
    ),
    "category": ("COALESCE(c.cost_category, '(none)')", "COALESCE(c.cost_category, '(none)')"),
    "app_category": (
        "COALESCE(a.name, c.app_raw) || ' / ' || COALESCE(c.cost_category, '')",
        "COALESCE(a.name, c.app_raw) || ' / ' || COALESCE(c.cost_category, '')",
    ),
}


def latest_budget_version(ctx: Context) -> str | None:
    return ctx.conn.execute("SELECT MAX(as_of) FROM cost_line WHERE line_type = 'budget'").fetchone()[0]


def cost_rows(ctx: Context, months: list[str], group_by: str) -> list[CostRow]:
    """Actual vs budget (latest budget version) per group for the given months, largest actual first."""
    if not months:
        return []
    key_sql, label_sql = GROUP_COLUMNS[group_by]
    clauses, params = entity_filter_sql(ctx.filters, "c")
    sql = (
        f"SELECT {key_sql} AS key, {label_sql} AS label, "
        "SUM(CASE WHEN c.line_type = 'actual' THEN c.amount_base END) AS actual, "
        "SUM(CASE WHEN c.line_type = 'budget' AND c.as_of IS ? THEN c.amount_base END) AS budget "
        "FROM cost_line c LEFT JOIN application a ON a.app_id = c.app_id "
        "LEFT JOIN vendor v ON v.vendor_id = c.vendor_id "
        f"WHERE c.period IN ({marks(months)}) AND {where_sql(clauses)} GROUP BY key ORDER BY actual DESC, key"
    )
    out = []
    for r in ctx.conn.execute(sql, [latest_budget_version(ctx), *months, *params]):
        actual, budget = r["actual"] or 0.0, r["budget"]
        out.append(
            CostRow(
                key=str(r["key"]),
                label=str(r["label"]),
                actual=round(actual, 2),
                budget=round(budget, 2) if budget else None,
                variance_pct=round(100.0 * (actual - budget) / budget, 2) if budget else None,
            )
        )
    return out


def cost_totals(ctx: Context, months: list[str]) -> tuple[float | None, float | None]:
    """(actual, budget) totals over the months with the entity filters; None when nothing matched."""
    if not months:
        return None, None
    clauses, params = entity_filter_sql(ctx.filters, "c")
    row = ctx.conn.execute(
        "SELECT SUM(CASE WHEN c.line_type = 'actual' THEN c.amount_base END), "
        "SUM(CASE WHEN c.line_type = 'budget' AND c.as_of IS ? THEN c.amount_base END) FROM cost_line c "
        f"WHERE c.period IN ({marks(months)}) AND {where_sql(clauses)}",
        [latest_budget_version(ctx), *months, *params],
    ).fetchone()
    actual, budget = row[0], row[1]
    return (round(actual, 2) if actual is not None else None, round(budget, 2) if budget is not None else None)


def costs(ctx: Context, group_by: str, months_n: int) -> CostsOut:
    if ctx.filters.period:
        months = period_months(ctx.parse(ctx.filters.period), ctx)
    else:
        months = months_before(ctx.as_of, months_n)
    rows = cost_rows(ctx, months, group_by)
    total_actual, total_budget = cost_totals(ctx, months)
    return CostsOut(group_by=group_by, months=months, rows=rows, total_actual=total_actual, total_budget=total_budget)


def _bool(value: Any) -> bool | None:
    return None if value is None else bool(value)


def renewal_rows(ctx: Context, days: int) -> list[RenewalRow]:
    """metrics.renewals within `days` of as-of, narrowed to the entity filters."""
    ids = allowed_ids(ctx, "contract", "contract_id")
    return [
        RenewalRow(
            contract_id=r["contract_id"],
            contract_number=r["contract_number"],
            vendor=r["vendor"],
            app=r["app"],
            product=r["product"],
            end_date=r["end_date"],
            days_to_end=r["days_to_end"],
            notice_deadline=r["notice_deadline"],
            days_to_notice=r["days_to_notice"],
            auto_renew=_bool(r["auto_renew"]),
            renewal_status=r["renewal_status"],
            annual_value_base=r["annual_value_base"],
        )
        for r in metrics.renewals(ctx.conn, ctx.as_of, days)
        if ids is None or r["contract_id"] in ids
    ]


def renewals(ctx: Context, days: int) -> RenewalsOut:
    return RenewalsOut(as_of=ctx.as_of.isoformat(), days=days, items=renewal_rows(ctx, days))


def license_dicts(ctx: Context, license_ids: set[str] | None = None) -> list[dict[str, Any]]:
    ids = allowed_ids(ctx, "license", "license_id") if license_ids is None else license_ids
    return [r for r in metrics.license_utilization(ctx.conn, ctx.as_of) if ids is None or r["license_id"] in ids]


def license_row(r: dict[str, Any]) -> LicenseRow:
    return LicenseRow(
        license_id=r["license_id"],
        app=r["app"],
        vendor=r["vendor"],
        product=r["product"],
        entitled_qty=r["entitled_qty"],
        assigned_qty=r["assigned_qty"],
        active_qty_90d=r["active_qty_90d"],
        utilization=r["utilization"],
        assigned_ratio=r["assigned_ratio"],
        unit_cost_base=r["unit_cost_base"],
        idle_cost_base=r["idle_cost_base"],
    )


def licenses(ctx: Context) -> LicensesOut:
    rows = license_dicts(ctx)
    return LicensesOut(
        as_of=ctx.as_of.isoformat(),
        idle_cost_total=round(sum(r["idle_cost_base"] or 0 for r in rows), 2),
        items=[license_row(r) for r in rows],
    )


def under_used_idle_cost(rows: list[dict[str, Any]]) -> float:
    """Idle cost of under-used lines (the weekly report's license.idle_cost fact)."""
    return round(
        sum(r["idle_cost_base"] or 0 for r in rows if r["utilization"] is not None and r["utilization"] < UNDER_USED),
        2,
    )


def vendor_trend(ctx: Context, months: int, *, min_tickets: int = 20) -> VendorTrendsOut:
    """Monthly SLA %, MTTR and reassignments per vendor, same rules as metrics.vendor_sla_trend, with filters."""
    as_of: date = ctx.as_of
    last = parse_period(shift_label(f"{as_of.year}-{as_of.month:02d}", -1), ctx.tz)
    periods = [parse_period(shift_label(last.label, -k), ctx.tz) for k in range(months - 1, -1, -1)]
    source = metrics.sla_source(ctx.conn)
    breach = {
        "task_sla": "(SELECT MAX(has_breached) FROM task_sla s WHERE s.ticket_id = t.ticket_id "
        "AND s.sla_type = 'resolution')",
        "made_sla": "(1 - COALESCE(t.made_sla, 1))",
    }.get(source, "0")
    clauses, params = ticket_filter_sql(ctx.filters)
    sql = (
        "SELECT t.vendor_id, v.name, t.resolved_at, (julianday(t.resolved_at) - julianday(t.opened_at)) * 24.0, "
        f"COALESCE({breach}, 1 - COALESCE(t.made_sla, 1)), COALESCE(t.reassignment_count, 0) "
        "FROM ticket t JOIN vendor v ON v.vendor_id = t.vendor_id WHERE t.kind = 'incident' "
        f"AND t.resolved_at >= ? AND t.resolved_at < ? AND {where_sql(clauses)}"
    )
    buckets = Bucketer(periods)
    acc: dict[str, dict[str, Any]] = {}
    for row_vendor, row_name, resolved_at, hours, breached, reassign in ctx.conn.execute(
        sql, [periods[0].start_iso, periods[-1].end_iso, *params]
    ):
        i = buckets.index(resolved_at)
        if i is None:
            continue
        entry = acc.setdefault(
            row_vendor,
            {"name": row_name, "buckets": [{"n": 0, "met": 0, "hours": [], "reassign": 0} for _ in periods]},
        )
        b = entry["buckets"][i]
        b["n"] += 1
        b["met"] += 0 if breached else 1
        b["reassign"] += reassign
        if hours is not None:
            b["hours"].append(hours)
    items = []
    for vendor_id, v in acc.items():
        series = [
            VendorPoint(
                period=p.label,
                sla_pct=pct(b["met"], b["n"]),
                mttr_median_h=round(statistics.median(b["hours"]), 2) if b["hours"] else None,
                reassign_avg=round(b["reassign"] / b["n"], 2) if b["n"] else None,
                tickets=b["n"],
            )
            for p, b in zip(periods, v["buckets"], strict=True)
        ]
        valid = [x for x in series if x.tickets >= min_tickets and x.sla_pct is not None]
        delta = None
        if len(valid) >= 6:
            recent = [x.sla_pct for x in valid[-3:] if x.sla_pct is not None]
            prior = [x.sla_pct for x in valid[-6:-3] if x.sla_pct is not None]
            delta = round(statistics.fmean(recent) - statistics.fmean(prior), 2)
        items.append(VendorTrendRow(vendor_id=vendor_id, vendor=v["name"], delta_pp=delta, series=series))
    items.sort(key=lambda x: (x.delta_pp is None, x.delta_pp or 0))
    return VendorTrendsOut(months=months, items=items)
