"""Run handler for the `sed-draft-release-notes` skill: resolved Jira issues of a period -> release notes.

`sed ai start-run sed-draft-release-notes --subject <project id> --scope period:2026-08` (or `since:YYYY-MM-DD`, or
`new`) builds one work item: the stories, bugs and tasks of the project's Jira projects resolved in the scope, with
their epic and fix versions, and the counts as facts. Numbers in the notes appear only as `{{f:<fact_key>}}` tokens of
those facts, so the exported file shows exactly the counted values.

Ingest checks that every entry cites issue keys of the packet and that every token is a cited packet fact, warns
about resolved issues no entry mentions and about bare numbers, and writes one `delivery_release_notes` finding
(stable key `delivery_release_notes:<project>:<scope>`). The evidence lists the issues, so a later run that finds more
resolved issues is a material change. Approved notes export as Markdown with the tokens filled
(`sed delivery export release-notes`).
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

from sed.ai.contract import IngestError, IngestResult, PacketLimits, RunContext, WorkItem
from sed.ai.findings import upsert_draft
from sed.ai.runs import scope_bounds
from sed.modules.delivery.ai import common as C
from sed.modules.delivery.ai.documents import render_release_notes
from sed.modules.delivery.ai.schemas import ReleaseNotesOutput
from sed.reports.sections import bare_numbers, tokens

KIND = "delivery_release_notes"
MAX_ISSUES = 400
BUG_TYPES = ("bug", "defect")
STORY_TYPES = ("story", "user story")


def _fact_units(project_id: str) -> dict[str, str]:
    base = f"delivery.release.{project_id}"
    return {
        f"{base}.issues_resolved": "count",
        f"{base}.stories_resolved": "count",
        f"{base}.bugs_fixed": "count",
        f"{base}.points_delivered": "number",
        f"{base}.fix_versions": "text",
    }


class ReleaseNotesHandler(C.DraftHandler):
    skill: ClassVar[str] = "sed-draft-release-notes"
    schema_version: ClassVar[int] = 1
    output_model: ClassVar[type[ReleaseNotesOutput]] = ReleaseNotesOutput

    def config_inputs(self, paths: Any) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "max_issues": MAX_ISSUES}

    def packet_limits(self, limits: PacketLimits, params: Any) -> PacketLimits:
        return PacketLimits(max_items=1, max_chars=max(limits.max_chars, 200_000), max_line_chars=180_000)

    def select(self, ctx: RunContext) -> list[WorkItem]:
        project = C.project_for(ctx)
        keys = project["jira_keys"]
        if not keys:
            return []
        bounds = scope_bounds(ctx.params.scope, ctx.settings, ctx.data_as_of)
        marks = ", ".join("?" for _ in keys)
        sql = (
            f"SELECT w.issue_key, w.issue_type, w.summary, w.resolved, w.fix_versions_json, w.story_points, "
            f"e.summary AS epic FROM work_item w LEFT JOIN work_item e ON e.issue_key = w.parent_key "
            f"WHERE w.project_key IN ({marks}) AND lower(w.issue_type) <> 'epic' AND w.resolved IS NOT NULL "
            f"AND w.resolved >= ?"
        )
        params: list[Any] = [*keys, bounds.start_iso]
        if bounds.end_iso:
            sql += " AND w.resolved < ?"
            params.append(bounds.end_iso)
        rows = ctx.conn.execute(sql + " ORDER BY w.resolved, w.issue_key LIMIT ?", [*params, MAX_ISSUES]).fetchall()
        if not rows:
            return []
        issues, versions = [], set()
        for r in rows:
            fix = [str(v) for v in json.loads(r["fix_versions_json"] or "[]") if v]
            versions.update(fix)
            issues.append(
                {"key": r["issue_key"], "type": r["issue_type"], "summary": r["summary"], "epic": r["epic"],
                 "fix_versions": fix}
            )  # fmt: skip
        pid = project["project_id"]
        base = f"delivery.release.{pid}"
        facts = {
            f"{base}.issues_resolved": len(rows),
            f"{base}.stories_resolved": sum(1 for r in rows if (r["issue_type"] or "").lower() in STORY_TYPES),
            f"{base}.bugs_fixed": sum(1 for r in rows if (r["issue_type"] or "").lower() in BUG_TYPES),
            f"{base}.points_delivered": float(sum(r["story_points"] or 0 for r in rows)),
            f"{base}.fix_versions": ", ".join(sorted(versions)) or None,
        }
        payload = {
            "type": "release",
            "project_id": pid,
            "project": project["name"],
            "scope": bounds.scope,
            "from": bounds.start_iso,
            "to": bounds.end_iso,
            "issues": issues,
            "facts": facts,
        }
        return [C.item(f"release:{pid}:{bounds.scope}", "release_notes", payload)]

    def context_files(self, ctx: RunContext, items: list[WorkItem]) -> dict[str, str]:
        project = C.project_for(ctx)
        lines = C.context_header(self.skill, ctx.run_id, project)
        lines += [
            "## Packet line (one per run)",
            "- `scope`, `from`, `to`: the resolution window (UTC).",
            "- `issues[]`: resolved Jira issues (`key`, `type`, `summary`, `epic`, `fix_versions`).",
            "- `facts`: counts you may cite, keyed by fact key.",
            "",
            "## Output rules",
            "- One item for the ref: `title`, `summary_md`, `sections[]`, `known_issues`, `facts_cited`, `confidence`.",
            "- Write for business users: what they can now do or what works better, not how it was built.",
            "- Group entries under headings such as 'New features', 'Improvements' and 'Fixes'. Each entry cites the",
            "  issue keys it covers; related issues may share one entry. Cover every issue a user would notice.",
            "- Numbers only as `{{f:<fact_key>}}` tokens, and list every fact key you use in `facts_cited`.",
            "- No person names, no internal ticket jargon.",
            "",
        ]
        return {"context.md": "\n".join(lines)}

    def validate(self, ctx: RunContext, output: Any, refs: dict[str, WorkItem]) -> list[IngestError]:
        errors: list[IngestError] = []
        for idx, notes in enumerate(output.items):
            payload = refs[notes.ref].payload
            keys = {i["key"] for i in payload["issues"]}
            facts = set(payload["facts"])
            cited = set(notes.facts_cited)
            for key in sorted(cited - facts):
                errors.append(IngestError(f"items.{idx}.facts_cited", f"'{key}' is not a packet fact", notes.ref))
            texts = [("summary_md", notes.summary_md)] + [
                (f"sections.{s}.entries.{e}.text", entry.text)
                for s, section in enumerate(notes.sections)
                for e, entry in enumerate(section.entries)
            ]
            for loc, text in texts:
                for token in tokens(text):
                    if token not in cited:
                        errors.append(
                            IngestError(
                                f"items.{idx}.{loc}", f"token {{{{f:{token}}}}} is not in facts_cited", notes.ref
                            )
                        )
            for s, section in enumerate(notes.sections):
                for e, entry in enumerate(section.entries):
                    for key in entry.issue_keys:
                        if key not in keys:
                            errors.append(
                                IngestError(
                                    f"items.{idx}.sections.{s}.entries.{e}.issue_keys",
                                    f"'{key}' is not a resolved issue of the packet",
                                    notes.ref,
                                )
                            )
        return errors

    def write(self, ctx: RunContext, batch_id: str, output: Any, refs: dict[str, WorkItem]) -> IngestResult:
        warnings: list[str] = []
        for notes in output.items:
            payload = refs[notes.ref].payload
            pid = payload["project_id"]
            units = _fact_units(pid)
            mentioned = {k for section in notes.sections for entry in section.entries for k in entry.issue_keys}
            missing = [i["key"] for i in payload["issues"] if i["key"] not in mentioned]
            if missing:
                warnings.append(f"{notes.ref}: {len(missing)} resolved issues are not in any entry")
            data = notes.model_dump()
            if any(bare_numbers(t) for t in [notes.summary_md] + [e.text for s in notes.sections for e in s.entries]):
                warnings.append(f"{notes.ref}: bare numbers in the text; use {{{{f:<fact_key>}}}} tokens")
            facts = {k: {"value": payload["facts"][k], "unit": units.get(k, "text")} for k in notes.facts_cited}
            scope = payload["scope"]
            upsert_draft(
                ctx.conn,
                run_id=ctx.run_id,
                stable_key=f"{KIND}:{pid}:{scope}",
                kind=KIND,
                title=notes.title[:200],
                body_md=render_release_notes(data),
                severity=None,
                confidence=float(notes.confidence),
                payload={
                    "scope": scope,
                    "from": payload["from"],
                    "to": payload["to"],
                    "facts": facts,
                    "issue_keys": sorted(mentioned),
                    "evidence": [
                        {"fact_key": f"jira.{i['key']}.resolved", "value_at_run": True} for i in payload["issues"]
                    ],
                },
                subject_type="delivery_project",
                subject_id=pid,
                period=scope.removeprefix("period:") if scope.startswith("period:") else None,
            )
        low = sum(1 for n in output.items if n.confidence < ctx.settings.ai.low_confidence_threshold)
        return IngestResult(items=len(output.items), low_confidence=low, warnings=warnings)
