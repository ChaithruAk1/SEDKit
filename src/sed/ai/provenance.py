"""AI run provenance for reports: one AiRunProvenance per run behind AI-derived report content."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable

from sed.reports.snapshot import AiRunProvenance


def run_provenance(
    conn: sqlite3.Connection, run_ids: Iterable[str], *, used_for: Iterable[str] = ()
) -> list[AiRunProvenance]:
    """Provenance rows for the given runs (unknown ids are skipped), in run order."""
    ids = sorted({r for r in run_ids if r})
    if not ids:
        return []
    marks = ", ".join("?" for _ in ids)
    rows = conn.execute(
        "SELECT run_id, skill, skill_hash, status, model_reported, reviewed_by, reviewed_at, sample_n, "
        f"sample_accuracy, sample_ci_low, sample_ci_high FROM ai_run WHERE run_id IN ({marks}) ORDER BY run_seq",
        ids,
    ).fetchall()
    return [
        AiRunProvenance(
            run_id=r["run_id"],
            skill=r["skill"],
            skill_hash=r["skill_hash"],
            status=r["status"],
            model_reported=r["model_reported"],
            reviewed_by=r["reviewed_by"],
            reviewed_at=r["reviewed_at"],
            sample_n=r["sample_n"],
            sample_accuracy=r["sample_accuracy"],
            sample_ci_low=r["sample_ci_low"],
            sample_ci_high=r["sample_ci_high"],
            used_for=list(used_for),
        )
        for r in rows
    ]
