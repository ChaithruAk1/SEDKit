"""Run handler for the `sed-draft-test-plan` skill: approved user stories -> test cases.

`sed ai start-run sed-draft-test-plan --subject <project id>` selects the project's published `delivery_stories`
findings (approved, or update_pending showing the approved text), one work item per story set, with the stories read
back from the approved Markdown so reviewer edits count.

Ingest checks that every case names a story of its set and that every story has at least one case, then writes one
`delivery_test_plan` finding per set (stable key `delivery_test_plan:<project>:<page_id>`) citing the story finding
(`cited_finding_ids`, checked again when the plan is approved). The evidence names the story finding, so a re-approved
story set is a material change. Approved plans export as Markdown and a CSV of test cases
(`sed delivery export test-plan`).
"""

from __future__ import annotations

from typing import Any, ClassVar

from sed.ai.contract import IngestError, IngestResult, PacketLimits, RunContext, WorkItem
from sed.ai.findings import upsert_draft
from sed.modules.delivery.ai import common as C
from sed.modules.delivery.ai.documents import parse_stories, render_test_plan
from sed.modules.delivery.ai.schemas import CasesOutput

KIND = "delivery_test_plan"


class TestPlanHandler(C.DraftHandler):
    __test__ = False  # not a pytest class

    skill: ClassVar[str] = "sed-draft-test-plan"
    schema_version: ClassVar[int] = 1
    output_model: ClassVar[type[CasesOutput]] = CasesOutput

    def config_inputs(self, paths: Any) -> dict[str, Any]:
        return {"schema_version": self.schema_version}

    def packet_limits(self, limits: PacketLimits, params: Any) -> PacketLimits:
        return PacketLimits(max_items=6, max_chars=max(limits.max_chars, 60_000), max_line_chars=20_000)

    def select(self, ctx: RunContext) -> list[WorkItem]:
        project = C.project_for(ctx)
        items = []
        for row in C.published(ctx.conn, "delivery_stories", project["project_id"]):
            stories = parse_stories(row["body_md"] or "", row["finding_id"])
            if not stories:
                continue
            data = C.payload_of(row)
            payload = {
                "type": "story_set",
                "project_id": project["project_id"],
                "finding_id": row["finding_id"],
                "run_id": row["run_id"],
                "page_id": data.get("page_id") or row["stable_key"].rsplit(":", 1)[-1],
                "page_title": data.get("page_title") or row["title"],
                "stories": [
                    {k: s[k] for k in ("title", "as_a", "i_want", "so_that", "acceptance_criteria")} for s in stories
                ],
            }
            items.append(C.item(f"stories:{row['finding_id']}", "test_plan", payload))
        return items

    def input_run_ids(self, ctx: RunContext, items: list[WorkItem]) -> list[str]:
        return [i.payload["run_id"] for i in items if i.payload.get("run_id")]

    def context_files(self, ctx: RunContext, items: list[WorkItem]) -> dict[str, str]:
        project = C.project_for(ctx)
        lines = C.context_header(self.skill, ctx.run_id, project)
        lines += [
            "## Packet lines (one approved story set per line)",
            "- `page_title` and `stories[]` (`title`, `as_a`, `i_want`, `so_that`, `acceptance_criteria`).",
            "",
            "## Output rules",
            "- One item per ref, every ref exactly once.",
            "- `cases[]`: every story has at least one case and every acceptance criterion is checked by a case.",
            "  Add negative cases for validation rules and integration cases where the story crosses a system.",
            "- `story`: the story title verbatim. `type`: functional | negative | integration | performance |",
            "  security | accessibility | uat.",
            "- `steps`: short imperative steps a tester can follow; `expected`: one observable result.",
            "- No real people, credentials, hosts or customer data in preconditions or steps; use roles and",
            "  placeholders such as <test supplier>.",
            "- `not_covered`: what these cases deliberately leave out (for example load tests), and why.",
            "",
        ]
        return {"context.md": "\n".join(lines)}

    def validate(self, ctx: RunContext, output: Any, refs: dict[str, WorkItem]) -> list[IngestError]:
        errors: list[IngestError] = []
        for idx, plan in enumerate(output.items):
            titles = {s["title"].strip().lower(): s["title"] for s in refs[plan.ref].payload["stories"]}
            covered: set[str] = set()
            seen: set[str] = set()
            for c_idx, case in enumerate(plan.cases):
                loc = f"items.{idx}.cases.{c_idx}"
                key = case.story.strip().lower()
                if key not in titles:
                    errors.append(IngestError(f"{loc}.story", f"'{case.story}' is not a story of this set", plan.ref))
                covered.add(key)
                if case.title.strip().lower() in seen:
                    errors.append(IngestError(f"{loc}.title", "duplicate test case title", plan.ref))
                seen.add(case.title.strip().lower())
            for key in sorted(set(titles) - covered):
                errors.append(IngestError(f"items.{idx}.cases", f"story '{titles[key]}' has no test case", plan.ref))
        return errors

    def write(self, ctx: RunContext, batch_id: str, output: Any, refs: dict[str, WorkItem]) -> IngestResult:
        for plan in output.items:
            payload = refs[plan.ref].payload
            cases = [c.model_dump() for c in plan.cases]
            upsert_draft(
                ctx.conn,
                run_id=ctx.run_id,
                stable_key=f"{KIND}:{payload['project_id']}:{payload['page_id']}",
                kind=KIND,
                title=f"Test plan: {payload['page_title']}"[:200],
                body_md=render_test_plan(payload["page_title"], cases, plan.not_covered),
                severity=None,
                confidence=float(plan.confidence),
                payload={
                    "page_id": payload["page_id"],
                    "page_title": payload["page_title"],
                    "cases": cases,
                    "not_covered": plan.not_covered,
                    "cited_finding_ids": [payload["finding_id"]],
                    "evidence": [{"fact_key": f"finding.{payload['finding_id']}", "value_at_run": "published"}],
                },
                subject_type="delivery_project",
                subject_id=payload["project_id"],
            )
        low = sum(1 for p in output.items if p.confidence < ctx.settings.ai.low_confidence_threshold)
        return IngestResult(items=len(output.items), low_confidence=low)
