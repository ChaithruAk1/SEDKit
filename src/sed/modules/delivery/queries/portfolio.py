"""Delivery read models: projects with their latest plan, slips, RAID, Jira progress, documents and computed health.

Everything is computed on read from the register, plan versions, RAID log, Jira `work_item` rows of the project's keys
and Confluence `doc_page` rows of its space, as of a date (plan versions and Jira data after it are ignored).
"""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import pairwise
from typing import Any

from pydantic import Field

from sed.settings import StrictModel, load_layered

CLOSED = {"closed", "done", "resolved", "complete", "completed", "cancelled", "canceled"}
SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


class MilestoneSlip(StrictModel):
    enabled: bool = True
    min_slip_days: int = Field(14, ge=1)
    high_slip_days: int = Field(30, ge=1)
    min_replans: int = Field(2, ge=1)


class RaidOverdue(StrictModel):
    enabled: bool = True
    severities: list[str] = Field(default_factory=lambda: ["high", "critical"])
    grace_days: int = Field(0, ge=0)


class ScopeGrowth(StrictModel):
    enabled: bool = True
    window_weeks: int = Field(4, ge=1, le=26)
    min_growth_pct: float = Field(20, gt=0)
    min_points: float = Field(10, ge=0)


class ForecastLate(StrictModel):
    enabled: bool = True
    velocity_weeks: int = Field(4, ge=1, le=26)
    min_days_late: int = Field(7, ge=0)


class Rules(StrictModel):
    milestone_slip: MilestoneSlip = MilestoneSlip()
    raid_overdue: RaidOverdue = RaidOverdue()
    scope_growth: ScopeGrowth = ScopeGrowth()
    forecast_late: ForecastLate = ForecastLate()


def load_rules(paths: Any) -> Rules:
    return Rules.model_validate(load_layered("delivery/risk_rules.yaml", paths) or {})


def _day(value: str | None) -> date | None:
    return date.fromisoformat(value[:10]) if value else None


def _end(as_of: date) -> str:
    return f"{as_of.isoformat()}T23:59:59Z"


@dataclass
class Progress:
    stories: int
    points_total: float
    points_done: float
    points_added_window: float
    scope_growth_pct: float | None
    velocity_per_week: float
    forecast_finish: date | None
    weekly: list[dict[str, Any]]


def projects(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM delivery_project WHERE is_deleted = 0 ORDER BY project_id").fetchall()
    out = []
    for r in rows:
        item = dict(r)
        item["jira_keys"] = json.loads(r["jira_keys_json"] or "[]")
        out.append(item)
    return out


def milestones(conn: sqlite3.Connection, project_id: str, as_of: date) -> dict[str, Any]:
    """Latest plan version on or before as_of: its tasks with slip (days past baseline) and replans (plan versions in
    which the forecast finish moved later)."""
    versions = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT status_date FROM delivery_milestone WHERE project_id = ? AND status_date <= ? "
            "ORDER BY status_date",
            (project_id, as_of.isoformat()),
        )
    ]
    if not versions:
        return {"status_date": None, "versions": 0, "tasks": []}
    history: dict[str, list[str | None]] = {}
    for task_id, finish in conn.execute(
        "SELECT task_id, finish_date FROM delivery_milestone WHERE project_id = ? AND status_date <= ? "
        "ORDER BY status_date",
        (project_id, as_of.isoformat()),
    ):
        history.setdefault(task_id, []).append(finish)
    tasks = []
    for r in conn.execute(
        "SELECT * FROM delivery_milestone WHERE project_id = ? AND status_date = ? ORDER BY finish_date, task_id",
        (project_id, versions[-1]),
    ):
        finish, baseline, actual = _day(r["finish_date"]), _day(r["baseline_finish"]), _day(r["actual_finish"])
        series = [f for f in history.get(r["task_id"], []) if f]
        replans = sum(1 for a, b in pairwise(series) if b > a)
        tasks.append(
            {
                "task_id": r["task_id"],
                "name": r["name"],
                "is_milestone": bool(r["is_milestone"]),
                "baseline_finish": r["baseline_finish"],
                "finish": r["finish_date"],
                "actual_finish": r["actual_finish"],
                "percent_complete": r["percent_complete"],
                "slip_days": (finish - baseline).days if finish and baseline else None,
                "replans": replans,
                "overdue": bool(finish and not actual and finish < as_of),
            }
        )
    return {"status_date": versions[-1], "versions": len(versions), "tasks": tasks}


def raid_items(conn: sqlite3.Connection, project_id: str | None, as_of: date) -> list[dict[str, Any]]:
    sql = "SELECT * FROM delivery_raid WHERE is_deleted = 0"
    params: list[Any] = []
    if project_id:
        sql += " AND project_id = ?"
        params.append(project_id)
    out = []
    for r in conn.execute(sql + " ORDER BY project_id, raid_id", params):
        is_open = (r["status"] or "open") not in CLOSED and not (r["closed_on"] and r["closed_on"] <= as_of.isoformat())
        due = _day(r["due_date"])
        out.append(
            {
                "raid_id": r["raid_id"],
                "project_id": r["project_id"],
                "raid_type": r["raid_type"],
                "title": r["title"],
                "severity": r["severity"],
                "status": r["status"],
                "open": is_open,
                "raised_on": r["raised_on"],
                "due_date": r["due_date"],
                "closed_on": r["closed_on"],
                "days_overdue": (as_of - due).days if is_open and due and due < as_of else 0,
            }
        )
    return out


