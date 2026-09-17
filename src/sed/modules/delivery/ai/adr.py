"""Run handler for the `sed-draft-adr` skill: design context -> architecture decision records (proposed).

`sed ai start-run sed-draft-adr --subject <project id>` builds one work item for the project: its requirements pages,
the ADRs already recorded in Confluence and the titles of its approved user stories. The agent proposes the decisions
the design still needs, in the MADR shape (context, drivers, options with pros and cons, chosen option, consequences).

Ingest writes one `delivery_adr` finding per ADR (stable key `delivery_adr:<project>:<title slug>`) with the rendered
Markdown as body, and refuses titles that repeat a recorded ADR, related pages that are not on the packet and a chosen
option that is not one of the options. Approved ADRs export as Markdown files for Confluence
(`sed delivery export adr`).
"""

from __future__ import annotations

from typing import Any, ClassVar

from sed.ai.contract import IngestError, IngestResult, PacketLimits, RunContext, WorkItem
from sed.ai.findings import upsert_draft
from sed.modules.delivery.ai import common as C
from sed.modules.delivery.ai.documents import parse_stories, render_adr
from sed.modules.delivery.ai.schemas import AdrOutput

KIND = "delivery_adr"
DESIGN_BODY_CHARS = 1500


class AdrHandler(C.DraftHandler):
    skill: ClassVar[str] = "sed-draft-adr"
    schema_version: ClassVar[int] = 1
    output_model: ClassVar[type[AdrOutput]] = AdrOutput

    def config_inputs(self, paths: Any) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "body_chars": DESIGN_BODY_CHARS}

    def packet_limits(self, limits: PacketLimits, params: Any) -> PacketLimits:
        return PacketLimits(max_items=1, max_chars=max(limits.max_chars, 120_000), max_line_chars=100_000)

    def select(self, ctx: RunContext) -> list[WorkItem]:
        project = C.project_for(ctx)
        space = project.get("confluence_space")

        def short(page: dict[str, Any]) -> dict[str, Any]:
            return {**page, "body": page["body"][:DESIGN_BODY_CHARS]}

        requirements = [short(p) for p in C.pages(ctx.conn, space, "requirements")]
        adrs = [short(p) for p in C.pages(ctx.conn, space, "adr")]
        stories = []
        for row in C.published(ctx.conn, "delivery_stories", project["project_id"]):
            try:
                stories += [s["title"] for s in parse_stories(row["body_md"] or "", row["finding_id"])]
            except Exception:  # an unreadable approved set is skipped here; its export reports the line
                continue
        if not requirements and not adrs:
            return []
        payload = {
            "type": "design_context",
            "project_id": project["project_id"],
            "project": project["name"],
            "application": project.get("app_raw"),
            "phase": project.get("phase"),
            "target_date": project.get("target_date"),
            "requirements": requirements,
            "recorded_adrs": adrs,
            "approved_story_titles": stories[:200],
        }
        return [C.item(f"project:{project['project_id']}", "adr", payload)]

    def context_files(self, ctx: RunContext, items: list[WorkItem]) -> dict[str, str]:
        project = C.project_for(ctx)
        lines = C.context_header(self.skill, ctx.run_id, project)
        lines += [
            "## Packet line (one per project)",
            "- `requirements[]` and `recorded_adrs[]`: Confluence pages (`page_id`, `title`, `last_updated`, `body`).",
            "- `approved_story_titles[]`: user stories a person has approved for this project.",
            "",
            "## Task",
            "Propose the architecture decisions the requirements still need and the recorded ADRs do not cover:",
            "integration style, data ownership, security and identity, hosting and deployment, build or buy.",
            "Never restate a recorded ADR; a decision that changes one names it in `context`.",
            "",
            "## Output rules",
            "- One item for the ref. `adrs[]` may be empty when nothing is missing.",
            "- `title`: the decision in a few words, unique, not the title of a recorded ADR.",
            "- `context`: the problem from the packet only; no invented systems, vendors, costs or names.",
            "- `options`: 2 to 4 realistic options with pros and cons; `chosen_option` repeats one option name.",
            "- `related_page_ids`: page_id values from the packet the ADR relies on.",
            "- `confidence`: probability that the set is right and complete.",
            "",
        ]
        return {"context.md": "\n".join(lines)}

    def validate(self, ctx: RunContext, output: Any, refs: dict[str, WorkItem]) -> list[IngestError]:
        errors: list[IngestError] = []
        for idx, adr_set in enumerate(output.items):
            payload = refs[adr_set.ref].payload
            page_ids = {p["page_id"] for p in payload["requirements"] + payload["recorded_adrs"]}
            recorded = {C.slug(p["title"]) for p in payload["recorded_adrs"]}
            recorded |= {C.slug(p["title"].split(" ", 1)[1]) for p in payload["recorded_adrs"] if " " in p["title"]}
            seen: set[str] = set()
            for a_idx, adr in enumerate(adr_set.adrs):
                loc = f"items.{idx}.adrs.{a_idx}"
                key = C.slug(adr.title)
                if key in seen:
                    errors.append(IngestError(f"{loc}.title", "duplicate ADR title", adr_set.ref))
                if key in recorded:
                    errors.append(
                        IngestError(f"{loc}.title", "an ADR with this title is already recorded", adr_set.ref)
                    )
                seen.add(key)
                names = [o.name.strip().lower() for o in adr.options]
                if len(set(names)) != len(names):
                    errors.append(IngestError(f"{loc}.options", "option names must be unique", adr_set.ref))
                if adr.chosen_option.strip().lower() not in names:
                    errors.append(IngestError(f"{loc}.chosen_option", "must be one of the option names", adr_set.ref))
                for page_id in sorted(set(adr.related_page_ids) - page_ids):
                    errors.append(
                        IngestError(f"{loc}.related_page_ids", f"page '{page_id}' is not on the packet", adr_set.ref)
                    )
        return errors

    def write(self, ctx: RunContext, batch_id: str, output: Any, refs: dict[str, WorkItem]) -> IngestResult:
        count = 0
        for adr_set in output.items:
            payload = refs[adr_set.ref].payload
            titles = {p["page_id"]: p["title"] for p in payload["requirements"] + payload["recorded_adrs"]}
            versions = {p["page_id"]: p.get("last_updated") for p in payload["requirements"] + payload["recorded_adrs"]}
            for adr in adr_set.adrs:
                data = adr.model_dump()
                count += 1
                upsert_draft(
                    ctx.conn,
                    run_id=ctx.run_id,
                    stable_key=f"{KIND}:{payload['project_id']}:{C.slug(adr.title)}",
                    kind=KIND,
                    title=f"ADR: {adr.title}"[:200],
                    body_md=render_adr(payload["project"], data, titles),
                    severity=None,
                    confidence=float(adr_set.confidence),
                    payload={
                        **data,
                        "evidence": [
                            {"fact_key": f"doc_page.{p}.last_updated", "value_at_run": versions.get(p)}
                            for p in adr.related_page_ids
                        ],
                    },
                    subject_type="delivery_project",
                    subject_id=payload["project_id"],
                )
        low = sum(len(s.adrs) for s in output.items if s.confidence < ctx.settings.ai.low_confidence_threshold)
        return IngestResult(items=count, low_confidence=low)
