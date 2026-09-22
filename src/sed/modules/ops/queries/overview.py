"""Overview KPIs and the Needs-attention list.

The overview period defaults to the last full ISO week on or before as-of, so its ticket KPIs equal the weekly
report's facts for that week. Commercial KPIs (costs YTD, renewals, notice deadlines, idle licenses) are as of the
as-of date. `top_risks` comes from `sed.api.findings.published_findings` (no rule refresh on a GET).
"""

from __future__ import annotations

from sed import metrics
from sed.api.findings import published_findings
from sed.api.models import FindingOut, FreshnessRow
from sed.calendar import as_of_end_utc
from sed.modules.ops.api_models import AttentionOut, AttentionRow, OpsOverview
from sed.modules.ops.queries.commercial import (
    cost_totals,
    license_dicts,
    renewal_rows,
    under_used_idle_cost,
)
from sed.modules.ops.queries.common import (
    FINDINGS_SCAN_LIMIT,
    AppIndex,
    Bucketer,
    Context,
    Window,
    data_as_of_text,
    freshness_rows,
    has_entity_filter,
    has_tickets_in,
    kpi,
    metrics_filters,
    pct,
    review_queue_count,
    selected_period,
    ticket_filter_sql,
    where_sql,
    window,
    ytd_months,
)
from sed.modules.ops.queries.tickets import backlog_summary, mttr_stats, resolved_rows
from sed.modules.ops.reports.queries import license_low_threshold

TOP_RISKS = 5


def scoped_findings(ctx: Context, limit: int) -> list[FindingOut]:
    """Published findings, narrowed to the filtered applications (and vendor) when an entity filter is set."""
    if not has_entity_filter(ctx.filters):
        return published_findings(ctx.conn, ctx.as_of, limit=limit)
    f = ctx.filters
    clauses, params = [], []
    if f.app:
        clauses.append(f"app_id IN ({', '.join('?' for _ in f.app)})")
        params += list(f.app)
    if f.family:
        clauses.append("app_family = ?")
        params.append(f.family)
    if f.vendor:
        clauses.append("primary_vendor_id = ?")
        params.append(f.vendor)
    apps = {r[0] for r in ctx.conn.execute(f"SELECT app_id FROM application WHERE {where_sql(clauses)}", params)}
    index = AppIndex.load(ctx.conn)
    out = []
    for finding in published_findings(ctx.conn, ctx.as_of, limit=FINDINGS_SCAN_LIMIT):
        vendor_hit = bool(f.vendor) and finding.subject_type == "vendor" and finding.subject_id == f.vendor
        if vendor_hit or apps.intersection(index.apps_for(finding)):
            out.append(finding)
            if len(out) >= limit:
                break
    return out


def attention_data(ctx: Context, limit: int) -> dict:
    at = as_of_end_utc(ctx.as_of, ctx.tz)
    return metrics.attention(ctx.conn, metrics_filters(ctx.filters), at, ctx.settings.thresholds, limit)


def attention(ctx: Context, limit: int) -> AttentionOut:
    data = attention_data(ctx, limit)
    items = [
        AttentionRow(
            ticket_id=f"incident:{r['number']}",
            number=r["number"],
            priority=r["priority"],
            app=r["app"],
            state=r["state"],
            assignment_group=r["assignment_group"],
            assigned_to_pid=r["assigned_to_pid"],
            opened_at=r["opened_at"],
            age_days=r["age_days"],
            reasons=[x for x in r["reasons"].split(", ") if x],
            short_description=r["short_description"],
        )
        for r in data["items"]
    ]
    return AttentionOut(
        as_of=ctx.as_of.isoformat(),
        data_as_of_last_import=data_as_of_text(ctx),
        count=data["count"],
        by_reason=data["by_reason"],
        items=items,
    )


def _p1p2_counts(ctx: Context, windows: list[Window]) -> list[int]:
    clauses, params = ticket_filter_sql(ctx.filters)
    sums = ", ".join("SUM(t.opened_at >= ? AND t.opened_at < ?)" for _ in windows)
    bounds = [b for w in windows for b in (w.start_iso, w.end_iso)]
    lo, hi = min(w.start_iso for w in windows), max(w.end_iso for w in windows)
    row = ctx.conn.execute(
        f"SELECT {sums} FROM ticket t WHERE {where_sql(['t.kind = ?', *clauses])} AND t.priority <= 2 "
        "AND t.opened_at >= ? AND t.opened_at < ?",
        [*bounds, "incident", *params, lo, hi],
    ).fetchone()
    return [int(v or 0) for v in row]


