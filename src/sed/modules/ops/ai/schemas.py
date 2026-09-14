"""Output models of the `sed-triage-batch` skill: the only source of truth for its output_schema.json.

Regenerate the skill's output_schema.json and the workflow schema block with `sed ai schemas export`
(or `scripts/codegen.py`) after changing anything here.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from sed.ai.contract import BatchMeta, OutputItem, StrictModel

MISFILED_VALUES = ("none", "request", "change", "problem")


class TriageItem(OutputItem):
    """One label for one packet line (`ref`). Codes come from the effective taxonomy in in/context.md."""

    am_category: str = Field(min_length=1, max_length=40, description="Category code from the taxonomy")
    am_subcategory: str | None = Field(
        None, max_length=40, description="Subcategory code of that category, or null when none fits"
    )
    symptom_key: str = Field(
        min_length=1, max_length=60, description="Lowercase snake_case symptom slug; reuse a vocabulary key first"
    )
    misfiled_as: Literal["none", "request", "change", "problem"] = Field(
        description="Record type the ticket should have been filed as, or none"
    )
    confidence: float = Field(ge=0, le=1, description="Calibrated probability that the category is right")
    rationale: str = Field("", max_length=300, description="At most 25 words; no names or personal data")


class TriageBatchOutput(StrictModel):
    """The JSON file an agent writes to runs/<run_id>/out/<batch>.json."""

    meta: BatchMeta = Field(default_factory=BatchMeta)
    items: list[TriageItem]
