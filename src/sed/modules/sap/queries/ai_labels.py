"""AI-assisted SAP subcategory breakdown: current sed-triage-batch labels of SAP tickets opened in a window.

A ticket's current label follows the ops rule (sed.modules.ops.queries.search.current_label_sql): the resolved stage
before the open stage, manual corrections first, then the latest run, only labels computed on the ticket's current
content. Approved runs only, unless drafts are asked for (completed, unreviewed runs; dashboard only). The accuracy
shown with the breakdown is the review-sample accuracy of the run behind most of the labels.
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from typing import Any

from sed.ai.provenance import run_provenance
from sed.modules.ops.queries.search import current_label_sql, label_statuses
from sed.modules.sap.scope import Scope
from sed.modules.sap.taxonomy import CODE_PREFIX, SapTaxonomy, label

KINDS = ("incident", "problem")  # the ticket kinds sed-triage-batch labels
USED_FOR = ("sap_ai_subcategories",)


def subcategory_label(code: str | None, taxonomy: SapTaxonomy) -> str:
    """Table label: SAP codes by name, portfolio subcategories as written, none as 'No subcategory'."""
    if code and code.startswith(CODE_PREFIX):
        return taxonomy.labels().get(code, label(code))
    return code.replace("_", " ").capitalize() if code else label(None)


def breakdown(
    conn: sqlite3.Connection,
    scope: Scope,
    taxonomy: SapTaxonomy,
    start_iso: str,
    end_iso: str,
    *,
    area: str | None = None,
    landscape: str | None = None,
    include_drafts: bool = False,
) -> dict[str, Any]:
    sql, params = scope.ticket_sql(area=area, landscape=landscape)
    rows = conn.execute(
        "SELECT l.am_category, l.am_subcategory, l.run_id FROM ticket t "
        f"LEFT JOIN ai_ticket_label l ON l.rowid = {current_label_sql(label_statuses(include_drafts))} "
        f"WHERE t.kind IN ({', '.join('?' for _ in KINDS)}) AND t.opened_at >= ? AND t.opened_at < ? "
        f"AND {sql.format(t='t')}",
        (*KINDS, start_iso, end_iso, *params),
    ).fetchall()
    labelled = [r for r in rows if r["run_id"] is not None]
    counts = Counter((r["am_category"], r["am_subcategory"]) for r in labelled)
    per_run = Counter(r["run_id"] for r in labelled)
    runs = run_provenance(conn, per_run, used_for=USED_FOR)
    main = max(runs, key=lambda r: (per_run.get(r["run_id"], 0), r["run_id"])) if runs else None
    total = len(labelled)
    return {
        "tickets": len(rows),
        "labelled": total,
        "labelled_pct": round(100.0 * total / len(rows), 1) if rows else None,
        "include_drafts": include_drafts,
        "unapproved_labels": sum(per_run[r["run_id"]] for r in runs if r["status"] != "approved"),
        "sample_accuracy": main["sample_accuracy"] if main else None,
        "sample_ci_low": main["sample_ci_low"] if main else None,
        "sample_ci_high": main["sample_ci_high"] if main else None,
        "sample_n": main["sample_n"] if main else None,
        "runs": runs,
        "rows": [
            {
                "category": category,
                "subcategory": sub,
                "label": subcategory_label(sub, taxonomy),
                "sap": bool(sub and sub.startswith(CODE_PREFIX)),
                "tickets": n,
                "share": round(100.0 * n / total, 1),
            }
            for (category, sub), n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0][0], kv[0][1] or ""))
        ],
    }
