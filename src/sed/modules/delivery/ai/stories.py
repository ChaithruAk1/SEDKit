"""Run handler for the `sed-draft-stories` skill: requirements pages -> user stories with acceptance criteria.

`sed ai start-run sed-draft-stories --subject <project id>` selects the requirements pages (label `requirements` or a
title starting "Requirements") of the project's Confluence space, one work item per page. context.md lists the project,
its Jira epics and the stories already in Jira, so the agent can link epics and avoid duplicates.

Ingest writes one `delivery_stories` finding per page (stable key `delivery_stories:<project>:<page_id>`) whose body is
the story set in the Markdown shape of `documents.render_stories`. The evidence carries a version key of the page text,
so a changed page is a material change and returns to full review; the same page re-drafted carries forward as
update_pending. Approved sets export as a Jira CSV import file (`sed delivery export stories`).
"""

from __future__ import annotations

from typing import Any, ClassVar

from sed.ai.contract import IngestError, IngestResult, PacketLimits, RunContext, WorkItem
from sed.ai.findings import upsert_draft
from sed.modules.delivery.ai import common as C
from sed.modules.delivery.ai.documents import render_stories
from sed.modules.delivery.ai.schemas import StoriesOutput

KIND = "delivery_stories"
EXISTING_STORIES = 150


class StoriesHandler(C.DraftHandler):
    skill: ClassVar[str] = "sed-draft-stories"
    schema_version: ClassVar[int] = 1
    output_model: ClassVar[type[StoriesOutput]] = StoriesOutput

    def config_inputs(self, paths: Any) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "body_chars": C.BODY_CHARS}

    def packet_limits(self, limits: PacketLimits, params: Any) -> PacketLimits:
        return PacketLimits(max_items=8, max_chars=max(limits.max_chars, 60_000), max_line_chars=C.BODY_CHARS + 800)

    def select(self, ctx: RunContext) -> list[WorkItem]:
        project = C.project_for(ctx)
        return [
            C.item(
                f"page:{page['page_id']}",
                "stories",
                {"type": "requirements_page", "project_id": project["project_id"], **page},
            )
            for page in C.pages(ctx.conn, project.get("confluence_space"), "requirements")
        ]

    def context_files(self, ctx: RunContext, items: list[WorkItem]) -> dict[str, str]:
        project = C.project_for(ctx)
        keys = project["jira_keys"]
        existing = []
        if keys:
            marks = ", ".join("?" for _ in keys)
            existing = ctx.conn.execute(
                f"SELECT issue_key, summary, status FROM work_item WHERE project_key IN ({marks}) "
                "AND lower(issue_type) IN ('story', 'user story') ORDER BY created DESC LIMIT ?",
                [*keys, EXISTING_STORIES],
            ).fetchall()
        lines = C.context_header(self.skill, ctx.run_id, project)
        lines += ["## Jira epics (use these keys for `epic_key`, or null)"]
        lines += [f"- {k}: {v}" for k, v in C.epics(ctx.conn, keys).items()] or ["- none"]
        lines += ["", f"## Stories already in Jira (latest {EXISTING_STORIES}; do not draft duplicates)"]
        lines += [f"- {r['issue_key']} [{r['status']}]: {r['summary']}" for r in existing] or ["- none"]
        lines += [
            "",
            "## Packet lines (one requirements page per line)",
            "- `ref`, `page_id`, `title`, `last_updated`, `body` (scrubbed page text, truncated).",
            "",
            "## Output rules",
            "- One item per ref, every ref exactly once.",
            "- `stories[]`: small, independent, testable stories that together cover the page. `as_a` is a role,",
            "  never a person. `acceptance_criteria` are testable, ideally 'Given ..., when ..., then ...'.",
            "- `priority`: highest | high | medium | low | lowest. `estimate_points`: 1, 2, 3, 5, 8, 13 or null.",
            "- `epic_key`: one of the epic keys above that fits, else null.",
            "- A page with nothing to build: `stories: []` and the reason in `open_questions`.",
            "- `open_questions`: what the page leaves unclear (for the product owner).",
            "- `confidence`: probability that the set covers the page correctly.",
            "",
        ]
        return {"context.md": "\n".join(lines)}

    def validate(self, ctx: RunContext, output: Any, refs: dict[str, WorkItem]) -> list[IngestError]:
        project = C.project_for(ctx)
        known_epics = set(C.epics(ctx.conn, project["jira_keys"]))
        errors: list[IngestError] = []
        for idx, story_set in enumerate(output.items):
            loc = f"items.{idx}"
            if not story_set.stories and not story_set.open_questions:
                errors.append(IngestError(f"{loc}.open_questions", "empty stories need open_questions", story_set.ref))
            seen: set[str] = set()
            for s_idx, story in enumerate(story_set.stories):
                title = story.title.strip().lower()
                if title in seen:
                    errors.append(IngestError(f"{loc}.stories.{s_idx}.title", "duplicate title", story_set.ref))
                seen.add(title)
                if story.epic_key and story.epic_key not in known_epics:
                    errors.append(
                        IngestError(
                            f"{loc}.stories.{s_idx}.epic_key",
                            f"'{story.epic_key}' is not an epic of this project",
                            story_set.ref,
                        )
                    )
        return errors

    def write(self, ctx: RunContext, batch_id: str, output: Any, refs: dict[str, WorkItem]) -> IngestResult:
        project = C.project_for(ctx)
        warnings = []
        for story_set in output.items:
            work = refs[story_set.ref]
            page = work.payload
            stories = [s.model_dump() for s in story_set.stories]
            if not stories:
                warnings.append(f"{story_set.ref}: no stories for page {page['page_id']}")
            upsert_draft(
                ctx.conn,
                run_id=ctx.run_id,
                stable_key=f"{KIND}:{project['project_id']}:{page['page_id']}",
                kind=KIND,
                title=f"User stories: {page['title']}"[:200],
                body_md=render_stories(page["title"], page["page_id"], stories, story_set.open_questions),
                severity=None,
                confidence=float(story_set.confidence),
                payload={
                    "page_id": page["page_id"],
                    "page_title": page["title"],
                    "stories": stories,
                    "open_questions": story_set.open_questions,
                    "evidence": [
                        {
                            "fact_key": f"doc_page.{page['page_id']}.version.{work.input_hash[:12]}",
                            "value_at_run": page.get("last_updated"),
                        }
                    ],
                },
                subject_type="delivery_project",
                subject_id=project["project_id"],
            )
        low = sum(1 for s in output.items if s.confidence < ctx.settings.ai.low_confidence_threshold)
        return IngestResult(items=len(output.items), low_confidence=low, warnings=warnings)
