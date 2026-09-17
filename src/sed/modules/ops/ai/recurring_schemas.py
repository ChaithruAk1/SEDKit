"""Output models of the `sed-find-recurring` skill: the only source of truth for its output_schema.json.

Regenerate the skill's output_schema.json with `sed ai schemas export` (or `scripts/codegen.py`) after changes.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from sed.ai.contract import REF_PATTERN, BatchMeta, StrictModel

ACTIONS = ("raise_problem", "kb_article", "vendor_escalation", "fix", "monitor")


class Evidence(StrictModel):
    fact_key: str = Field(
        pattern=r"^T\d{3,5}\.[a-z_]+$", description="<ref>.<fact name> of a referenced group, e.g. T004.tickets"
    )


class KeyMerge(StrictModel):
    """Two symptom keys of one application that name the same symptom: from_key is folded into to_key."""

    app_id: str = Field(min_length=1, max_length=40)
    from_key: str = Field(min_length=1, max_length=60)
    to_key: str = Field(min_length=1, max_length=60)


class Cluster(StrictModel):
    """One recurring issue: the groups it is made of, what probably causes it and what to do."""

    title: str = Field(min_length=3, max_length=120, description="Short name of the issue, no numbers or names")
    refs: list[str] = Field(min_length=1, description="Packet refs of the groups that form the issue")
    periodicity: Literal["none", "monthly", "weekly", "burst", "episode"]
    suspected_change: str | None = Field(
        None, max_length=40, description="A change number listed in a referenced group's changes_before_onset, or null"
    )
    problem_exists: bool = Field(description="True when a referenced group lists a problem record")
    root_cause_hypothesis: str = Field(min_length=1, max_length=400)
    recommended_action: Literal["raise_problem", "kb_article", "vendor_escalation", "fix", "monitor"]
    severity: Literal["low", "medium", "high", "critical"]
    evidence: list[Evidence] = Field(min_length=1, max_length=12)
    body_md: str = Field(
        min_length=1, max_length=1500, description="Markdown for the reviewer; numbers only as {{f:<fact_key>}} tokens"
    )
    confidence: float = Field(ge=0, le=1)


class RecurringOutput(StrictModel):
    """The JSON file the agent writes to runs/<run_id>/out/batch_0001.json."""

    meta: BatchMeta = Field(default_factory=BatchMeta)
    key_merges: list[KeyMerge] = Field(default_factory=list, max_length=200)
    clusters: list[Cluster] = Field(default_factory=list, max_length=60)


__all__ = ["ACTIONS", "REF_PATTERN", "Cluster", "Evidence", "KeyMerge", "RecurringOutput"]
