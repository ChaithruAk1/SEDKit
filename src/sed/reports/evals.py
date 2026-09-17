"""`sed report eval <run_id>`: score a sed-draft-report run (scores only; no ground truth needed).

Checks, over the run's section drafts against the snapshot they were drafted on:
- `tokens_resolve`: every `{{f:<fact_key>}}` token is a fact of that snapshot (0 unresolved);
- `required_sections`: every required section of the report spec has a draft in this run;
- `word_limits`: every section is within its spec word limit;
- `no_bare_numbers`: no digits outside tokens and the allowed labels;
- `citations_valid`: every cited finding exists and is not rejected or superseded.
`cited_findings_approved` and `sections_approved` are reported for readiness but do not fail the eval. The result is
also written to runs/<run_id>/eval.json, like the other evals.
"""

from __future__ import annotations

import json
from typing import Any

from sed import db
from sed.errors import PreconditionFailed
from sed.paths import Paths
from sed.reports import sections

SKILL = "sed-draft-report"


def evaluate_report_run(paths: Paths, run_id: str) -> dict[str, Any]:
    from sed.reports.specs import load_report_spec

    conn = db.connect(paths.db, readonly=True)
    try:
        run = conn.execute(
            "SELECT run_id, skill, status, params_json FROM ai_run WHERE run_id = ?", (run_id,)
        ).fetchone()
        if run is None:
            raise PreconditionFailed(f"Unknown AI run '{run_id}'")
        if run["skill"] != SKILL:
            raise PreconditionFailed(f"Run {run_id} is a {run['skill']} run, not {SKILL}")
        params = json.loads(run["params_json"] or "{}")
        report_key = params.get("report")
        spec = load_report_spec(report_key, paths)
        limits = {s.key: s.max_words for s in spec.sections}
        rows = conn.execute(
            "SELECT finding_id, stable_key, status, body_md, payload_json FROM finding WHERE run_id = ? AND kind = ?",
            (run_id, sections.KIND),
        ).fetchall()
        drafted: dict[str, dict[str, Any]] = {}
        unresolved = over_limit = bare = invalid_citations = 0
        cited_total = cited_approved = 0
        snapshot_cache: dict[str, dict[str, Any] | None] = {}
        for r in rows:
            payload = json.loads(r["payload_json"] or "{}")
            key = str(payload.get("section_key"))
            snapshot_id = str(payload.get("snapshot_id"))
            if snapshot_id not in snapshot_cache:
                snap = conn.execute(
                    "SELECT facts_json FROM report_snapshot WHERE snapshot_id = ?", (snapshot_id,)
                ).fetchone()
                snapshot_cache[snapshot_id] = json.loads(snap["facts_json"]) if snap else None
            facts = snapshot_cache[snapshot_id] or {}
            body = r["body_md"] or ""
            missing = [t for t in sections.tokens(body) if t not in facts]
            words = len(sections.TOKEN_RE.sub("N", body).split())
            unresolved += len(missing)
            over_limit += int(words > limits.get(key, 10**6))
            bare += int(sections.bare_numbers(body))
            for fid in payload.get("cited_finding_ids") or []:
                cited_total += 1
                cited = conn.execute("SELECT origin, status FROM finding WHERE finding_id = ?", (fid,)).fetchone()
                if cited is None or cited["status"] in ("rejected", "superseded"):
                    invalid_citations += 1
                elif cited["origin"] == "rule" or cited["status"] in sections.PUBLISHED:
                    cited_approved += 1
            drafted[key] = {"status": r["status"], "words": words, "limit": limits.get(key), "unresolved": missing}
    finally:
        conn.close()
    required = [s.key for s in spec.sections if s.required]
    missing_sections = [k for k in required if k not in drafted]
    checks = {
        "tokens_resolve": unresolved == 0,
        "required_sections": not missing_sections,
        "word_limits": over_limit == 0,
        "no_bare_numbers": bare == 0,
        "citations_valid": invalid_citations == 0,
    }
    result = {
        "run_id": run_id,
        "skill": SKILL,
        "report": report_key,
        "period": params.get("scope", "").removeprefix("period:"),
        "vendor_id": params.get("vendor"),
        "passed": all(checks.values()),
        "checks": checks,
        "sections_drafted": len(drafted),
        "sections_required": len(required),
        "missing_sections": missing_sections,
        "unresolved_tokens": unresolved,
        "over_word_limit": over_limit,
        "bare_number_sections": bare,
        "cited_findings": cited_total,
        "cited_findings_approved": cited_approved,
        "sections_approved": sum(1 for d in drafted.values() if d["status"] in sections.PUBLISHED),
        "sections": drafted,
    }
    target = paths.runs / run_id / "eval.json"
    if target.parent.is_dir():
        target.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result
