"""Ticket trend read models: volumes, SLA, MTTR and backlog (aging, groups, arrival/closure flow).

Semantics mirror `sed.metrics` (volume_trend, sla, mttr, backlog, group_flow) so dashboard numbers equal report
numbers; the implementations here read each window once and bucket in Python instead of issuing one query per
period.
"""

from __future__ import annotations

import statistics
from datetime import datetime
from typing import Any

from sed import metrics
from sed.calendar import Period, parse_utc
from sed.modules.ops.api_models import (
    AgingOut,
    BacklogGroupRow,
    BacklogOut,
    FlowRow,
    MttrOut,
    MttrRow,
    SlaOut,
    SlaPriorityRow,
    SlaRow,
    VolumeRow,
    VolumesOut,
)
from sed.modules.ops.queries.common import (
    Bucketer,
    Context,
    Window,
    clamp_end,
    pct,
    series_end,
    ticket_filter_sql,
    trend_periods,
    where_sql,
)

HOURS_EXPR = "(julianday(t.resolved_at) - julianday(t.opened_at)) * 24.0"
FLOW_WEEKS = 12
AGING_KEYS = ("d0_7", "d8_30", "d31_90", "d90p")


def met_expr(source: str) -> str:
    """SQL expression that is truthy when a resolved incident met its resolution SLA (same as metrics.sla)."""
    if source == "task_sla":
        return (
            "(COALESCE((SELECT MAX(s.has_breached) FROM task_sla s WHERE s.ticket_id = t.ticket_id "
            "AND s.sla_type = 'resolution'), t.made_sla = 0, 0) = 0)"
        )
    if source == "made_sla":
        return "COALESCE(t.made_sla, 1)"
    cases = " ".join(f"WHEN {p} THEN {h}" for p, h in metrics.INCIDENT_TARGET_H.items())
    return f"({HOURS_EXPR} <= (CASE t.priority {cases} ELSE 120 END))"


def _incident_where(ctx: Context, kind: str = "incident") -> tuple[str, list[Any]]:
    clauses, params = ticket_filter_sql(ctx.filters)
    return where_sql(["t.kind = ?", *clauses]), [kind, *params]


def resolved_rows(
    ctx: Context, start_iso: str, end_iso: str, *, source: str, columns: str = ""
) -> list[tuple[Any, ...]]:
    """(resolved_at, priority, met, hours, *columns) for incidents resolved in [start, end) matching the filters."""
    where, params = _incident_where(ctx)
    extra = f", {columns}" if columns else ""
    sql = (
        f"SELECT t.resolved_at, t.priority, {met_expr(source)} AS met, "
        f"CASE WHEN t.opened_at IS NOT NULL THEN {HOURS_EXPR} END AS hours{extra} "
        f"FROM ticket t WHERE {where} AND t.resolved_at >= ? AND t.resolved_at < ?"
    )
    return [tuple(r) for r in ctx.conn.execute(sql, [*params, start_iso, end_iso])]


def mttr_stats(hours: list[float]) -> dict[str, Any]:
    """Median/mean/P90 exactly as metrics.mttr."""
    values = sorted(hours)
    if not values:
        return {"count": 0, "median_h": None, "mean_h": None, "p90_h": None}
    p90 = values[min(len(values) - 1, round(0.9 * (len(values) - 1)))]
    return {
        "count": len(values),
        "median_h": round(statistics.median(values), 2),
        "mean_h": round(statistics.fmean(values), 2),
        "p90_h": round(p90, 2),
    }


def _priority_label(priority: int | None) -> str:
    return f"P{priority}" if priority else "P?"


def bucket_case(column: str, periods: list[Period]) -> tuple[str, list[str]]:
    """CASE expression giving the index of the consecutive period holding `column` (rows are pre-filtered to
    [first start, last end), so only the upper bounds need testing)."""
    return "CASE " + " ".join(f"WHEN {column} < ? THEN {i}" for i in range(len(periods))) + " END", [
        p.end_iso for p in periods
    ]


