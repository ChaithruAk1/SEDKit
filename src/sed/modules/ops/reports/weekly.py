"""Weekly Application Operations Review snapshot builder (moved from sed.reports.snapshot in M2 Phase 0).

Aggregates of the current week use `req.window` (the week clamped to the data date, so an open week is reported to
date and labelled so); backlog and attention are measured at the end of that window; as-of-dependent calls
(renewals, licenses, rule findings) use `req.as_of`.
"""

from __future__ import annotations

from datetime import date, timedelta

from sed import analytics, metrics
from sed.modules.ops.reports import queries
from sed.modules.ops.reports.ai_provenance import approved_findings, weekly_ai
from sed.reports.snapshot import SnapshotParts, SnapshotRequest, fact, table


def _delta_pct(current: float | None, baseline: float | None) -> float | None:
    if current is None or not baseline:
        return None
    return round(100.0 * (current - baseline) / baseline, 1)


def _avg(values: list[float | None]) -> float | None:
    vals = [v for v in values if v is not None]
    return round(sum(vals) / len(vals), 2) if vals else None


def _months_before(as_of: date, n: int) -> list[str]:
    out = []
    d = date(as_of.year, as_of.month, 1)
    for _ in range(n):
        d = date(d.year, d.month, 1) - timedelta(days=1)
        out.append(f"{d.year}-{d.month:02d}")
        d = date(d.year, d.month, 1)
    return list(reversed(out))


