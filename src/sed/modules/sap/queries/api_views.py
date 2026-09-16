"""SAP dashboard read models: the /api/sap responses built from the L3 and change queries (read-only)."""

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
    SapChangeRow,
    SapChangesOut,
    SapFailedImportRow,
    SapFlowRow,
    SapImportIncidentsRow,
    SapImportWeekRow,
    SapL3Out,
    SapLandscapeRow,
    SapOption,
    SapOverview,
    SapSlaPriorityRow,
    SapStageRow,
    SapStuckChangeRow,
    SapTrendRow,
    SapUrgentAreaRow,
    SapWaitingTransportRow,
)
from sed.modules.sap.charm import load_charm
from sed.modules.sap.definitions import DEFINITIONS
from sed.modules.sap.queries import changes, l3
from sed.modules.sap.scope import Scope, load_scope

FLOW_WEEKS = 8
FINDING_LIMIT = 200
LIST_LIMIT = 100


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
    cs = changes.load(ctx.conn, load_charm(ctx.paths, scope), at)
    change_summary = changes.summary(ctx.conn, cs, week)
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
        _kpi("sap.changes.open", "Open SAP changes", change_summary["open"], "count"),
        _kpi(
            "sap.changes.urgent_ratio_8w",
            "Urgent changes (8 weeks)",
            change_summary["urgent_ratio_8w"],
            "pct",
            change_summary["urgent_ratio_previous_8w"],
        ),
        _kpi("sap.transports.failed_4w", "Failed transport imports (28 days)", change_summary["failed_4w"], "count"),
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


def _at(ctx: Context) -> str:
    at_iso = ctx.as_of_end_iso
    if ctx.filters.period:
        at_iso = min(ctx.parse(ctx.filters.period).end_iso, at_iso)
    return at_iso


def changes_view(ctx: Context, area: str | None, landscape: str | None, weeks: int) -> SapChangesOut:
    scope: Scope = load_scope(ctx.paths)
    scope.check_area(area)
    scope.check_landscape(landscape)
    scope = scope.resolve(ctx.conn)
    at_iso = _at(ctx)
    cs = changes.load(ctx.conn, load_charm(ctx.paths, scope), parse_utc(at_iso))
    week = series_end(ctx, "week")
    periods = trend_periods(week, weeks)
    window = trend_periods(week, 8)
    previous = [window[0].previous(k) for k in range(8, 0, -1)]
    selected = changes.select(cs, area=area, landscape=landscape)
    s = changes.summary(ctx.conn, cs, week, area=area, landscape=landscape)
    kpis = [
        _kpi("sap.changes.open", "Open changes", s["open"], "count"),
        _kpi(
            "sap.changes.urgent_ratio_8w",
            "Urgent changes (8 weeks)",
            s["urgent_ratio_8w"],
            "pct",
            s["urgent_ratio_previous_8w"],
        ),
        _kpi("sap.changes.stuck", "Stuck changes", s["stuck"], "count"),
        _kpi("sap.changes.without_jira", "Without a Jira story", s["without_jira"], "count"),
        _kpi("sap.changes.prod_imports", f"Production imports ({week.label})", s["prod_imports_week"], "count"),
        _kpi("sap.transports.failed_4w", "Failed imports (28 days)", s["failed_4w"], "count"),
        _kpi("sap.transports.waiting", "Waiting for production", s["waiting"], "count"),
    ]
    without = changes.without_jira(cs, selected)
    since = periods[0].start_iso
    return SapChangesOut(
        as_of=ctx.as_of.isoformat(),
        at=at_iso,
        period=week.label,
        area=area,
        landscape=landscape,
        areas=_options(scope.area_labels, l3.area_order(scope)),
        landscapes=_options(scope.landscape_labels, l3.landscape_order(scope)),
        kpis=kpis,
        stages=[SapStageRow(**r) for r in changes.stage_matrix(selected)],
        urgent_by_area=[
            SapUrgentAreaRow(**r) for r in changes.urgent_by_area(cs, window, previous, landscape=landscape)
        ],
        production_imports=[
            SapImportWeekRow(**r) for r in changes.production_imports(cs, periods, area=area, landscape=landscape)
        ],
        stuck=[SapStuckChangeRow(**r) for r in changes.stuck(cs, selected)[:LIST_LIMIT]],
        waiting=[
            SapWaitingTransportRow(**r)
            for r in changes.waiting_for_production(cs, area=area, landscape=landscape)[:LIST_LIMIT]
        ],
        failed=[
            SapFailedImportRow(**r)
            for r in changes.failed_imports(cs, since, area=area, landscape=landscape)[:LIST_LIMIT]
        ],
        incidents_after_imports=[
            SapImportIncidentsRow(**r)
            for r in changes.incidents_after_imports(
                ctx.conn, cs, since, at_iso, area=area, landscape=landscape, limit=LIST_LIMIT
            )
        ],
        without_jira_count=len(without),
        without_jira=[SapChangeRow(**r) for r in without[:LIST_LIMIT]],
    )
