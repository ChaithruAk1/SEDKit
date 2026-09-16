"""Weekly SAP Operations Review snapshot builder (sap-weekly): SAP L3 support, ChaRM changes and IDoc health for one
ISO week.

Aggregates of the week use `req.window` (the week clamped to the data date, labelled "to date" when open); backlog,
attention, change status and IDoc errors are measured at the end of that window; rule findings use `req.as_of`.
"""

from __future__ import annotations

from sed import metrics, rule_findings
from sed.modules.sap.charm import load_charm
from sed.modules.sap.idoc import load_idoc
from sed.modules.sap.queries import changes, idocs, l3
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

    cs = changes.load(conn, load_charm(paths, scope), at)
    change_weeks = [period.previous(k) for k in range(11, 0, -1)] + [window]
    urgent_window = change_weeks[-8:]
    urgent_previous = [urgent_window[0].previous(k) for k in range(8, 0, -1)]
    since = change_weeks[0].start_iso
    all_changes = changes.select(cs)
    cs_summary = changes.summary(conn, cs, window)
    ratio, ratio_before = cs_summary["urgent_ratio_8w"], cs_summary["urgent_ratio_previous_8w"]

    ids = idocs.load(conn, load_idoc(paths, scope), at)
    ids_summary = idocs.summary(ids, window)

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
        "sap.changes.open": fact(cs_summary["open"], "count", "Open SAP changes", "sap.changes.open"),
        "sap.changes.urgent_ratio_8w": fact(
            ratio, "pct", "Urgent share of new changes, 8 weeks", "sap.changes.urgent_ratio_8w"
        ),
        "sap.changes.urgent_ratio_previous_8w": fact(
            ratio_before, "pct", "Urgent share of new changes, the 8 weeks before", "sap.changes.urgent_ratio_8w"
        ),
        "sap.changes.urgent_ratio_delta_pp": fact(
            round(ratio - ratio_before, 1) if ratio is not None and ratio_before is not None else None,
            "pp",
            "Urgent share vs the 8 weeks before",
        ),
        "sap.changes.stuck": fact(cs_summary["stuck"], "count", "Stuck SAP changes", "sap.changes.stuck"),
        "sap.changes.without_jira": fact(
            cs_summary["without_jira"], "count", "Open changes without a Jira story", "sap.changes.without_jira"
        ),
        "sap.changes.prod_imports": fact(
            cs_summary["prod_imports_week"], "count", f"Production imports{suffix}", "sap.changes.prod_imports"
        ),
        "sap.transports.failed_4w": fact(
            cs_summary["failed_4w"], "count", "Failed transport imports (28 days)", "sap.transports.failed_4w"
        ),
        "sap.transports.waiting": fact(
            cs_summary["waiting"], "count", "Transports waiting for production", "sap.transports.waiting"
        ),
        "sap.idocs.errors_open": fact(ids_summary["errors_open"], "count", "IDocs in error", "sap.idocs.errors_open"),
        "sap.idocs.errors_aged": fact(
            ids_summary["errors_aged"], "count", "IDoc errors open for more than 48 hours", "sap.idocs.errors_aged"
        ),
        "sap.idocs.new_persistent": fact(
            ids_summary["new_persistent_week"],
            "count",
            f"New persistent IDoc errors{suffix}",
            "sap.idocs.new_persistent",
        ),
        "sap.idocs.new_persistent.avg4w": fact(
            ids_summary["new_persistent_avg4w"],
            "number",
            "New persistent IDoc errors, 4-week average",
            "sap.idocs.new_persistent",
        ),
        "sap.idocs.reprocess_median_h": fact(
            ids_summary["reprocess_median_h"],
            "hours",
            f"IDoc reprocessing median{suffix}",
            "sap.idocs.reprocess_median_h",
        ),
        "sap.idocs.reprocessed_in_grace_pct": fact(
            ids_summary["reprocessed_within_grace_pct"],
            "pct",
            "IDocs reprocessed within the grace time",
            "sap.idocs.reprocessed_in_grace_pct",
        ),
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
        "sap_change_stages": table(
            "Open SAP changes by stage and type",
            [
                ("label", "Stage", "text"),
                ("normal", "Normal", "count"),
                ("urgent", "Urgent", "count"),
                ("standard", "Standard", "count"),
                ("defect_correction", "Defect correction", "count"),
                ("total", "Open", "count"),
            ],
            changes.stage_matrix(all_changes),
        ),
        "sap_urgent_by_area": table(
            "Urgent share of new changes by area (8 weeks vs the 8 before)",
            [
                ("label", "Area", "text"),
                ("created", "Created", "count"),
                ("urgent", "Urgent", "count"),
                ("ratio_pct", "Urgent %", "pct"),
                ("previous_ratio_pct", "8 weeks before %", "pct"),
                ("delta_pp", "Change (pp)", "pp"),
            ],
            changes.urgent_by_area(cs, urgent_window, urgent_previous),
        ),
        "sap_prod_imports_12w": table(
            "Production imports, last 12 weeks",
            [
                ("period", "Week", "text"),
                ("imports", "Imports", "count"),
                ("failed", "Failed", "count"),
                ("changes", "Changes", "count"),
            ],
            changes.production_imports(cs, change_weeks),
        ),
        "sap_transports_failed": table(
            "Failed transport imports, last 12 weeks",
            [
                ("transport", "Transport", "text"),
                ("system_id", "System", "text"),
                ("return_code", "RC", "count"),
                ("imported_at", "Imported", "datetime"),
                ("change_id", "Change", "text"),
                ("title", "Title", "text"),
            ],
            changes.failed_imports(cs, since),
        ),
        "sap_incidents_after_imports": table(
            "SAP incidents after production imports, last 12 weeks",
            [
                ("change_id", "Change", "text"),
                ("title", "Title", "text"),
                ("system_id", "System", "text"),
                ("imported_at", "Imported", "datetime"),
                ("return_code", "RC", "count"),
                ("incidents", "Incidents after", "count"),
                ("incidents_before", "Before", "count"),
                ("lift", "Lift", "count"),
            ],
            changes.incidents_after_imports(conn, cs, since, window.end_iso),
        ),
        "sap_changes_stuck": table(
            "Stuck SAP changes",
            [
                ("change_id", "Change", "text"),
                ("title", "Title", "text"),
                ("area_label", "Area", "text"),
                ("status", "Status", "text"),
                ("days_in_status", "Days in status", "number"),
                ("threshold_days", "Limit (days)", "count"),
            ],
            changes.stuck(cs, all_changes),
        ),
        "sap_transports_waiting": table(
            "Transports waiting for production",
            [
                ("transport", "Transport", "text"),
                ("change_id", "Change", "text"),
                ("landscape", "Landscape", "text"),
                ("qa_system", "QA system", "text"),
                ("qa_imported_at", "QA import", "datetime"),
                ("days_waiting", "Days waiting", "number"),
            ],
            changes.waiting_for_production(cs),
        ),
        "sap_changes_without_jira": table(
            "Open changes without a Jira story",
            [
                ("change_id", "Change", "text"),
                ("title", "Title", "text"),
                ("type_label", "Type", "text"),
                ("area_label", "Area", "text"),
                ("stage_label", "Stage", "text"),
            ],
            changes.without_jira(cs, all_changes),
        ),
        "sap_idoc_by_type": table(
            "IDocs in error by system and message type",
            [
                ("system_id", "System", "text"),
                ("message_type", "Message type", "text"),
                ("direction", "Direction", "text"),
                ("errors", "In error", "count"),
                ("aged", "Aged", "count"),
                ("partners", "Partners", "count"),
                ("oldest_hours", "Oldest (hours)", "hours"),
            ],
            idocs.backlog_by_type(ids, ids.errors),
        ),
        "sap_idoc_partners": table(
            "IDoc partners with most errors",
            [
                ("partner", "Partner", "text"),
                ("system_id", "System", "text"),
                ("message_type", "Message type", "text"),
                ("errors", "In error", "count"),
                ("oldest_hours", "Oldest (hours)", "hours"),
            ],
            idocs.partners(ids, ids.errors),
        ),
        "sap_idoc_weekly_12w": table(
            "IDoc errors, last 12 weeks",
            [
                ("period", "Week", "text"),
                ("idocs", "IDocs", "count"),
                ("new_errors", "New errors", "count"),
                ("persistent", "Persistent", "count"),
                ("reprocessed", "Reprocessed", "count"),
                ("reprocess_median_h", "Reprocessing median (h)", "hours"),
            ],
            idocs.weekly(conn, ids, ids.errors, change_weeks),
        ),
        "sap_idoc_texts": table(
            "Most frequent IDoc error texts",
            [("text", "Error text", "text"), ("errors", "In error", "count")],
            idocs.top_texts(ids.errors),
        ),
        "sap_idoc_spikes": table(
            "IDoc error spikes after production imports, last 12 weeks",
            [
                ("change_id", "Change", "text"),
                ("system_id", "System", "text"),
                ("imported_at", "Imported", "datetime"),
                ("errors", "Errors after", "count"),
                ("errors_before", "Before", "count"),
                ("lift", "Lift", "count"),
            ],
            idocs.spikes_after_imports(ids, cs, since, window.end_iso),
        ),
    }
    return SnapshotParts(facts=facts, tables=tables, sla_source=src, freshness=metrics.freshness(conn))
