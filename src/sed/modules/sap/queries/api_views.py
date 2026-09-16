"""SAP dashboard read models: the /api/sap responses built from the L3 and change queries (read-only)."""

from __future__ import annotations

from typing import Any

from sed import metrics
from sed.api.findings import published_findings
from sed.api.models import FindingOut, Kpi
from sed.calendar import parse_utc
from sed.errors import ValidationFailed
from sed.modules.ops.queries.common import Context, last_full_period, series_end, trend_periods
from sed.modules.sap.api_models import (
    SapAging,
    SapAiRun,
    SapAiSubcategories,
    SapAiSubcategoryRow,
    SapAreaRow,
    SapAttentionRow,
    SapBacklogAreaRow,
    SapChangeRow,
    SapChangesOut,
    SapFailedImportRow,
    SapFlowRow,
    SapIdocAging,
    SapIdocErrorRow,
    SapIdocPartnerRow,
    SapIdocsOut,
    SapIdocSpikeRow,
    SapIdocTextRow,
    SapIdocTypeRow,
    SapIdocWeekRow,
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
from sed.modules.sap.idoc import load_idoc
from sed.modules.sap.queries import ai_labels, changes, idocs, l3
from sed.modules.sap.scope import Scope, load_scope
from sed.modules.sap.taxonomy import load_sap_taxonomy

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
    idoc_summary = idocs.summary(idocs.load(ctx.conn, load_idoc(ctx.paths, scope), at), week)
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
        _kpi("sap.idocs.errors_open", "IDocs in error", idoc_summary["errors_open"], "count"),
        _kpi(
            "sap.idocs.new_persistent",
            f"New persistent IDoc errors ({week.label})",
            idoc_summary["new_persistent_week"],
            "count",
            idoc_summary["new_persistent_avg4w"],
        ),
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
    at_iso = _at(ctx)
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
        ai_subcategories=_ai_subcategories(ctx, scope, periods, area, landscape),
    )


def _ai_subcategories(
    ctx: Context, scope: Scope, periods: list[Any], area: str | None, landscape: str | None
) -> SapAiSubcategories:
    b = ai_labels.breakdown(
        ctx.conn,
        scope,
        load_sap_taxonomy(ctx.paths),
        periods[0].start_iso,
        periods[-1].end_iso,
        area=area,
        landscape=landscape,
        include_drafts=ctx.filters.include_drafts,
    )
    return SapAiSubcategories(
        **{k: v for k, v in b.items() if k not in ("runs", "rows")},
        runs=[SapAiRun(**{k: r[k] for k in SapAiRun.model_fields}) for r in b["runs"]],
        rows=[SapAiSubcategoryRow(**r) for r in b["rows"]],
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
                ctx.conn,
                cs,
                since,
                at_iso,
                area=area,
                landscape=landscape,
                limit=LIST_LIMIT,
                min_lift=cs.charm.config.thresholds.incident_min_lift,
            )
        ],
        without_jira_count=len(without),
        without_jira=[SapChangeRow(**r) for r in without[:LIST_LIMIT]],
    )


def idocs_view(
    ctx: Context, system: str | None, landscape: str | None, area: str | None, direction: str | None, weeks: int
) -> SapIdocsOut:
    scope: Scope = load_scope(ctx.paths)
    scope.check_area(area)
    scope.check_landscape(landscape)
    sids = [s.sid for s in scope.config.systems]
    seen = [r[0] for r in ctx.conn.execute("SELECT DISTINCT system_id FROM sap_idoc ORDER BY 1")]
    systems = list(dict.fromkeys([*sids, *seen]))
    if system is not None and system not in systems:
        raise ValidationFailed(f"Unknown SAP system '{system}'", {"systems": systems})
    if direction not in (None, "inbound", "outbound"):
        raise ValidationFailed(f"Unknown IDoc direction '{direction}'", {"directions": ["inbound", "outbound"]})
    scope = scope.resolve(ctx.conn)
    at_iso = _at(ctx)
    at = parse_utc(at_iso)
    ids = idocs.load(ctx.conn, load_idoc(ctx.paths, scope), at)
    week = series_end(ctx, "week")
    periods = trend_periods(week, weeks)
    kw = {"system": system, "landscape": landscape, "area": area, "direction": direction}
    selected = idocs.select(ids, **kw)
    s = idocs.summary(ids, week, **kw)
    kpis = [
        _kpi("sap.idocs.errors_open", "IDocs in error", s["errors_open"], "count"),
        _kpi("sap.idocs.errors_aged", "Errors open > 48 h", s["errors_aged"], "count"),
        _kpi(
            "sap.idocs.new_persistent",
            f"New persistent errors ({week.label})",
            s["new_persistent_week"],
            "count",
            s["new_persistent_avg4w"],
        ),
        _kpi("sap.idocs.reprocess_median_h", f"Reprocessing median ({week.label})", s["reprocess_median_h"], "hours"),
        _kpi(
            "sap.idocs.reprocessed_in_grace_pct",
            f"Reprocessed within grace ({week.label})",
            s["reprocessed_within_grace_pct"],
            "pct",
        ),
    ]
    cs = changes.load(ctx.conn, load_charm(ctx.paths, scope), at)
    open_rows = idocs.open_errors(ids, selected, limit=LIST_LIMIT)
    labels = {
        x.sid: f"{x.sid} ({scope.landscape_labels.get(x.landscape, x.landscape)}, {x.role})"
        for x in scope.config.systems
    }
    return SapIdocsOut(
        as_of=ctx.as_of.isoformat(),
        at=at_iso,
        period=week.label,
        system=system,
        landscape=landscape,
        area=area,
        direction=direction,
        systems=[SapOption(value=sid, label=labels.get(sid, sid)) for sid in systems],
        areas=_options(scope.area_labels, l3.area_order(scope)),
        landscapes=_options(scope.landscape_labels, l3.landscape_order(scope)),
        kpis=kpis,
        aging=SapIdocAging(**idocs.aging(ids, selected)),
        by_type=[SapIdocTypeRow(**r) for r in idocs.backlog_by_type(ids, selected)[:LIST_LIMIT]],
        partners=[SapIdocPartnerRow(**r) for r in idocs.partners(ids, selected)],
        weekly=[SapIdocWeekRow(**r) for r in idocs.weekly(ctx.conn, ids, selected, periods, **kw)],
        top_texts=[SapIdocTextRow(**r) for r in idocs.top_texts(selected)],
        spikes=[
            SapIdocSpikeRow(**r)
            for r in idocs.spikes_after_imports(
                ids,
                cs,
                periods[0].start_iso,
                at_iso,
                errors=selected,
                system=system,
                landscape=landscape,
                min_lift=ids.idoc.config.thresholds.spike_min_lift,
            )[:LIST_LIMIT]
        ],
        open_errors_count=sum(1 for e in selected if e.is_open),
        open_errors=[SapIdocErrorRow(**r) for r in open_rows],
    )
