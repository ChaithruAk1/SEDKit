"""AI contributions to ops reports: approved triage runs behind the weekly category breakdown, and approved findings.

`weekly_ai(req)` finds the approved runs whose labels `v_ticket` currently shows for incidents opened in the period
(`v_ticket.label_run_id`) and returns their provenance plus four AI-derived facts. The category breakdown table and
all four facts are declared AI-derived, so `--ai none` removes them (and their definitions) at render time.
`approved_findings(req)` does the same for published AI findings (recurring issues and risks).
"""

from __future__ import annotations

import json
from typing import Any

from sed.ai.provenance import run_provenance
from sed.reports.snapshot import AiParts, SnapshotRequest, fact, table

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


# -- approved AI findings --------------------------------------------------------------------------------------------

FINDINGS_TABLE = "ai_findings"
FINDINGS_FACT = "ai.findings.approved.count"
# Ops finding kinds an AI run can publish in the weekly review (report sections belong to report drafting, M5).
FINDING_KINDS = ("issue_cluster", "vendor_risk", "renewal_risk", "license_risk", "cost_risk", "rationalization")
_SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def approved_findings(req: SnapshotRequest) -> tuple[dict[str, Any], AiParts]:
    """Published AI findings (approved, or update_pending with their approved text) of the ops kinds, as one table.

    The table and its count fact are AI-derived, so `--ai none` removes them; the runs behind the rows are listed in
    the provenance with `used_for` ai_findings. Rows carry title, severity and subject only: numbers stay in the
    finding evidence, which the dashboard shows.
    """
    marks = ", ".join("?" for _ in FINDING_KINDS)
    rows = req.conn.execute(
        "SELECT finding_id, run_id, kind, severity, title, subject_type, subject_id, reviewed_by, reviewed_at, "
        f"payload_json FROM v_findings_published WHERE origin = 'ai' AND kind IN ({marks})",
        FINDING_KINDS,
    ).fetchall()
    out = []
    for r in rows:
        try:
            payload = json.loads(r["payload_json"] or "{}")
        except ValueError:
            payload = {}
        out.append(
            {
                "severity": r["severity"],
                "kind": r["kind"],
                "title": r["title"],
                "subject_id": r["subject_id"],
                "tickets": payload.get("ticket_count"),
                "recommendation": payload.get("recommendation"),
                "reviewed_by": r["reviewed_by"],
                "run_id": r["run_id"],
            }
        )
    out.sort(key=lambda x: (_SEVERITY_RANK.get(x["severity"] or "", 9), x["kind"], x["title"]))
    findings_table = table(
        "AI-assisted findings (approved)",
        [
            ("severity", "Severity", "text"),
            ("kind", "Kind", "text"),
            ("title", "Finding", "text"),
            ("subject_id", "Subject", "text"),
            ("tickets", "Tickets", "count"),
            ("recommendation", "Recommendation", "text"),
            ("reviewed_by", "Approved by", "text"),
            ("run_id", "AI run", "text"),
        ],
        out,
    )
    facts = {FINDINGS_FACT: fact(len(out), "count", "Approved AI findings", FINDINGS_FACT)}
    runs = run_provenance(req.conn, {x["run_id"] for x in out}, used_for=(FINDINGS_TABLE,))
    return findings_table, AiParts(facts, runs, (FINDINGS_TABLE,), (FINDINGS_FACT,))
