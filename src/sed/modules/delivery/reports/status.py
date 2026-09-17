"""Monthly delivery status snapshot builder (delivery-status): portfolio health, milestones, RAID and Jira progress of
the delivery projects for one month.

Everything is measured at `req.as_of` (the month end, or the data date for the running month): the latest plan version
on or before that date, open RAID items, story points and the forecast finish. Milestones "completed" are those with an
actual finish inside the month window. Per-project facts (`delivery.project.<id>.*`) give the AI sections exact tokens
to cite.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sed import rule_findings
from sed.modules.delivery.queries import portfolio as P
from sed.reports.snapshot import SnapshotParts, SnapshotRequest, fact, table

DUE_DAYS = 30
RAG_ORDER = {"red": 0, "amber": 1, "green": 2}


def _day(value: str | None) -> date | None:
    return date.fromisoformat(value[:10]) if value else None


def _milestones(item: dict[str, Any]) -> list[dict[str, Any]]:
    project = item["project"]
    return [
        {**t, "project_id": project["project_id"], "project": project["name"]}
        for t in item["plan"]["tasks"]
        if t["is_milestone"]
    ]


def _project_row(item: dict[str, Any]) -> dict[str, Any]:
    project, prog, raid = item["project"], item["progress"], item["raid"]
    open_ms = [t for t in _milestones(item) if not t["actual_finish"]]
    target = _day(project["target_date"])
    return {
        "project_id": project["project_id"],
        "name": project["name"],
        "phase": project["phase"],
        "reported_rag": (project["rag_raw"] or "").lower() or None,
        "computed_rag": item["health"]["rag"],
        "reasons": "; ".join(item["health"]["reasons"]),
        "target_date": project["target_date"],
        "forecast_finish": prog.forecast_finish.isoformat() if prog.forecast_finish else None,
        "days_late": (prog.forecast_finish - target).days if prog.forecast_finish and target else None,
        "worst_slip_days": max((t["slip_days"] or 0 for t in open_ms), default=0),
        "open_high_raid": sum(1 for i in raid if i["open"] and i["severity"] in ("high", "critical")),
        "overdue_raid": sum(1 for i in raid if i["days_overdue"] > 0),
        "points_done_pct": round(100.0 * prog.points_done / prog.points_total, 1) if prog.points_total else None,
        "scope_growth_pct": prog.scope_growth_pct,
        "velocity": prog.velocity_per_week,
        "stories": prog.stories,
        "points_total": prog.points_total,
        "points_done": prog.points_done,
        "requirements": item["documents"]["requirements"],
        "adrs": item["documents"]["adrs"],
    }


def build(req: SnapshotRequest) -> SnapshotParts:
    conn, paths, period, window, as_of = req.conn, req.paths, req.period, req.window, req.as_of
    rules = P.load_rules(paths)
    items = P.portfolio(conn, paths, as_of)
    rows = sorted(
        (_project_row(i) for i in items), key=lambda r: (RAG_ORDER.get(r["computed_rag"], 3), r["project_id"])
    )
    milestones = [m for i in items for m in _milestones(i)]
    start, end = period.start_local.isoformat(), window.end_local.isoformat()
    # window.end_local is exclusive
    completed = [m for m in milestones if m["actual_finish"] and start <= m["actual_finish"][:10] < end]
    due_until = (as_of + timedelta(days=DUE_DAYS)).isoformat()
    due = [m for m in milestones if not m["actual_finish"] and m["finish"] and m["finish"][:10] <= due_until]
    slipped = [
        m for m in milestones if not m["actual_finish"] and (m["slip_days"] or 0) >= rules.milestone_slip.min_slip_days
    ]
    raid = [dict(r, project=next(i["project"]["name"] for i in items if i["project"]["project_id"] == r["project_id"]))
            for i in items for r in i["raid"]]  # fmt: skip
    raid_open = [r for r in raid if r["open"] and (r["severity"] in ("high", "critical") or r["days_overdue"] > 0)]
    findings = rule_findings.as_of_findings(conn, paths, as_of, "delivery")
    points_total = sum(i["progress"].points_total for i in items)
    points_done = sum(i["progress"].points_done for i in items)
    counts = {rag: sum(1 for r in rows if r["computed_rag"] == rag) for rag in RAG_ORDER}
    suffix = " (to date)" if window.end_local < period.end_local else ""

    facts: dict[str, dict[str, Any]] = {
        "period.label": fact(period.label, "text", "Period"),
        "period.start": fact(period.start_local.isoformat(), "date", "Period start"),
        "period.end": fact(period.last_day.isoformat(), "date", "Period end"),
        "delivery.projects.count": fact(len(rows), "count", "Delivery projects", "delivery.projects.count"),
        "delivery.projects.red": fact(counts["red"], "count", "Projects with red health", "delivery.projects.red"),
        "delivery.projects.amber": fact(
            counts["amber"], "count", "Projects with amber health", "delivery.projects.amber"
        ),
        "delivery.projects.green": fact(counts["green"], "count", "Projects with green health"),
        "delivery.projects.rag_mismatch": fact(
            sum(1 for r in rows if r["reported_rag"] and r["reported_rag"] != r["computed_rag"]),
            "count",
            "Projects whose reported RAG differs from the computed health",
            "delivery.projects.rag_mismatch",
        ),
        "delivery.milestones.completed": fact(
            len(completed), "count", f"Milestones completed{suffix}", "delivery.milestones.completed"
        ),
        "delivery.milestones.slipped": fact(
            len(slipped), "count", "Open milestones slipped past baseline", "delivery.milestones.slipped"
        ),
        "delivery.milestones.due_30d": fact(
            len(due), "count", f"Milestones due in the next {DUE_DAYS} days", "delivery.milestones.due_30d"
        ),
        "delivery.raid.open_high": fact(
            sum(1 for r in raid if r["open"] and r["severity"] in ("high", "critical")),
            "count",
            "Open high RAID items",
            "delivery.raid.open_high",
        ),
        "delivery.raid.overdue": fact(
            sum(1 for r in raid if r["days_overdue"] > 0), "count", "Overdue RAID items", "delivery.raid.overdue"
        ),
        "delivery.points.done_pct": fact(
            round(100.0 * points_done / points_total, 1) if points_total else None,
            "pct",
            "Story points done",
            "delivery.points.done_pct",
        ),
        "delivery.velocity.points_per_week": fact(
            round(sum(i["progress"].velocity_per_week for i in items), 1),
            "number",
            "Velocity (story points per week)",
            "delivery.velocity.points_per_week",
        ),
        "delivery.findings.count": fact(
            len(findings), "count", "System-detected delivery risks", "delivery.findings.count"
        ),
    }
    for r in rows:
        key = f"delivery.project.{r['project_id']}"
        name = r["name"]
        facts |= {
            f"{key}.name": fact(name, "text", f"{r['project_id']} name"),
            f"{key}.rag": fact(r["computed_rag"], "text", f"{name}: computed health"),
            f"{key}.worst_slip_days": fact(r["worst_slip_days"], "count", f"{name}: worst open milestone slip (days)"),
            f"{key}.open_high_raid": fact(r["open_high_raid"], "count", f"{name}: open high RAID items"),
            f"{key}.points_done_pct": fact(r["points_done_pct"], "pct", f"{name}: story points done"),
            f"{key}.scope_growth_pct": fact(r["scope_growth_pct"], "pct", f"{name}: scope growth in the window"),
            f"{key}.velocity": fact(r["velocity"], "number", f"{name}: story points per week"),
            f"{key}.forecast_finish": fact(r["forecast_finish"], "date", f"{name}: forecast finish"),
            f"{key}.target_date": fact(r["target_date"], "date", f"{name}: target go-live"),
            f"{key}.days_late": fact(r["days_late"], "count", f"{name}: forecast days after target"),
        }

    milestone_cols = [
        ("project", "Project", "text"),
        ("name", "Milestone", "text"),
        ("baseline_finish", "Baseline", "date"),
        ("finish", "Forecast", "date"),
        ("actual_finish", "Actual", "date"),
        ("slip_days", "Slip (days)", "count"),
        ("replans", "Replans", "count"),
    ]
    tables = {
        "delivery_projects": table(
            "Delivery projects",
            [
                ("project_id", "Project", "text"),
                ("name", "Name", "text"),
                ("phase", "Phase", "text"),
                ("computed_rag", "Health", "text"),
                ("reported_rag", "Reported RAG", "text"),
                ("reasons", "Why", "text"),
                ("target_date", "Target", "date"),
                ("forecast_finish", "Forecast", "date"),
                ("worst_slip_days", "Worst slip (days)", "count"),
                ("open_high_raid", "Open high RAID", "count"),
                ("points_done_pct", "Points done %", "pct"),
            ],
            rows,
        ),
        "delivery_progress": table(
            "Jira progress by project",
            [
                ("name", "Project", "text"),
                ("stories", "Stories", "count"),
                ("points_total", "Scope (points)", "number"),
                ("points_done", "Done (points)", "number"),
                ("points_done_pct", "Done %", "pct"),
                ("scope_growth_pct", "Scope growth %", "pct"),
                ("velocity", "Points per week", "number"),
                ("forecast_finish", "Forecast", "date"),
                ("target_date", "Target", "date"),
                ("days_late", "Days late", "count"),
            ],
            rows,
        ),
        "delivery_milestones_slipped": table(
            "Open milestones slipped past baseline",
            milestone_cols,
            sorted(slipped, key=lambda m: -(m["slip_days"] or 0)),
        ),
        "delivery_milestones_completed": table(
            f"Milestones completed{suffix}", milestone_cols, sorted(completed, key=lambda m: m["actual_finish"])
        ),
        "delivery_milestones_due": table(
            f"Milestones due in the next {DUE_DAYS} days", milestone_cols, sorted(due, key=lambda m: m["finish"])
        ),
        "delivery_raid": table(
            "Open high or overdue RAID items",
            [
                ("project", "Project", "text"),
                ("raid_id", "ID", "text"),
                ("raid_type", "Type", "text"),
                ("title", "Title", "text"),
                ("severity", "Severity", "text"),
                ("due_date", "Due", "date"),
                ("days_overdue", "Days overdue", "count"),
            ],
            sorted(raid_open, key=lambda r: (-r["days_overdue"], r["raid_id"])),
        ),
        "delivery_findings": table(
            "System-detected delivery risks (rule findings)",
            [
                ("severity", "Severity", "text"),
                ("kind", "Kind", "text"),
                ("title", "Finding", "text"),
                ("subject_id", "Subject", "text"),
            ],
            findings,
        ),
    }
    return SnapshotParts(facts=facts, tables=tables)