def volume_rows(ctx: Context, kind: str, periods: list[Period]) -> list[VolumeRow]:
    """Opened, resolved and net per consecutive period (same counts as metrics.volume_trend): one indexed range scan
    for opened_at and one for resolved_at, grouped by period in SQL."""
    where, params = _incident_where(ctx, kind)
    lo, hi = periods[0].start_iso, periods[-1].end_iso
    counts = [[0, 0] for _ in periods]
    for slot, column in ((0, "t.opened_at"), (1, "t.resolved_at")):
        case, bounds = bucket_case(column, periods)
        sql = (
            f"SELECT {case} AS bucket, COUNT(*) FROM ticket t WHERE {where} AND {column} >= ? AND {column} < ? "
            "GROUP BY 1"
        )
        for bucket, count in ctx.conn.execute(sql, [*bounds, *params, lo, hi]):
            if bucket is not None:
                counts[bucket][slot] = int(count)
    return [
        VolumeRow(period=p.label, opened=opened, resolved=resolved, net=opened - resolved)
        for p, (opened, resolved) in zip(periods, counts, strict=True)
    ]


def volumes(ctx: Context, granularity: str, n: int, kind: str) -> VolumesOut:
    periods = trend_periods(series_end(ctx, granularity), n)
    return VolumesOut(granularity=granularity, items=volume_rows(ctx, kind, periods))


def sla(ctx: Context, granularity: str, n: int) -> SlaOut:
    periods = trend_periods(series_end(ctx, granularity), n)
    source = metrics.sla_source(ctx.conn)
    buckets = Bucketer(periods)
    totals = [[0, 0] for _ in periods]
    by_priority: dict[int, list[int]] = {}
    for resolved_at, priority, met, _hours in resolved_rows(
        ctx, periods[0].start_iso, periods[-1].end_iso, source=source
    ):
        i = buckets.index(resolved_at)
        if i is None:
            continue
        hit = 1 if met else 0
        totals[i][0] += 1
        totals[i][1] += hit
        acc = by_priority.setdefault(int(priority or 0), [0, 0])
        acc[0] += 1
        acc[1] += hit
    items = [
        SlaRow(period=p.label, pct=pct(met, total), met=met, total=total)
        for p, (total, met) in zip(periods, totals, strict=True)
    ]
    prio = [
        SlaPriorityRow(priority=_priority_label(p), pct=pct(met, total), met=met, total=total)
        for p, (total, met) in sorted(by_priority.items())
    ]
    return SlaOut(granularity=granularity, sla_source=source, items=items, by_priority=prio)


def mttr(ctx: Context, granularity: str, n: int) -> MttrOut:
    periods = trend_periods(series_end(ctx, granularity), n)
    where, params = _incident_where(ctx)
    sql = (
        f"SELECT t.resolved_at, {HOURS_EXPR} FROM ticket t WHERE {where} AND t.resolved_at >= ? AND t.resolved_at < ? "
        "AND t.opened_at IS NOT NULL"
    )
    buckets = Bucketer(periods)
    hours: list[list[float]] = [[] for _ in periods]
    for resolved_at, value in ctx.conn.execute(sql, [*params, periods[0].start_iso, periods[-1].end_iso]):
        i = buckets.index(resolved_at)
        if i is not None and value is not None:
            hours[i].append(value)
    items = [MttrRow(period=p.label, **mttr_stats(h)) for p, h in zip(periods, hours, strict=True)]
    return MttrOut(granularity=granularity, items=items)


# ---------------------------------------------------------------------------
# backlog
# ---------------------------------------------------------------------------


def open_at_rows(ctx: Context, at_iso: str, kind: str = "incident") -> list[tuple[str, str | None, int]]:
    """(opened_at, assignment_group, stale_open) for tickets open at `at` (same predicate as metrics.backlog).

    The candidates are the tickets of this kind not resolved before `at` (resolved_at NULL or >= `at`), read through
    ix_ticket_kind_resolved, instead of every ticket opened before `at`; the CROSS JOIN keeps that join order.
    """
    clauses, params = ticket_filter_sql(ctx.filters)
    sql = (
        "SELECT t.opened_at, t.assignment_group, t.stale_open FROM ("
        "SELECT rowid AS rid FROM ticket WHERE kind = ? AND resolved_at IS NULL "
        "UNION ALL SELECT rowid FROM ticket WHERE kind = ? AND resolved_at >= ?) AS candidate "
        "CROSS JOIN ticket t ON t.rowid = candidate.rid "
        f"WHERE {where_sql(['t.opened_at < ?', '(t.closed_at IS NULL OR t.closed_at >= ?)', *clauses])}"
    )
    rows = ctx.conn.execute(sql, [kind, kind, at_iso, at_iso, at_iso, *params])
    return [(r[0], r[1], int(r[2] or 0)) for r in rows]


