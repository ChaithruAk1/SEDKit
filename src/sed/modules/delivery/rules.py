"""Delivery rule findings ("system-detected"), the delivery module's `rule_findings` provider (see sed.rule_findings).

* delivery_risk:slip:<project>:<task>: a milestone's forecast finish is past its baseline by the threshold, or moved
  later in several plan versions.
* delivery_risk:raid_overdue:<raid id>: a high or critical RAID item is open past its due date.
* delivery_risk:scope_growth:<project>: story points added in the window exceed the growth threshold.
* delivery_risk:forecast_late:<project>: at the recent velocity the remaining story points finish after the target
  go-live (or nothing was delivered in the velocity window).

Thresholds live in config/delivery/risk_rules.yaml.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any

from sed.modules.delivery.queries import portfolio as P
from sed.paths import Paths


def _finding(key: str, subject: tuple[str, str], severity: str, title: str, evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        "stable_key": key,
        "kind": "delivery_risk",
        "subject_type": subject[0],
        "subject_id": subject[1],
        "severity": severity,
        "title": title,
        "evidence": [{"fact_key": k, "value": v} for k, v in evidence.items()],
    }


def compute(conn: sqlite3.Connection, paths: Paths | None, as_of: date) -> list[dict[str, Any]]:
    rules = P.load_rules(paths)
    out: list[dict[str, Any]] = []
    for item in P.portfolio(conn, paths, as_of):
        project, plan, prog = item["project"], item["plan"], item["progress"]
        pid, name = project["project_id"], project["name"]
        slip = rules.milestone_slip
        if slip.enabled:
            for task in plan["tasks"]:
                if not task["is_milestone"] or task["actual_finish"]:
                    continue
                days = task["slip_days"] or 0
                if days >= slip.min_slip_days or (task["replans"] >= slip.min_replans and days > 0):
                    severity = "high" if days >= slip.high_slip_days else "medium"
                    out.append(
                        _finding(
                            f"delivery_risk:slip:{pid}:{task['task_id']}",
                            ("delivery_project", pid),
                            severity,
                            f"{name}: '{task['name']}' is {days} days past its baseline ({task['replans']} replans)",
                            {
                                "milestone.slip_days": days,
                                "milestone.replans": task["replans"],
                                "milestone.baseline_finish": task["baseline_finish"],
                                "milestone.finish": task["finish"],
                            },
                        )
                    )
        overdue = rules.raid_overdue
        if overdue.enabled:
            for raid in item["raid"]:
                if (
                    raid["open"]
                    and raid["severity"] in overdue.severities
                    and raid["days_overdue"] > overdue.grace_days
                ):
                    out.append(
                        _finding(
                            f"delivery_risk:raid_overdue:{raid['raid_id']}",
                            ("delivery_project", pid),
                            "critical" if raid["severity"] == "critical" else "high",
                            f"{name}: {raid['severity']} {raid['raid_type'] or 'RAID item'} {raid['raid_id']} is "
                            f"{raid['days_overdue']} days overdue",
                            {"raid.days_overdue": raid["days_overdue"], "raid.due_date": raid["due_date"]},
                        )
                    )
        growth = rules.scope_growth
        if (
            growth.enabled
            and prog.scope_growth_pct is not None
            and prog.scope_growth_pct >= growth.min_growth_pct
            and prog.points_added_window >= growth.min_points
        ):
            out.append(
                _finding(
                    f"delivery_risk:scope_growth:{pid}",
                    ("delivery_project", pid),
                    "high" if prog.scope_growth_pct >= 2 * growth.min_growth_pct else "medium",
                    f"{name}: scope grew {prog.scope_growth_pct:.0f}% in {growth.window_weeks} weeks",
                    {"scope.growth_pct": prog.scope_growth_pct, "scope.points_added": prog.points_added_window},
                )
            )
        late = rules.forecast_late
        target = project.get("target_date")
        if late.enabled and target and prog.points_total > prog.points_done:
            target_day = date.fromisoformat(target)
            days_late = None if prog.forecast_finish is None else (prog.forecast_finish - target_day).days
            if days_late is None or days_late >= late.min_days_late:
                forecast = prog.forecast_finish.isoformat() if prog.forecast_finish else "none (no recent delivery)"
                out.append(
                    _finding(
                        f"delivery_risk:forecast_late:{pid}",
                        ("delivery_project", pid),
                        "high",
                        f"{name}: forecast finish {forecast} is after the target go-live {target}",
                        {
                            "forecast.finish": prog.forecast_finish.isoformat() if prog.forecast_finish else None,
                            "forecast.days_late": days_late,
                            "velocity.points_per_week": prog.velocity_per_week,
                            "scope.points_remaining": prog.points_total - prog.points_done,
                        },
                    )
                )
    return out
