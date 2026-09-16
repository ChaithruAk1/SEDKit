"""SAP dashboard read models: the /api/sap responses built from the L3 queries (read-only, no refresh)."""

from __future__ import annotations

from sed import metrics
from sed.api.findings import published_findings
from sed.api.models import FindingOut, Kpi
from sed.calendar import parse_utc
from sed.modules.ops.queries.common import Context, last_full_period, series_end, trend_periods
from sed.modules.sap.api_models import (
    SapAging,
    SapAreaRow,
    SapAttentionRow,
    SapBacklogAreaRow,
    SapFlowRow,
    SapL3Out,
    SapLandscapeRow,
    SapOption,
    SapOverview,
    SapSlaPriorityRow,
    SapTrendRow,
)
from sed.modules.sap.definitions import DEFINITIONS
from sed.modules.sap.queries import l3
from sed.modules.sap.scope import Scope, load_scope

FLOW_WEEKS = 8
FINDING_LIMIT = 200


def _kpi(key: str, label: str, value: float | int | None, unit: str, compare: float | None = None) -> Kpi:
    delta = round(value - compare, 2) if isinstance(value, int | float) and compare is not None else None
    definition = DEFINITIONS.get(key, (None, None))[1]
    return Kpi(key=key, label=label, value=value, unit=unit, compare=compare, delta=delta, definition=definition)


def sap_findings(ctx: Context) -> list[FindingOut]:
    from sed.modules import get

    items: list[FindingOut] = []
    for kind in get("sap").finding_kinds:
        items += published_findings(ctx.conn, ctx.as_of, kind=kind, limit=FINDING_LIMIT)
    rank = {"critical": 3, "high": 2, "medium": 1}
    return sorted(items, key=lambda f: (-rank.get(f.severity or "", 0), f.kind, f.title))


def _options(labels: dict[str, str], order: list[str]) -> list[SapOption]:
    return [SapOption(value=code, label=labels[code]) for code in order]


def overview(ctx: Context) -> SapOverview:
    scope = load_scope(ctx.paths).resolve(ctx.conn)
    week = last_full_period(ctx, "week")
    previous = [week.previous(k) for k in range(4, 0, -1)]
    source = metrics.sla_source(ctx.conn)
    at = parse_utc(ctx.as_of_end_iso)
    kpis_week = l3.week_kpis(ctx.conn, scope, week, previous, source)
    backlog = l3.backlog(ctx.conn, scope, at)
    p1p2_open = metrics.backlog(ctx.conn, l3.filters(scope, priorities=[1, 2]), at)["total"]
    findings = sap_findings(ctx)
    kpis = [
        _kpi("sap.l3.backlog", "Open SAP incidents", backlog["total"], "count"),
        _kpi(
            "sap.l3.aged_30d",
            "Open for more than 30 days",
            backlog["aging"]["d31_90"] + backlog["aging"]["d90p"],
            "count",
        ),
        _kpi("sap.l3.opened", f"Opened ({week.label})", kpis_week["opened"], "count", kpis_week["opened_avg"]),
        _kpi("sap.l3.resolved", f"Resolved ({week.label})", kpis_week["resolved"], "count", kpis_week["resolved_avg"]),
        _kpi("sap.l3.sla.pct", f"SLA met ({week.label})", kpis_week["sla_pct"], "pct", kpis_week["sla_pct_avg"]),
        _kpi(
            "sap.l3.mttr.median_h",
            f"MTTR median ({week.label})",
            kpis_week["mttr_median_h"],
            "hours",
            kpis_week["mttr_median_h_avg"],
        ),
        _kpi("sap.l3.p1p2.open", "Open P1/P2", p1p2_open, "count"),
        _kpi("sap.findings.count", "System-detected SAP risks", len(findings), "count"),
    ]
    return SapOverview(
        as_of=ctx.as_of.isoformat(),
        period=week.label,
        data_as_of_last_import=ctx.data_as_of.isoformat() if ctx.data_as_of else None,
        configured=scope.configured,
        kpis=kpis,
        areas=[SapAreaRow(**r) for r in l3.area_summary(ctx.conn, scope, week, at, source)],
        landscapes=[SapLandscapeRow(**r) for r in l3.backlog_by_landscape(ctx.conn, scope, at)],
        findings=findings,
    )


def l3_view(ctx: Context, area: str | None, landscape: str | None, weeks: int) -> SapL3Out:
    scope: Scope = load_scope(ctx.paths)
    scope.check_area(area)
    scope.check_landscape(landscape)
    scope = scope.resolve(ctx.conn)
    at_iso = ctx.as_of_end_iso
    if ctx.filters.period:
        at_iso = min(ctx.parse(ctx.filters.period).end_iso, at_iso)
    at = parse_utc(at_iso)
    source = metrics.sla_source(ctx.conn)
    periods = trend_periods(series_end(ctx, "week"), weeks)
    backlog = l3.backlog(ctx.conn, scope, at, area=area, landscape=landscape)
    sla = metrics.sla(ctx.conn, l3.filters(scope, area=area, landscape=landscape), periods[-1], source)
    attention = l3.attention(ctx.conn, scope, at, ctx.settings.thresholds, area=area, landscape=landscape)
    return SapL3Out(
        as_of=ctx.as_of.isoformat(),
        at=at_iso,
        sla_source=source,
        area=area,
        landscape=landscape,
        areas=_options(scope.area_labels, l3.area_order(scope)),
        landscapes=_options(scope.landscape_labels, l3.landscape_order(scope)),
        backlog_total=backlog["total"],
        aging=SapAging(**backlog["aging"]),
        by_area=[SapBacklogAreaRow(**r) for r in backlog["by_area"]],
        by_landscape=[SapLandscapeRow(**r) for r in l3.backlog_by_landscape(ctx.conn, scope, at, area=area)],
        trend=[SapTrendRow(**r) for r in l3.trend(ctx.conn, scope, periods, source, area=area, landscape=landscape)],
        flow=[SapFlowRow(**r) for r in l3.flow_by_area(ctx.conn, scope, periods[-FLOW_WEEKS:], landscape=landscape)],
        sla_by_priority=[
            SapSlaPriorityRow(priority=f"P{p}" if p else "P?", total=v["total"], met=v["met"], pct=v["pct"])
            for p, v in sorted(sla["by_priority"].items())
        ],
        attention_count=attention["count"],
        attention=[SapAttentionRow(**{k: r.get(k) for k in SapAttentionRow.model_fields}) for r in attention["items"]],
    )
