"""Output models of the `sed-assess-risks` skill: the only source of truth for its output_schema.json."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import Field

from sed.ai.contract import BatchMeta, StrictModel

RISK_KINDS = ("renewal_risk", "license_risk", "vendor_risk", "cost_risk", "rationalization")


class Signal(StrictModel):
    fact_key: str = Field(
        pattern=r"^[A-Za-z0-9_.:\-]{1,120}$", description="A key of a packet line's `facts`, verbatim"
    )


class RiskFinding(StrictModel):
    """One commercial or vendor risk with the evidence behind it and the decision it needs."""

    kind: Literal["renewal_risk", "license_risk", "vendor_risk", "cost_risk", "rationalization"]
    subject_type: Literal["contract", "license", "vendor", "app"]
    subject_id: str = Field(min_length=1, max_length=60, description="The subject_id shown on the packet line")
    severity: Literal["low", "medium", "high", "critical"]
    title: str = Field(min_length=3, max_length=120, description="The risk in plain words; no numbers or names")
    body_md: str = Field(
        min_length=1, max_length=1500, description="Markdown for the reviewer; numbers only as {{f:<fact_key>}} tokens"
    )
    recommendation: str = Field(min_length=1, max_length=400)
    decision_due: date | None = Field(None, description="The date a decision is needed, e.g. the notice deadline")
    signals: list[Signal] = Field(min_length=1, max_length=12)
    annotates_rule_stable_key: str | None = Field(
        None, max_length=200, description="stable_key of the rule finding this adds context to, or null"
    )
    confidence: float = Field(ge=0, le=1)


class RisksOutput(StrictModel):
    """The JSON file the agent writes to runs/<run_id>/out/batch_0001.json."""

    meta: BatchMeta = Field(default_factory=BatchMeta)
    findings: list[RiskFinding] = Field(default_factory=list, max_length=60)