def overview(ctx: Context) -> OpsOverview:
    conn, f = ctx.conn, ctx.filters
    period = selected_period(ctx, "week")
    current = window(period, ctx)
    prev_period = period.previous()
    previous = Window(prev_period.label, prev_period.start_iso, prev_period.end_iso)

    backlog_now = backlog_summary(ctx, current.end_iso)
    backlog_prev = backlog_summary(ctx, previous.end_iso)

    source = metrics.sla_source(conn)
    windows = [previous, current]
    buckets = Bucketer(windows)
    sla_acc = [[0, 0], [0, 0]]
    hours: list[list[float]] = [[], []]
    for resolved_at, _priority, met, value in resolved_rows(ctx, previous.start_iso, current.end_iso, source=source):
        i = buckets.index(resolved_at)
        if i is None:
            continue
        sla_acc[i][0] += 1
        sla_acc[i][1] += 1 if met else 0
        if value is not None:
            hours[i].append(value)
    sla_prev, sla_now = (pct(met, total) for total, met in sla_acc)
    mttr_prev, mttr_now = (mttr_stats(h)["median_h"] for h in hours)
    p1p2_prev, p1p2_now = _p1p2_counts(ctx, windows)

    months = ytd_months(ctx)
    actual_ytd, budget_ytd = cost_totals(ctx, months)
    variance = (
        round(100.0 * (actual_ytd - budget_ytd) / budget_ytd, 2) if actual_ytd is not None and budget_ytd else None
    )
    renewals_90 = renewal_rows(ctx, 90)
    idle = under_used_idle_cost(license_dicts(ctx), license_low_threshold(ctx.paths))
    queue = review_queue_count(conn)
    attention_count = attention_data(ctx, 1)["count"]
    currency = ctx.settings.base_currency.lower()

    # Nothing in the comparison window means no comparison, rather than a "fall" from zero (see has_tickets_in).
    comparable = has_tickets_in(ctx, prev_period)
    kpis = [
        kpi(
            "inc.backlog",
            "Open incident backlog",
            backlog_now["total"],
            "count",
            compare=backlog_prev["total"] if comparable else None,
        ),
        kpi("inc.sla.pct", "SLA met", sla_now, "pct", compare=sla_prev if comparable else None),
        kpi("inc.mttr.median_h", "MTTR median (hours)", mttr_now, "hours", compare=mttr_prev if comparable else None),
        kpi("inc.p1p2.opened", "P1/P2 opened", p1p2_now, "count", compare=p1p2_prev if comparable else None),
        kpi("cost.actual.ytd", "Spend YTD", actual_ytd, currency, compare=budget_ytd),
        kpi("cost.budget.ytd", "Budget YTD", budget_ytd, currency),
        kpi("cost.variance.ytd_pct", "Spend vs budget YTD", variance, "pct"),
        kpi(
            "renewals.90d.count",
            "Contracts ending within 90 days",
            sum(1 for r in renewals_90 if r.days_to_end is not None and 0 <= r.days_to_end <= 90),
            "count",
        ),
        kpi(
            "notice.30d.count",
            "Notice deadlines within 30 days",
            sum(1 for r in renewals_90 if r.days_to_notice is not None and 0 <= r.days_to_notice <= 30),
            "count",
        ),
        kpi("license.idle_cost", "Idle license cost (under-used lines)", idle, currency),
        kpi("review.queue.count", "Review queue", queue, "count"),
    ]
    return OpsOverview(
        period=period.label,
        as_of=ctx.as_of.isoformat(),
        data_as_of_last_import=data_as_of_text(ctx),
        kpis=kpis,
        attention_count=attention_count,
        stale_open=metrics.stale_open_count(conn, metrics_filters(f)),
        top_risks=scoped_findings(ctx, TOP_RISKS),
        review_queue_count=queue,
        freshness=[FreshnessRow(**r) for r in freshness_rows(conn)],
    )
