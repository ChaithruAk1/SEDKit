"""Run handler for the `sed-triage-open` skill: interactive triage of open P1-P3 incidents and problems.

Same contract as sed-triage-batch (packets, output model, ingest, sampling, review) restricted to the `open` stage of
priority 1 to 3, plus `in/suggestions.md` listing approved issue clusters and the open P1/P2 tickets of the run, so the
agent can point out likely duplicates and matching clusters in the session. Suggestions are never stored or sent
anywhere; only the labels are ingested.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

from sed.ai.contract import RunContext, WorkItem
from sed.modules.ops.ai.triage import TriageBatchHandler

MAX_PRIORITY = 3
MAX_CLUSTERS = 40


class TriageOpenHandler(TriageBatchHandler):
    skill: ClassVar[str] = "sed-triage-open"

    def config_inputs(self, paths: Any) -> dict[str, Any]:
        return {**super().config_inputs(paths), "stage": "open", "max_priority": MAX_PRIORITY}

    def select(self, ctx: RunContext) -> list[WorkItem]:
        return [
            item
            for item in super().select(ctx)
            if item.stage == "open" and (item.payload.get("prio") or 9) <= MAX_PRIORITY
        ]

    def context_files(self, ctx: RunContext, items: list[WorkItem]) -> dict[str, str]:
        files = dict(super().context_files(ctx, items))
        files["suggestions.md"] = self._suggestions(ctx, items)
        return files

    @staticmethod
    def _suggestions(ctx: RunContext, items: list[WorkItem]) -> str:
        lines = [
            "# Suggestion context (sed-triage-open)",
            "",
            "Use this only for the suggestions you give in the session; it is never ingested.",
            "",
            "## Approved issue clusters (title | applications | periodicity | recommended action)",
            "",
        ]
        rows = ctx.conn.execute(
            "SELECT title, payload_json FROM finding WHERE kind = 'issue_cluster' AND origin = 'ai' "
            "AND status IN ('approved', 'update_pending') ORDER BY created_at DESC, finding_id LIMIT ?",
            (MAX_CLUSTERS,),
        ).fetchall()
        names = dict(ctx.conn.execute("SELECT app_id, name FROM application").fetchall())
        for r in rows:
            try:
                payload = json.loads(r["payload_json"] or "{}")
            except ValueError:
                payload = {}
            apps = ", ".join(names.get(a, a) for a in payload.get("apps", [])) or "-"
            periodicity, action = payload.get("periodicity") or "-", payload.get("recommendation") or "-"
            lines.append(f"- {r['title']} | {apps} | {periodicity} | {action}")
        if not rows:
            lines.append("- none approved yet")
        lines += ["", "## Open P1/P2 lines in this run (possible parents of duplicates)", ""]
        majors = [i for i in items if (i.payload.get("prio") or 9) <= 2]
        lines += [f"- {i.payload.get('app') or '-'}: {i.payload.get('short') or ''}" for i in majors] or ["- none"]
        return "\n".join(lines) + "\n"
