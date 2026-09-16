"""Weekly SAP Operations Review snapshot builder (sap-weekly): SAP L3 support for one ISO week.

Aggregates of the week use `req.window` (the week clamped to the data date, labelled "to date" when open); backlog and
attention are measured at the end of that window; rule findings use `req.as_of`.
"""

from __future__ import annotations

from sed import metrics, rule_findings
from sed.modules.sap.queries import l3
from sed.modules.sap.scope import Scope, load_scope
from sed.reports.snapshot import SnapshotParts, SnapshotRequest, fact, table


def _delta_pct(current: float | None, baseline: float | None) -> float | None:
    if current is None or not baseline:
        return None
    return round(100.0 * (current - baseline) / baseline, 1)


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _scope_note(scope: Scope) -> str:
    c = scope.config
    parts = [
        _count(len(c.groups), "SAP group"),
        _count(len(c.categories), "category name"),
        _count(len(c.custom_fields), "custom field"),
    ]
    return "SAP scope: " + ", ".join(parts) + " (config/sap/scope.yaml)"


def build(req: SnapshotRequest) -> SnapshotParts:
    conn, paths, settings, period, window = req.conn, req.paths, req.settings, req.period, req.window
    scope = load_scope(paths).resolve(conn)
    partial = window.end_local < period.end_local
    suffix = " (to date)" if partial else ""
    previous = [period.previous(k) for k in range(4, 0, -1)]
    src = metrics.sla_source(conn)
    f = l3.filters(scope)

    week = l3.week_kpis(conn, scope, window, previous, src)
    trend = l3.trend(conn, scope, [period.previous(k) for k in range(11, 0, -1)] + [window], src)
    at = window.end_utc
    backlog_now = l3.backlog(conn, scope, at)
    backlog_now_all = metrics.backlog(conn, f, at, exclude_stale=False)["total"]
    backlog_start_all = metrics.backlog(conn, f, period.start_utc, exclude_stale=False)["total"]
    sla_now = metrics.sla(conn, f, window, src)
    p1p2 = metrics.volume_trend(conn, l3.filters(scope, priorities=[1, 2]), [window])[0]["opened"]
    att = l3.attention(conn, scope, at, settings.thresholds)
    areas = l3.area_summary(conn, scope, window, at, src)
    flow = l3.flow_by_area(conn, scope, [period.previous(k) for k in range(7, 0, -1)] + [window])
    findings = rule_findings.as_of_findings(conn, paths, req.as_of, "sap")
    aged = backlog_now["aging"]["d31_90"] + backlog_now["aging"]["d90p"]

    facts = {
        "period.label": fact(period.label, "text", "Period"),
        "period.start": fact(period.start_local.isoformat(), "date", "Period start"),
        "period.end": fact(period.last_day.isoformat(), "date", "Period end"),
        "sap.scope.note": fact(_scope_note(scope), "text", "SAP scope"),
        "sap.l3.opened": fact(week["opened"], "count", f"SAP incidents opened{suffix}", "sap.l3.opened"),
        "sap.l3.opened.avg4w": fact(week["opened_avg"], "number", "Opened, 4-week average", "sap.l3.opened"),
        # A partial week's count is not comparable with full-week averages, so no delta is reported for it.
        "sap.l3.opened.delta_vs_avg4w_pct": fact(
            None if partial else _delta_pct(week["opened"], week["opened_avg"]), "pct", "Opened vs 4-week avg"
        ),
        "sap.l3.resolved": fact(week["resolved"], "count", f"SAP incidents resolved{suffix}", "sap.l3.resolved"),
        "sap.l3.resolved.avg4w": fact(week["resolved_avg"], "number", "Resolved, 4-week average", "sap.l3.resolved"),
        "sap.l3.backlog": fact(
            backlog_now["total"],
            "count",
            "Open SAP backlog at the as-of date" if partial else "Open SAP backlog at week end",
            "sap.l3.backlog",
        ),
        "sap.l3.backlog.delta": fact(
            backlog_now_all - backlog_start_all, "count", "SAP backlog change (arrivals - resolutions)"
        ),
        "sap.l3.aged_30d": fact(aged, "count", "Open for more than 30 days", "sap.l3.aged_30d"),
        "sap.l3.sla.pct": fact(sla_now["pct"], "pct", f"SLA met (resolved this week){suffix}", "sap.l3.sla.pct"),
        "sap.l3.sla.pct.avg4w": fact(week["sla_pct_avg"], "pct", "SLA met, 4-week average", "sap.l3.sla.pct"),
        "sap.l3.sla.delta_pp_vs_4w": fact(
            round(sla_now["pct"] - week["sla_pct_avg"], 2)
            if sla_now["pct"] is not None and week["sla_pct_avg"] is not None
            else None,
            "pp",
            "SLA vs 4-week avg",
        ),
        "sap.l3.sla.source": fact(src, "text", "SLA source"),
        "sap.l3.mttr.median_h": fact(
            week["mttr_median_h"], "hours", f"MTTR median (hours){suffix}", "sap.l3.mttr.median_h"
        ),
        "sap.l3.mttr.median_h.avg4w": fact(
            week["mttr_median_h_avg"], "hours", "MTTR median, 4-week average", "sap.l3.mttr.median_h"
        ),
        "sap.l3.p1p2.opened": fact(p1p2, "count", f"P1/P2 opened{suffix}", "sap.l3.p1p2.opened"),
        "sap.l3.attention.count": fact(
            att["count"], "count", "SAP tickets needing attention", "sap.l3.attention.count"
        ),
        "sap.findings.count": fact(len(findings), "count", "System-detected SAP risks", "sap.findings.count"),
    }

    tables = {
        "sap_l3_trend_12w": table(
            "SAP incidents, last 12 weeks",
            [
                ("period", "Week", "text"),
                ("opened", "Opened", "count"),
                ("resolved", "Resolved", "count"),
                ("net", "Net", "count"),
                ("sla_pct", "SLA %", "pct"),
            ],
            trend,
        ),
        "sap_areas": table(
            "SAP areas this week",
            [
                ("label", "Area", "text"),
                ("open", "Open", "count"),
                ("aged_30d", "Open > 30 days", "count"),
                ("opened", "Opened", "count"),
                ("resolved", "Resolved", "count"),
                ("sla_pct", "SLA %", "pct"),
            ],
            areas,
        ),
        "sap_backlog_aging_by_area": table(
            "SAP backlog aging by area (week end)",
            [
                ("label", "Area", "text"),
                ("total", "Open", "count"),
                ("d0_7", "0-7d", "count"),
                ("d8_30", "8-30d", "count"),
                ("d31_90", "31-90d", "count"),
                ("d90p", ">90d", "count"),
            ],
            backlog_now["by_area"],
        ),
        "sap_landscapes": table(
            "Open SAP incidents by landscape (week end)",
            [("label", "Landscape", "text"), ("open", "Open", "count")],
            l3.backlog_by_landscape(conn, scope, at),
        ),
        "sap_flow_8w": table(
            "Arrivals vs closures by SAP area, last 8 weeks",
            [
                ("period", "Week", "text"),
                ("label", "Area", "text"),
                ("arrived", "Arrived", "count"),
                ("closed", "Closed", "count"),
                ("net", "Net", "count"),
            ],
            flow,
        ),
        "sap_sla_by_priority": table(
            "SAP SLA by priority (resolved this week)",
            [
                ("priority", "Priority", "text"),
                ("total", "Resolved", "count"),
                ("met", "Met", "count"),
                ("pct", "SLA %", "pct"),
            ],
            [{"priority": f"P{p}", **v} for p, v in sorted(sla_now["by_priority"].items())],
        ),
        "sap_attention": table(
            "SAP tickets needing attention (open incidents)",
            [
                ("number", "Number", "text"),
                ("priority", "P", "count"),
                ("area_label", "Area", "text"),
                ("app", "Application", "text"),
                ("state", "State", "text"),
                ("age_days", "Age (days)", "number"),
                ("reasons", "Why", "text"),
                ("short_description", "Short description", "text"),
            ],
            att["items"],
        ),
        "sap_findings": table(
            "System-detected SAP risks (rule findings)",
            [
                ("severity", "Severity", "text"),
                ("kind", "Kind", "text"),
                ("title", "Finding", "text"),
                ("subject_id", "Subject", "text"),
            ],
            findings,
        ),
    }
    return SnapshotParts(facts=facts, tables=tables, sla_source=src, freshness=metrics.freshness(conn))
