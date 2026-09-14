"""Core API models (frozen for M2). Module models live in their module (e.g. sed.modules.ops.api_models)."""

from __future__ import annotations

from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ErrorBody(ApiModel):
    kind: str
    message: str
    details: Any | None = None


class ErrorEnvelope(ApiModel):
    ok: Literal[False] = False
    error: ErrorBody


class Page(ApiModel, Generic[T]):
    page: int
    page_size: int
    total: int
    items: list[T]


class Kpi(ApiModel):
    key: str
    label: str
    value: float | int | str | None
    unit: str
    compare: float | None = None
    delta: float | None = None
    definition: str | None = None


class Evidence(ApiModel):
    fact_key: str
    value: Any = None


class FindingOut(ApiModel):
    finding_id: str
    origin: Literal["rule", "ai"]
    kind: str
    severity: str | None
    title: str
    subject_type: str | None
    subject_id: str | None
    status: str
    body_md: str | None
    evidence: list[Evidence]
    system_detected: bool
    run_id: str | None
    reviewed_by: str | None
    reviewed_at: str | None


class FreshnessRow(ApiModel):
    mapping_name: str
    last_import: str | None
    files: int | None
    latest_as_of: str | None


class FilterOption(ApiModel):
    value: str
    label: str


class HealthOut(ApiModel):
    ok: bool
    version: str


class PeriodsOut(ApiModel):
    weeks: list[str]
    months: list[str]
    quarters: list[str]


class DefinitionOut(ApiModel):
    unit: str
    text: str


class ModuleRef(ApiModel):
    key: str
    title: str


class MetaOut(ApiModel):
    data_class: str
    profile: str
    pii_mode: str
    schema_version: int
    sed_version: str
    reporting_tz: str
    base_currency: str
    as_of_default: str | None
    periods: PeriodsOut
    entities: dict[str, list[FilterOption]]
    freshness: list[FreshnessRow]
    definitions: dict[str, DefinitionOut]
    modules: list[ModuleRef]


class NavItemOut(ApiModel):
    id: str
    module: str
    label: str
    path: str
    order: int
    icon: str | None


class NavOut(ApiModel):
    items: list[NavItemOut]


class ModuleOut(ApiModel):
    key: str
    title: str
    description: str
    version: str
    enabled: bool
    reports: list[str]
    skills: list[str]


class ModulesOut(ApiModel):
    modules: list[ModuleOut]


class FindingsOut(ApiModel):
    items: list[FindingOut]


class ImportRow(ApiModel):
    batch_id: int
    file_name: str
    mapping_name: str
    load_mode: str
    status: str
    as_of: str | None
    rows_read: int
    rows_inserted: int
    rows_updated: int
    rows_unchanged: int
    rows_rejected: int
    rows_soft_deleted: int
    dq_severity: str | None
    dq: dict[str, Any]
    imported_at: str


class ImportsOut(ApiModel):
    items: list[ImportRow]


class UnmappedRow(ApiModel):
    kind: str
    raw_value: str
    occurrences: int
    suggestion: str | None
    score: float | None
    first_batch_id: int | None
    last_batch_id: int | None


class UnmappedList(ApiModel):
    items: list[UnmappedRow]


class AliasTarget(ApiModel):
    id: str
    name: str


class AliasTargetsOut(ApiModel):
    items: list[AliasTarget]


class AliasIn(ApiModel):
    kind: str
    raw_value: str = Field(min_length=1, max_length=300)
    target: str = Field(min_length=1, max_length=200)


class AliasOut(ApiModel):
    kind: str
    raw_value: str
    target_id: str
    reresolved: dict[str, int]


class RunRow(ApiModel):
    run_id: str
    skill: str
    status: str
    invoked_via: str
    started_at: str
    finished_at: str | None
    counts: dict[str, int]
    sample_accuracy: float | None
    sample_ci_low: float | None
    sample_ci_high: float | None
    sample_n: int | None
    reviewed_by: str | None
    reviewed_at: str | None


class RunsOut(ApiModel):
    items: list[RunRow]
