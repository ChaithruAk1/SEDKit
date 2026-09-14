"""AI contributions to ops reports (ws1-ai): approved triage runs behind the weekly category breakdown.

`weekly_ai(req)` finds the approved runs whose labels `v_ticket` currently shows for incidents opened in the period
(`v_ticket.label_run_id`) and returns their provenance plus four AI-derived facts. The category breakdown table and
all four facts are declared AI-derived, so `--ai none` removes them (and their definitions) at render time.
"""

from __future__ import annotations

from sed.ai.provenance import run_provenance
from sed.reports.snapshot import AiParts, SnapshotRequest, fact

USED_FOR = ("category_breakdown",)
AI_FACTS = (
    "ai.category.labelled_pct",
    "ai.category.sample_accuracy_pct",
    "ai.category.sample_ci_low_pct",
    "ai.category.sample_ci_high_pct",
)


def _pct(value: float | None) -> float | None:
    return round(100.0 * value, 1) if value is not None else None


def weekly_ai(req: SnapshotRequest) -> AiParts:
    period = req.period
    counts = req.conn.execute(
        "SELECT label_run_id, COUNT(*) AS n FROM v_ticket WHERE kind = 'incident' AND opened_at >= ? "
        "AND opened_at < ? AND label_run_id IS NOT NULL GROUP BY label_run_id",
        (period.start_iso, period.end_iso),
    ).fetchall()
    per_run = {r["label_run_id"]: int(r["n"]) for r in counts}
    runs = run_provenance(req.conn, per_run, used_for=USED_FOR)
    labelled = labelled_pct = accuracy = ci_low = ci_high = None
    if runs:
        opened = req.conn.execute(
            "SELECT COUNT(*) FROM ticket WHERE kind = 'incident' AND opened_at >= ? AND opened_at < ?",
            (period.start_iso, period.end_iso),
        ).fetchone()[0]
        labelled = sum(per_run.values())
        labelled_pct = round(100.0 * labelled / opened, 1) if opened else None
        main = max(runs, key=lambda r: (per_run.get(r["run_id"], 0), r["run_id"]))
        accuracy, ci_low, ci_high = main["sample_accuracy"], main["sample_ci_low"], main["sample_ci_high"]
    facts = {
        "ai.category.labelled_pct": fact(
            labelled_pct, "pct", "Incidents with an approved AI category", "ai.category.labelled_pct"
        ),
        "ai.category.sample_accuracy_pct": fact(
            _pct(accuracy), "pct", "AI category sample accuracy", "ai.category.sample_accuracy_pct"
        ),
        "ai.category.sample_ci_low_pct": fact(
            _pct(ci_low), "pct", "AI category accuracy, 95% CI low", "ai.category.sample_ci_low_pct"
        ),
        "ai.category.sample_ci_high_pct": fact(
            _pct(ci_high), "pct", "AI category accuracy, 95% CI high", "ai.category.sample_ci_high_pct"
        ),
    }
    return AiParts(facts, runs, USED_FOR, AI_FACTS)
