"""Review-rate trends per skill version: how people decided on each skill's output, by skill hash and month.

For finding skills: drafts, approved as written, edited then approved, rejected, still open, and the approval / edit /
reject rates over decided findings. For label skills: runs approved or rejected and their mean sample accuracy. A new
skill hash (changed SKILL.md, references or taxonomy) starts a new row, so a skill change that makes reviewers edit
or reject more shows up next to the previous version. Read-only.
"""

from __future__ import annotations

import sqlite3
from typing import Any


def _rate(part: int, whole: int) -> float | None:
    return round(part / whole, 4) if whole else None


def review_rates(conn: sqlite3.Connection, *, skill: str | None = None) -> dict[str, Any]:
    params: list[Any] = []
    where = "r.skill <> 'manual'"
    if skill:
        where += " AND r.skill = ?"
        params.append(skill)
    runs = conn.execute(
        "SELECT r.skill, r.skill_hash, substr(r.started_at, 1, 7) AS month, COUNT(*) AS runs, "
        "SUM(r.status = 'approved') AS approved_runs, SUM(r.status = 'rejected') AS rejected_runs, "
        "AVG(r.sample_accuracy) AS sample_accuracy, MIN(r.started_at) AS first_run, MAX(r.started_at) AS last_run "
        f"FROM ai_run r WHERE {where} GROUP BY r.skill, r.skill_hash, month",
        params,
    ).fetchall()
    findings = {
        (row["skill"], row["skill_hash"], row["month"]): row
        for row in conn.execute(
            "SELECT r.skill, r.skill_hash, substr(r.started_at, 1, 7) AS month, COUNT(*) AS drafted, "
            "SUM(f.status IN ('approved', 'update_pending', 'superseded') AND f.edited = 0) AS approved, "
            "SUM(f.status IN ('approved', 'update_pending', 'superseded') AND f.edited = 1) AS edited, "
            "SUM(f.status = 'rejected') AS rejected, "
            "SUM(f.status IN ('draft', 'stale_input')) AS open "
            f"FROM finding f JOIN ai_run r ON r.run_id = f.run_id WHERE f.origin = 'ai' AND {where} "
            "GROUP BY r.skill, r.skill_hash, month",
            params,
        ).fetchall()
    }
    rows = []
    for run in runs:
        key = (run["skill"], run["skill_hash"], run["month"])
        f = findings.get(key)
        approved, edited, rejected = (int(f[c] or 0) if f else 0 for c in ("approved", "edited", "rejected"))
        decided = approved + edited + rejected
        rows.append(
            {
                "skill": run["skill"],
                "skill_hash": run["skill_hash"],
                "month": run["month"],
                "runs": int(run["runs"]),
                "approved_runs": int(run["approved_runs"] or 0),
                "rejected_runs": int(run["rejected_runs"] or 0),
                "sample_accuracy": round(run["sample_accuracy"], 4) if run["sample_accuracy"] is not None else None,
                "findings_drafted": int(f["drafted"]) if f else 0,
                "findings_approved": approved,
                "findings_edited": edited,
                "findings_rejected": rejected,
                "findings_open": int(f["open"] or 0) if f else 0,
                "approval_rate": _rate(approved, decided),
                "edit_rate": _rate(edited, decided),
                "reject_rate": _rate(rejected, decided),
                "first_run": run["first_run"],
                "last_run": run["last_run"],
            }
        )
    rows.sort(key=lambda r: (r["skill"], r["month"], r["first_run"]))
    return {"rows": rows}