def progress(
    conn: sqlite3.Connection, jira_keys: list[str], as_of: date, window_weeks: int, velocity_weeks: int
) -> Progress:
    """Story points of the project's Jira stories: scope, done, growth over the window, velocity and forecast finish."""
    if not jira_keys:
        return Progress(0, 0.0, 0.0, 0.0, None, 0.0, None, [])
    marks = ", ".join("?" for _ in jira_keys)
    rows = conn.execute(
        f"SELECT created, resolved, COALESCE(story_points, 0) AS points FROM work_item WHERE project_key IN ({marks}) "
        "AND lower(issue_type) IN ('story', 'user story') AND created <= ?",
        [*jira_keys, _end(as_of)],
    ).fetchall()
    end = _end(as_of)
    window_start = (as_of - timedelta(weeks=window_weeks)).isoformat()
    velocity_start = (as_of - timedelta(weeks=velocity_weeks)).isoformat()
    total = sum(r["points"] for r in rows)
    done = sum(r["points"] for r in rows if r["resolved"] and r["resolved"] <= end)
    added = sum(r["points"] for r in rows if r["created"] > window_start)
    before = total - added
    growth = round(100.0 * added / before, 1) if before > 0 else None
    recent = sum(r["points"] for r in rows if r["resolved"] and velocity_start < r["resolved"] <= end)
    velocity = round(recent / velocity_weeks, 2)
    remaining = total - done
    forecast = (
        as_of
        if remaining <= 0
        else (as_of + timedelta(weeks=math.ceil(remaining / velocity)) if velocity > 0 else None)
    )
    weekly = []
    for k in range(11, -1, -1):
        cut = as_of - timedelta(weeks=k)
        cut_end = _end(cut)
        weekly.append(
            {
                "week_ending": cut.isoformat(),
                "scope_points": sum(r["points"] for r in rows if r["created"] <= cut_end),
                "done_points": sum(r["points"] for r in rows if r["resolved"] and r["resolved"] <= cut_end),
            }
        )
    return Progress(len(rows), total, done, added, growth, velocity, forecast, weekly)


def documents(conn: sqlite3.Connection, space: str | None, as_of: date) -> dict[str, Any]:
    if not space:
        return {"requirements": 0, "adrs": 0, "pages": 0, "last_updated": None, "items": []}
    items = []
    for r in conn.execute(
        "SELECT page_id, title, labels_json, last_updated FROM doc_page WHERE space_key = ? ORDER BY title", (space,)
    ):
        labels = [str(x).lower() for x in json.loads(r["labels_json"] or "[]")]
        kind = "adr" if "adr" in labels or r["title"].upper().startswith("ADR") else (
            "requirements" if "requirements" in labels or r["title"].lower().startswith("requirements") else "other"
        )  # fmt: skip
        items.append({"page_id": r["page_id"], "title": r["title"], "kind": kind, "last_updated": r["last_updated"]})
    updated = [i["last_updated"] for i in items if i["last_updated"]]
    return {
        "requirements": sum(1 for i in items if i["kind"] == "requirements"),
        "adrs": sum(1 for i in items if i["kind"] == "adr"),
        "pages": len(items),
        "last_updated": max(updated) if updated else None,
        "items": items,
    }


def health(
    project: dict[str, Any], plan: dict[str, Any], raid: list[dict[str, Any]], prog: Progress, rules: Rules, as_of: date
) -> dict[str, Any]:
    """Computed RAG with its reasons (independent of the RAG the project manager reports)."""
    reasons_red: list[str] = []
    reasons_amber: list[str] = []
    worst = max((t["slip_days"] or 0 for t in plan["tasks"] if t["is_milestone"] and not t["actual_finish"]), default=0)
    if worst >= rules.milestone_slip.high_slip_days:
        reasons_red.append(f"a milestone is {worst} days past its baseline")
    elif worst >= rules.milestone_slip.min_slip_days:
        reasons_amber.append(f"a milestone is {worst} days past its baseline")
    if any(t["overdue"] and t["is_milestone"] for t in plan["tasks"]):
        reasons_red.append("a milestone is overdue")
    severe = set(rules.raid_overdue.severities)
    if any(i["open"] and i["severity"] in severe and i["days_overdue"] > rules.raid_overdue.grace_days for i in raid):
        reasons_red.append("a high RAID item is overdue")
    elif any(i["open"] and i["severity"] in severe for i in raid):
        reasons_amber.append("high RAID items are open")
    target = _day(project.get("target_date"))
    if target and prog.points_total > prog.points_done:
        if prog.forecast_finish is None:
            reasons_red.append("no delivery in the velocity window")
        elif (prog.forecast_finish - target).days >= rules.forecast_late.min_days_late:
            reasons_red.append(f"forecast finish {prog.forecast_finish.isoformat()} is after the target go-live")
    if prog.scope_growth_pct is not None and prog.scope_growth_pct >= rules.scope_growth.min_growth_pct:
        reasons_amber.append(f"scope grew {prog.scope_growth_pct:.0f}% in {rules.scope_growth.window_weeks} weeks")
    rag = "red" if reasons_red else "amber" if reasons_amber else "green"
    return {"rag": rag, "reasons": reasons_red + reasons_amber}


def portfolio(conn: sqlite3.Connection, paths: Any, as_of: date) -> list[dict[str, Any]]:
    rules = load_rules(paths)
    out = []
    for project in projects(conn):
        plan = milestones(conn, project["project_id"], as_of)
        raid = raid_items(conn, project["project_id"], as_of)
        prog = progress(
            conn, project["jira_keys"], as_of, rules.scope_growth.window_weeks, rules.forecast_late.velocity_weeks
        )
        docs = documents(conn, project.get("confluence_space"), as_of)
        out.append(
            {
                "project": project,
                "plan": plan,
                "raid": raid,
                "progress": prog,
                "documents": docs,
                "health": health(project, plan, raid, prog, rules, as_of),
            }
        )
    return out
