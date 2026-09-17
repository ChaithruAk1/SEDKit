"""Output models of the `sed-draft-report` skill: the only source of truth for its output_schema.json."""

from __future__ import annotations

from pydantic import Field

from sed.ai.contract import BatchMeta, OutputItem, StrictModel


class SectionDraft(OutputItem):
    """The draft of one report section (`ref` of its packet line)."""

    body_md: str = Field(
        min_length=1,
        max_length=4000,
        description="Markdown text of the section; every number only as a {{f:<fact_key>}} token of facts.md",
    )
    slide_headline: str | None = Field(
        None, max_length=90, description="Optional one-line slide headline in plain words; no numbers"
    )
    cited_finding_ids: list[str] = Field(
        default_factory=list,
        max_length=20,
        description="finding_id of every finding in findings.md the text relies on",
    )


class DraftReportOutput(StrictModel):
    """The JSON file the agent writes to runs/<run_id>/out/<batch>.json (one section per batch)."""

    meta: BatchMeta = Field(default_factory=BatchMeta)
    items: list[SectionDraft] = Field(min_length=1, max_length=20)
