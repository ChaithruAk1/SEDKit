"""AI layer contract (frozen for M2): run parameters, packet/plan shapes and the SkillHandler protocol.

Every path in agent-facing payloads (RunPlan, BatchInput) is absolute with forward slashes, e.g.
`C:/Users/me/AppData/Local/sed/synthetic/runs/<run_id>/out/batch_0001.json`, because agents run Git Bash where
backslashes are escape characters. Agents always double-quote these paths.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, ClassVar, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

InvokedVia = Literal["interactive", "workflow", "headless", "manual"]
REF_PATTERN = r"^T\d{3,5}$"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BatchMeta(StrictModel):
    model: str | None = Field(None, max_length=100)


class OutputItem(StrictModel):
    ref: str = Field(pattern=REF_PATTERN)


class StartParams(StrictModel):
    # new | since:YYYY-MM-DD | period:<label>. new = event_at >= local midnight of (data_as_of - backfill_days)
    scope: str = "new"
    batch_size: int | None = Field(None, ge=1, le=500)
    max_chars: int | None = Field(None, ge=1000)
    max_items: int | None = Field(None, ge=1)
    limit: int | None = Field(None, ge=1)
    # Restrict the run to one handler-defined subset, e.g. the tickets of a triage extension (`sap`).
    only: str | None = Field(None, pattern=r"^[a-z][a-z0-9]{1,15}$")
    # Report skills (sed-draft-report): the report key and, for vendor reports, the vendor id.
    report: str | None = Field(None, pattern=r"^[a-z][a-z0-9-]{1,39}$")
    vendor: str | None = Field(None, pattern=r"^[A-Za-z0-9_.:-]{1,60}$")
    # Subject skills (e.g. the delivery drafts): the entity the run is about, such as a project id.
    subject: str | None = Field(None, pattern=r"^[A-Za-z0-9_.:-]{1,60}$")
    resume: str | None = None
    invoked_via: InvokedVia = "interactive"
    model_arg: str | None = None
    claude_version: str | None = None
    dry_run: bool = False


class PacketLimits(StrictModel):
    max_items: int
    max_chars: int
    max_line_chars: int = 1800


@dataclass
class RunContext:
    conn: sqlite3.Connection
    paths: Any  # sed.paths.Paths
    settings: Any  # sed.settings.Settings
    run_id: str | None
    skill: str
    params: StartParams
    data_as_of: date | None
    run_dir: Path | None
    in_dir: Path | None
    out_dir: Path | None


@dataclass(frozen=True)
class WorkItem:
    item_id: str
    stage: str
    input_hash: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class IngestError:
    loc: str
    msg: str
    ref: str | None = None


@dataclass(frozen=True)
class SampleCandidate:
    item_id: str
    stage: str
    stratum: str
    confidence: float | None


class IngestResult(StrictModel):
    items: int
    low_confidence: int = 0
    warnings: list[str] = Field(default_factory=list)


class BatchInput(StrictModel):
    batch: str
    packet: str
    aux: list[str]
    out: str
    items: int
    chars: int


class RunPlan(StrictModel):
    run_id: str | None
    skill: str
    status: Literal["running", "planned"]
    dry_run: bool
    plan: dict[str, int]
    run_dir: str | None
    out_dir: str | None
    context: list[str]
    inputs: list[BatchInput]


class BatchResult(StrictModel):
    batch: str
    status: Literal["ingested", "failed"]
    ingested: int
    errors_count: int
    low_conf: int


class FinishSummary(StrictModel):
    run_id: str
    status: str
    counts: dict[str, int]
    failed_batches: list[str]
    review: dict[str, Any]


@runtime_checkable
class SkillHandler(Protocol):
    """Module-side behaviour of one skill. The core owns runs, batches, refs, files and transactions."""

    skill: ClassVar[str]
    schema_version: ClassVar[int]
    output_model: ClassVar[type[BaseModel]]

    def config_inputs(self, paths: Any) -> dict[str, Any]:
        """Effective config that shapes the output (hashed into skill_hash)."""
        ...

    def select(self, ctx: RunContext) -> list[WorkItem]:
        """Eligible items in priority order. NO WRITES (runs inside write_tx, or on a query_only conn for dry runs)."""
        ...

    def claim(self, ctx: RunContext, items: list[WorkItem]) -> None:
        """Claim exactly the trimmed items, inside the same write_tx as select."""
        ...

    def context_files(self, ctx: RunContext, items: list[WorkItem]) -> dict[str, str]:
        """Run-level files for in/ (name -> text), e.g. context.md."""
        ...

    def batch_files(self, ctx: RunContext, batch: str, items: list[WorkItem]) -> dict[str, str]:
        """Per-batch auxiliary files for in/ (name -> text), e.g. vocab_batch_0001.txt."""
        ...

    def validate(self, ctx: RunContext, output: BaseModel, refs: dict[str, WorkItem]) -> list[IngestError]:
        """Semantic checks beyond the schema (taxonomy codes, facts). Any error rejects the whole batch."""
        ...

    def write(self, ctx: RunContext, batch_id: str, output: BaseModel, refs: dict[str, WorkItem]) -> IngestResult:
        """Persist the batch inside write_tx; idempotent."""
        ...

    def release(self, ctx: RunContext) -> int:
        """Release claims at finish-run, inside write_tx. Returns the number released."""
        ...

    def sample_candidates(self, conn: sqlite3.Connection, run_id: str) -> list[SampleCandidate]:
        """Every reviewable item of a run with its stratum and confidence."""
        ...

    def review_card(self, conn: sqlite3.Connection, run_id: str, keys: list[tuple[str, str]]) -> dict[str, Any]:
        """Display data for sampled (item_id, stage) keys."""
        ...