def aging_key(at: datetime, opened_at: str) -> str:
    age = (at - parse_utc(opened_at)).total_seconds() / 86400
    return "d0_7" if age <= 7 else "d8_30" if age <= 30 else "d31_90" if age <= 90 else "d90p"


def backlog_summary(ctx: Context, at_iso: str) -> dict[str, Any]:
    """Backlog at `at` excluding stale_open tickets, with the stale count and per-group aging."""
    at = parse_utc(at_iso)
    aging = dict.fromkeys(AGING_KEYS, 0)
    groups: dict[str | None, dict[str, int]] = {}
    total = stale = 0
    for opened_at, group, stale_open in open_at_rows(ctx, at_iso):
        if stale_open:
            stale += 1
            continue
        key = aging_key(at, opened_at)
        total += 1
        aging[key] += 1
        g = groups.setdefault(group, {**dict.fromkeys(AGING_KEYS, 0), "total": 0})
        g[key] += 1
        g["total"] += 1
    return {"total": total, "stale_excluded": stale, "aging": aging, "by_group": groups}


def flow(ctx: Context, periods: list[Period | Window]) -> list[FlowRow]:
    """Arrivals vs closures per assignment group per period (same counts as metrics.group_flow)."""
    if not periods:
        return []
    where, params = _incident_where(ctx)
    lo, hi = periods[0].start_iso, periods[-1].end_iso
    acc: dict[tuple[int, str | None], list[int]] = {}
    for slot, column in ((0, "t.opened_at"), (1, "t.resolved_at")):
        case = " ".join(f"WHEN {column} >= ? AND {column} < ? THEN {i}" for i in range(len(periods)))
        bounds = [b for p in periods for b in (p.start_iso, p.end_iso)]
        sql = (
            f"SELECT t.assignment_group, CASE {case} END AS bucket, COUNT(*) FROM ticket t WHERE {where} "
            f"AND {column} >= ? AND {column} < ? GROUP BY 1, 2"
        )
        for group, bucket, count in ctx.conn.execute(sql, [*bounds, *params, lo, hi]):
            if bucket is not None:
                acc.setdefault((bucket, group), [0, 0])[slot] += int(count)
    return [
        FlowRow(period=periods[i].label, group=group, arrived=arrived, closed=closed)
        for (i, group), (arrived, closed) in sorted(acc.items(), key=lambda kv: (kv[0][0], kv[0][1] or ""))
    ]


def backlog(ctx: Context) -> BacklogOut:
    """Backlog at the end of the as-of day (or at the end of the period filter, whichever is earlier), with the
    arrival/closure flow per group over the 12 weeks ending with the last full week."""
    at_iso = ctx.as_of_end_iso
    if ctx.filters.period:
        at_iso = min(ctx.parse(ctx.filters.period).end_iso, at_iso)
    summary = backlog_summary(ctx, at_iso)
    by_group = [
        BacklogGroupRow(group=group, **values)
        for group, values in sorted(summary["by_group"].items(), key=lambda kv: (-kv[1]["total"], kv[0] or ""))
    ]
    weeks = trend_periods(series_end(ctx, "week"), FLOW_WEEKS)
    windows: list[Period | Window] = [Window(p.label, p.start_iso, clamp_end(p, ctx)) for p in weeks]
    return BacklogOut(
        at=at_iso,
        total=summary["total"],
        stale_excluded=summary["stale_excluded"],
        aging=AgingOut(**summary["aging"]),
        by_group=by_group,
        flow=flow(ctx, windows),
    )
