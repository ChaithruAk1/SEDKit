"""Monthly Service Review snapshot builder (business stakeholders, plan A10).

Exec summary KPIs, a 6-month SLA/MTTR/volume trend per application family, top recurring issues with problem status,
improvements delivered (Jira resolved), upcoming changes and system-detected risks.

Aggregates use `req.window` (the month clamped to the data date, so an open month is reported to date); everything
as-of dependent (backlog, upcoming changes, rule findings) uses `req.as_of`.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sed import analytics, metrics
from sed.calendar import Period, local_midnight_utc
from sed.modules.ops.reports import queries
from sed.reports.snapshot import SnapshotParts, SnapshotRequest, fact, table

TREND_MONTHS = 6
UPCOMING_CHANGE_DAYS = 30
TOP_RECURRING = 10


def _delta_pp(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None:
        return None
    return round(current - previous, 2)


def _to_date(req: SnapshotRequest) -> str:
    return " (to date)" if req.window.end_local < req.period.end_local else ""


def _trend(conn: Any, f: metrics.Filters, periods: list[Period], src: str) -> list[dict[str, Any]]:
    """Opened/resolved, SLA % and MTTR median per period (labels from the periods, the last one may be a window)."""
    volumes = metrics.volume_trend(conn, f, periods)
    rows = []
    for p, vol in zip(periods, volumes, strict=True):
        rows.append(
            {
                "period": p.label,
                "opened": vol["opened"],
                "resolved": vol["resolved"],
                "sla_pct": metrics.sla(conn, f, p, src)["pct"],
                "mttr_median_h": metrics.mttr(conn, f, p)["median_h"],
            }
        )
    return rows


def build(req: SnapshotRequest) -> SnapshotParts:
    conn, paths, settings, period, window = req.conn, req.paths, req.settings, req.period, req.window
    fys = settings.fiscal_year_start
    f = metrics.Filters()
    src = metrics.sla_source(conn)
    suffix = _to_date(req)

    history = [queries.shift_period(period, -k, fys) for k in range(TREND_MONTHS - 1, 0, -1)]
    trend_periods = [*history, window]

    trend = _trend(conn, f, trend_periods, src)
    for row, p in zip(trend, trend_periods, strict=True):
        row["backlog"] = metrics.backlog(conn, f, p.end_utc)["total"]
    current, previous = trend[-1], trend[-2]
    p1p2_opened = metrics.volume_trend(conn, metrics.Filters(priorities=[1, 2]), [window])[0]["opened"]
    chg = metrics.changes(conn, window)
    work_resolved = queries.work_resolved_count(conn, window)

    families = queries.app_families(conn)
    by_family: list[dict[str, Any]] = []
    sla_wide: list[dict[str, Any]] = []
    for family in families:
        rows = _trend(conn, metrics.Filters(family=family), trend_periods, src)
        by_family += [{"family": family, **r} for r in rows]
        sla_wide.append({"family": family, **{f"m{i}": r["sla_pct"] for i, r in enumerate(rows, start=1)}})

    change_start = local_midnight_utc(req.as_of, settings.reporting_tz)
    change_end = local_midnight_utc(req.as_of + timedelta(days=UPCOMING_CHANGE_DAYS), settings.reporting_tz)
    findings = analytics.findings_as_of(conn, paths, req.as_of)

    facts = {
        "period.label": fact(period.label, "text", "Period"),
        "period.start": fact(period.start_local.isoformat(), "date", "Period start"),
        "period.end": fact(period.last_day.isoformat(), "date", "Period end"),
        "inc.opened": fact(current["opened"], "count", f"Incidents opened{suffix}", "inc.opened"),
        "inc.opened.prev_month": fact(previous["opened"], "count", "Incidents opened, previous month", "inc.opened"),
        "inc.resolved": fact(current["resolved"], "count", f"Incidents resolved{suffix}", "inc.resolved"),
        "inc.resolved.prev_month": fact(
            previous["resolved"], "count", "Incidents resolved, previous month", "inc.resolved"
        ),
        "inc.sla.pct": fact(current["sla_pct"], "pct", f"SLA met (resolved in month){suffix}", "inc.sla.pct"),
        "inc.sla.pct.prev_month": fact(previous["sla_pct"], "pct", "SLA met, previous month", "inc.sla.pct"),
        "inc.sla.delta_pp_vs_prev_month": fact(
            _delta_pp(current["sla_pct"], previous["sla_pct"]),
            "pp",
            "SLA vs previous month",
            "inc.sla.delta_pp_vs_prev_month",
        ),
        "inc.mttr.median_h": fact(current["mttr_median_h"], "hours", f"MTTR median{suffix}", "inc.mttr.median_h"),
        "inc.mttr.median_h.prev_month": fact(
            previous["mttr_median_h"], "hours", "MTTR median, previous month", "inc.mttr.median_h"
        ),
        "inc.backlog": fact(
            current["backlog"],
            "count",
            "Open backlog at the as-of date" if suffix else "Open backlog at month end",
            "inc.backlog",
        ),
        "inc.p1p2.opened": fact(p1p2_opened, "count", f"P1/P2 incidents opened{suffix}", "inc.p1p2.opened"),
        "chg.count": fact(chg["count"], "count", f"Changes closed{suffix}", "chg.count"),
        "chg.success.pct": fact(chg["success_pct"], "pct", "Change success rate", "chg.success.pct"),
        "work.resolved.count": fact(
            work_resolved, "count", f"Improvements delivered (Jira resolved){suffix}", "work.resolved.count"
        ),
    }

    month_labels = [p.label for p in trend_periods]
    tables = {
        "trend_6m": table(
            "Incidents, SLA and MTTR, last 6 months",
            [
                ("period", "Month", "text"),
                ("opened", "Opened", "count"),
                ("resolved", "Resolved", "count"),
                ("sla_pct", "SLA %", "pct"),
                ("mttr_median_h", "MTTR median", "hours"),
                ("backlog", "Backlog at month end", "count"),
            ],
            trend,
        ),
        "trend_6m_by_family": table(
            "SLA, MTTR and volume per application family, last 6 months",
            [
                ("family", "Family", "text"),
                ("period", "Month", "text"),
                ("opened", "Opened", "count"),
                ("resolved", "Resolved", "count"),
                ("sla_pct", "SLA %", "pct"),
                ("mttr_median_h", "MTTR median", "hours"),
            ],
            by_family,
        ),
        "sla_6m_by_family": table(
            "SLA % per application family, last 6 months",
            [("family", "Family", "text")] + [(f"m{i}", label, "pct") for i, label in enumerate(month_labels, start=1)],
            sla_wide,
        ),
        "top_recurring": table(
            "Top recurring issues (application x category, seen in 2+ of the last 6 months)",
            [
                ("app", "Application", "text"),
                ("family", "Family", "text"),
                ("category", "Category", "text"),
                ("subcategory", "Subcategory", "text"),
                ("incidents", "Incidents this month", "count"),
                ("prev_3m", "Previous 3 months", "count"),
                ("months_seen", "Months seen (of 6)", "count"),
                ("p1p2", "P1/P2", "count"),
                ("problems", "Linked problems", "text"),
            ],
            queries.top_recurring(conn, window, history, TOP_RECURRING),
        ),
        "problems": table(
            "Problem status (open during the month)",
            [
                ("number", "Problem", "text"),
                ("app", "Application", "text"),
                ("priority", "P", "count"),
                ("status", "Status at month end", "text"),
                ("state", "Latest state", "text"),
                ("opened_at", "Opened (UTC)", "datetime"),
                ("resolved_at", "Resolved (UTC)", "datetime"),
                ("age_days", "Age (days)", "number"),
                ("incidents_linked", "Linked incidents", "count"),
                ("incidents_in_period", "Linked this month", "count"),
                ("short_description", "Short description", "text"),
            ],
            queries.problems(conn, window),
        ),
        "improvements": table(
            "Improvements delivered (Jira work items resolved)",
            [
                ("app", "Application", "text"),
                ("project", "Project", "text"),
                ("resolved", "Resolved", "count"),
                ("stories", "Stories", "count"),
                ("bugs", "Bugs", "count"),
                ("points", "Story points", "number"),
                ("open_at_end", "Open at month end", "count"),
            ],
            queries.improvements(conn, window),
        ),
        "upcoming_changes": table(
            f"Upcoming changes (planned start in the {UPCOMING_CHANGE_DAYS} days from {req.as_of.isoformat()})",
            [
                ("number", "Change", "text"),
                ("app", "Application", "text"),
                ("change_type", "Type", "text"),
                ("risk", "Risk", "text"),
                ("state", "State", "text"),
                ("start_date", "Planned start (UTC)", "datetime"),
                ("end_date", "Planned end (UTC)", "datetime"),
                ("short_description", "Short description", "text"),
            ],
            queries.upcoming_changes(conn, change_start, change_end),
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
    return SnapshotParts(facts=facts, tables=tables, sla_source=src, freshness=metrics.freshness(conn))