def build(req: SnapshotRequest) -> SnapshotParts:
    conn, paths, settings, period, window = req.conn, req.paths, req.settings, req.period, req.window
    f = metrics.Filters()
    as_of_date = req.as_of
    partial = window.end_local < period.end_local
    suffix = " (to date)" if partial else ""
    previous = [period.previous(k) for k in range(1, 5)]
    trend_periods = [period.previous(k) for k in range(11, 0, -1)] + [window]
    src = metrics.sla_source(conn)

    vol = metrics.volume_trend(conn, f, trend_periods)
    current = vol[-1]
    prev_vol = vol[-5:-1]
    sla_now = metrics.sla(conn, f, window, src)
    sla_prev = [metrics.sla(conn, f, p, src)["pct"] for p in previous]
    mttr_now = metrics.mttr(conn, f, window)
    mttr_prev = [metrics.mttr(conn, f, p)["median_h"] for p in previous]
    backlog_now = metrics.backlog(conn, f, window.end_utc)
    backlog_now_all = metrics.backlog(conn, f, window.end_utc, exclude_stale=False)["total"]
    backlog_prev_all = metrics.backlog(conn, f, period.start_utc, exclude_stale=False)["total"]
    quality = metrics.quality(conn, f, window)
    att = metrics.attention(conn, f, window.end_utc, settings.thresholds)
    chg = metrics.changes(conn, window)
    p1p2 = metrics.p1p2_opened(conn, f, window)
    p1p2_count = metrics.volume_trend(conn, metrics.Filters(priorities=[1, 2]), [window])[0]["opened"]
    renewals_90 = metrics.renewals(conn, as_of_date, 90)
    licenses = metrics.license_utilization(conn, as_of_date)
    low = queries.license_low_threshold(paths)
    outliers = [
        x
        for x in licenses
        if x["utilization"] is not None
        and (x["utilization"] < low or x["utilization"] > 1.0 or (x["assigned_ratio"] or 0) > 1.0)
    ]
    cost_months = _months_before(as_of_date, 2)
    cost_rows = [
        r
        for r in metrics.cost_vs_budget(conn, cost_months, "app_category")
        if r["variance_pct"] is not None and abs(r["variance_pct"]) >= 10
    ]
    findings = analytics.findings_as_of(conn, paths, as_of_date)
    opened_avg = _avg([v["opened"] for v in prev_vol])
    resolved_avg = _avg([v["resolved"] for v in prev_vol])
    sla_avg = _avg(sla_prev)
    mttr_avg = _avg(mttr_prev)

    facts = {
        "period.label": fact(period.label, "text", "Period"),
        "period.start": fact(period.start_local.isoformat(), "date", "Period start"),
        "period.end": fact(period.last_day.isoformat(), "date", "Period end"),
        "inc.opened": fact(current["opened"], "count", f"Incidents opened{suffix}", "inc.opened"),
        "inc.opened.avg4w": fact(opened_avg, "number", "Opened, 4-week average", "inc.opened"),
        # A partial week's count is not comparable with full-week averages, so no delta is reported for it.
        "inc.opened.delta_vs_avg4w_pct": fact(
            None if partial else _delta_pct(current["opened"], opened_avg), "pct", "Opened vs 4-week avg"
        ),
        "inc.resolved": fact(current["resolved"], "count", f"Incidents resolved{suffix}", "inc.resolved"),
        "inc.resolved.avg4w": fact(resolved_avg, "number", "Resolved, 4-week average", "inc.resolved"),
        "inc.backlog": fact(
            backlog_now["total"],
            "count",
            "Open backlog at the as-of date" if partial else "Open backlog at week end",
            "inc.backlog",
        ),
        "inc.backlog.delta": fact(
            backlog_now_all - backlog_prev_all, "count", "Backlog change (arrivals - resolutions)"
        ),
        "inc.backlog.stale_excluded": fact(
            backlog_now_all - backlog_now["total"], "count", "Stale open excluded from backlog", "inc.stale_open"
        ),
        "inc.sla.pct": fact(sla_now["pct"], "pct", f"SLA met (resolved this week){suffix}", "inc.sla.pct"),
        "inc.sla.pct.avg4w": fact(sla_avg, "pct", "SLA met, 4-week average", "inc.sla.pct"),
        "inc.sla.delta_pp_vs_4w": fact(
            round(sla_now["pct"] - sla_avg, 2) if sla_now["pct"] is not None and sla_avg is not None else None,
            "pp",
            "SLA vs 4-week avg",
        ),
        "inc.sla.source": fact(src, "text", "SLA source"),
        "inc.mttr.median_h": fact(mttr_now["median_h"], "hours", f"MTTR median (hours){suffix}", "inc.mttr.median_h"),
        "inc.mttr.median_h.avg4w": fact(mttr_avg, "hours", "MTTR median, 4-week average", "inc.mttr.median_h"),
        "inc.mttr.delta_vs_avg4w_pct": fact(_delta_pct(mttr_now["median_h"], mttr_avg), "pct", "MTTR vs 4-week avg"),
        "inc.p1p2.opened": fact(p1p2_count, "count", f"P1/P2 opened{suffix}", "inc.p1p2.opened"),
        "inc.reopen.pct": fact(quality["reopen_pct"], "pct", "Reopen rate", "inc.reopen.pct"),
        "inc.reassign.avg": fact(quality["reassign_avg"], "number", "Avg reassignments", "inc.reassign.avg"),
        "inc.stale_open": fact(
            metrics.stale_open_count(conn, f), "count", "Stale open (not in active export)", "inc.stale_open"
        ),
        "attention.count": fact(att["count"], "count", "Tickets needing attention", "attention.count"),
        "chg.count": fact(chg["count"], "count", f"Changes closed{suffix}", "chg.count"),
        "chg.success.pct": fact(chg["success_pct"], "pct", "Change success rate", "chg.success.pct"),
        "chg.failed": fact(len(chg["failed"]), "count", "Changes not fully successful"),
        "renewals.90d.count": fact(
            sum(1 for r in renewals_90 if r["days_to_end"] is not None and 0 <= r["days_to_end"] <= 90),
            "count",
            "Contracts ending within 90 days",
            "renewals.count",
        ),
        "notice.30d.count": fact(
            sum(1 for r in renewals_90 if r["days_to_notice"] is not None and 0 <= r["days_to_notice"] <= 30),
            "count",
            "Notice deadlines within 30 days",
            "notice.count",
        ),
        "license.idle_cost": fact(
            round(sum(x["idle_cost_base"] or 0 for x in outliers if x["utilization"] < low), 2),
            "eur",
            "Idle license cost (under-used lines)",
            "license.idle_cost",
        ),
        "findings.system_detected.count": fact(len(findings), "count", "System-detected risks"),
    }

    tables = {
        "volume_trend_12w": table(
            "Incident volume, last 12 weeks",
            [
                ("period", "Week", "text"),
                ("opened", "Opened", "count"),
                ("resolved", "Resolved", "count"),
                ("net", "Net", "count"),
            ],
            vol,
        ),
        "sla_by_priority": table(
            "SLA by priority (resolved this week)",
            [
                ("priority", "Priority", "text"),
                ("total", "Resolved", "count"),
                ("met", "Met", "count"),
                ("pct", "SLA %", "pct"),
            ],
            [{"priority": f"P{p}", **v} for p, v in sorted(sla_now["by_priority"].items())],
        ),
        "backlog_aging_by_group": table(
            "Backlog aging by assignment group (week end)",
            [
                ("group", "Group", "text"),
                ("total", "Open", "count"),
                ("0-7d", "0-7d", "count"),
                ("8-30d", "8-30d", "count"),
                ("31-90d", "31-90d", "count"),
                (">90d", ">90d", "count"),
            ],
            [{"group": g, **v} for g, v in list(backlog_now["by_group"].items())[:25]],
        ),
        "attention": table(
            "Needs attention (open incidents)",
            [
                ("number", "Number", "text"),
                ("priority", "P", "count"),
                ("app", "Application", "text"),
                ("state", "State", "text"),
                ("assignment_group", "Group", "text"),
                ("age_days", "Age (days)", "number"),
                ("reasons", "Why", "text"),
                ("short_description", "Short description", "text"),
            ],
            att["items"][:100],
        ),
        "top_apps": table(
            "Top applications by incidents opened",
            [
                ("app", "Application", "text"),
                ("family", "Family", "text"),
                ("opened", "Opened", "count"),
                ("p1p2", "P1/P2", "count"),
            ],
            metrics.top_apps(conn, f, window),
        ),
        "p1p2": table(
            "P1/P2 incidents opened this week",
            [
                ("number", "Number", "text"),
                ("priority", "P", "count"),
                ("app", "Application", "text"),
                ("state", "State", "text"),
                ("assignment_group", "Group", "text"),
                ("opened_at", "Opened (UTC)", "datetime"),
                ("short_description", "Short description", "text"),
            ],
            p1p2,
        ),
        "changes_failed": table(
            "Changes closed with issues or unsuccessful",
            [
                ("number", "Number", "text"),
                ("app", "Application", "text"),
                ("change_type", "Type", "text"),
                ("close_code", "Close code", "text"),
                ("closed_at", "Closed (UTC)", "datetime"),
                ("short_description", "Short description", "text"),
            ],
            chg["failed"],
        ),
        "category_breakdown": table(
            "ServiceNow category vs AI app-owner category (incidents opened)",
            [
                ("sn_category", "ServiceNow category", "text"),
                ("am_category", "AI category", "text"),
                ("n", "Incidents", "count"),
            ],
            metrics.category_breakdown(conn, f, window),
        ),
        "renewals_90d": table(
            "Commercial watchlist: renewals and notice deadlines (next 90 days)",
            [
                ("contract_number", "Contract", "text"),
                ("vendor", "Vendor", "text"),
                ("app", "Application", "text"),
                ("product", "Product", "text"),
                ("end_date", "End date", "date"),
                ("days_to_end", "Days to end", "count"),
                ("notice_deadline", "Notice deadline", "date"),
                ("days_to_notice", "Days to notice", "count"),
                ("auto_renew", "Auto-renew", "count"),
                ("annual_value_base", "Annual value", "eur"),
            ],
            renewals_90,
        ),
        "license_outliers": table(
            "Commercial watchlist: license utilization outliers",
            [
                ("license_id", "License", "text"),
                ("app", "Application", "text"),
                ("product", "Product", "text"),
                ("entitled_qty", "Entitled", "number"),
                ("active_qty_90d", "Active 90d", "number"),
                ("utilization", "Utilization", "ratio"),
                ("assigned_ratio", "Assigned", "ratio"),
                ("idle_cost_base", "Idle cost / yr", "eur"),
            ],
            outliers,
        ),
        "cost_variance": table(
            f"Commercial watchlist: cost vs budget ({' & '.join(cost_months)}, |variance| >= 10%)",
            [
                ("key", "Application / category", "text"),
                ("actual", "Actual", "eur"),
                ("budget", "Budget", "eur"),
                ("variance_pct", "Variance %", "pct"),
            ],
            cost_rows,
        ),
        "findings": table(
            "System-detected risks (rule findings)",
            [
                ("severity", "Severity", "text"),
                ("kind", "Kind", "text"),
                ("title", "Finding", "text"),
                ("subject_id", "Subject", "text"),
            ],
            findings,
        ),
    }
    ai = weekly_ai(req)
    findings_table, ai_findings = approved_findings(req)
    facts.update(ai.facts)
    facts.update(ai_findings.facts)
    tables["ai_findings"] = findings_table
    return SnapshotParts(
        facts=facts,
        tables=tables,
        sla_source=src,
        freshness=metrics.freshness(conn),
        ai_runs=[*ai.ai_runs, *ai_findings.ai_runs],
        ai_derived_tables=[*ai.ai_derived_tables, *ai_findings.ai_derived_tables],
        ai_derived_facts=[*ai.ai_derived_facts, *ai_findings.ai_derived_facts],
    )
