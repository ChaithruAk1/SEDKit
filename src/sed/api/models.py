"""Core API models (frozen for M2). Module models live in their module (e.g. sed.modules.ops.api_models)."""

from __future__ import annotations

from datetime import date
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
    score: float | None = Field(description="Suggestion similarity, 0-100 (rapidfuzz)")
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


# -- review (M4) ---------------------------------------------------------------------------------------------------


class ReviewItem(ApiModel):
    finding_id: str
    origin: Literal["rule", "ai"]
    run_id: str | None
    kind: str
    status: str
    severity: str | None
    confidence: float | None
    title: str
    subject_type: str | None
    subject_id: str | None
    body_md: str | None
    pending_body_md: str | None
    carried_forward_from: str | None
    evidence: list[Evidence]
    ticket_count: int | None
    periodicity: str | None
    suspected_change: str | None
    recommendation: str | None
    decision_due: str | None
    material_change: list[str]


class ReviewQueueOut(ApiModel):
    items: list[ReviewItem]
    counts: dict[str, int]


class FindingReviewIn(ApiModel):
    action: Literal["approve", "reject", "edit", "approve_update", "acknowledge", "suppress_until"]
    note: str | None = Field(None, max_length=1000)
    body_md: str | None = Field(None, max_length=5000)
    until: date | None = None


class BulkReviewIn(ApiModel):
    finding_ids: list[str] = Field(min_length=1, max_length=200)
    action: Literal["approve", "reject", "approve_update", "acknowledge"]
    note: str | None = Field(None, max_length=1000)


class ReviewResult(ApiModel):
    finding_id: str
    action: str
    status: str


class FindingReviewOut(ApiModel):
    action: str
    reviewed_by: str
    results: list[ReviewResult]


class SampleLabel(ApiModel):
    am_category: str | None = None
    am_subcategory: str | None = None
    symptom_key: str | None = None
    misfiled_as: str | None = None
    confidence: float | None = None
    rationale: str | None = None


class SampleTicket(ApiModel):
    number: str | None = None
    kind: str | None = None
    priority: int | None = None
    app: str | None = None
    sn_category: str | None = None
    short_description: str | None = None


class Correction(ApiModel):
    category: str | None = Field(None, max_length=40)
    subcategory: str | None = Field(None, max_length=40)


class SampleCard(ApiModel):
    key: str
    item_id: str
    stage: str
    sample_kind: Literal["random", "lowest_conf"]
    stratum: str
    weight: float
    verdict: Literal["correct", "incorrect"] | None
    correction: Correction | None
    label: SampleLabel
    ticket: SampleTicket


class SubcategoryOption(ApiModel):
    code: str
    only: str | None = Field(description="Extension key whose tickets alone may take this subcategory (null = any)")


class CategoryOption(ApiModel):
    code: str
    description: str
    subcategories: list[SubcategoryOption]


class RunDetailOut(ApiModel):
    run: RunRow
    skill_hash: str | None
    model_reported: str | None
    input_run_ids: list[str]
    random: list[SampleCard]
    lowest_confidence: list[SampleCard]
    matrix: list[dict[str, Any]]
    misfiled: dict[str, int]
    findings: dict[str, int]
    eval_passed: bool | None
    eval_checks: dict[str, bool]
    categories: list[CategoryOption]
    misfiled_as: list[str]


class VerdictsIn(ApiModel):
    verdicts: dict[str, Literal["correct", "incorrect"] | None]
    corrections: dict[str, Correction] = Field(default_factory=dict)


class VerdictsOut(ApiModel):
    run_id: str
    recorded: int
    skipped: int
    incorrect: int
    random_missing: int
    lowest_conf_missing: int


class RunReviewIn(ApiModel):
    action: Literal["approve", "reject"]
    note: str | None = Field(None, max_length=1000)


class RunReviewOut(ApiModel):
    run_id: str
    status: str
    reviewed_by: str
    sample_accuracy: float | None = None
    sample_ci_low: float | None = None
    sample_ci_high: float | None = None
    corrections_applied: int = 0
    findings_rejected: int = 0
    dependent_findings_stale: int = 0


class LabelCorrectionIn(ApiModel):
    ticket_id: str = Field(min_length=3, max_length=80)
    stage: Literal["open", "resolved"]
    category: str = Field(min_length=1, max_length=40)
    subcategory: str | None = Field(None, max_length=40)
    symptom_key: str | None = Field(None, max_length=60)
    misfiled_as: Literal["none", "request", "change", "problem"] | None = None
    skill: str = Field("sed-triage-batch", max_length=60)


class LabelCorrectionOut(ApiModel):
    ticket_id: str
    stage: str
    run_id: str


# -- reports (M5) --------------------------------------------------------------------------------------------------


class ReportInfo(ApiModel):
    key: str
    module: str
    title: str
    period_kinds: list[str]
    needs_vendor: bool
    formats: list[str]
    sections: list[str]


class ArtifactRow(ApiModel):
    artifact_id: str
    report: str
    period: str
    vendor_id: str | None
    snapshot_id: str
    format: str
    ai_mode: str
    built_at: str
    file_name: str
    sha256: str
    ai_run_ids: list[str]
    omitted: list[str]


class ReportsOut(ApiModel):
    reports: list[ReportInfo]
    artifacts: list[ArtifactRow]


class ReadinessSection(ApiModel):
    key: str
    title: str
    required: bool
    approved_status: str
    draft_status: str
    has_newer_draft: bool
    finding_id: str | None
    reasons: list[str]


class ReadinessOut(ApiModel):
    report: str
    period: str
    vendor_id: str | None
    snapshot_id: str | None
    sections_required: int
    sections_approved: int
    sections_with_drafts: int
    cited_findings_unapproved: list[str]
    complete: bool
    sections: list[ReadinessSection]


class BuildIn(ApiModel):
    report: str = Field(min_length=1, max_length=40)
    period: str = Field(min_length=4, max_length=12)
    vendor: str | None = Field(None, max_length=60)
    formats: list[Literal["xlsx", "md", "pptx"]] | None = Field(None, max_length=3)
    ai_mode: Literal["approved", "none", "draft"] = "approved"
    require_complete: bool = False


class JobOut(ApiModel):
    job_id: str
    kind: str
    status: Literal["queued", "running", "done", "failed"]
    created_at: str
    finished_at: str | None
    params: dict[str, Any]
    result: dict[str, Any] | None
    error: dict[str, Any] | None


# -- sources: API pull or file (W8) ---------------------------------------------------------------------------------


class PullIn(ApiModel):
    source: str | None = Field(None, pattern=r"^[a-z][a-z0-9_]{1,40}$")
    full: bool = False


class ConnectorSourceRow(ApiModel):
    source: str
    kind: Literal["table", "query", "list", "library", "space", "odata"]
    mappings: list[str]
    watermark: str | None
    last_pull_at: str | None
    last_rows: int | None
    last_file: str | None
    credential: str | None
    credential_found_in: str | None
    warning: str | None


class SourceConnectorRow(ApiModel):
    connector: str
    enabled: bool
    credential: str
    credential_found_in: str | None
    can_pull: bool
    reason: str | None
    sources: list[ConnectorSourceRow]


class FileSourceRow(ApiModel):
    mapping: str
    module: str | None
    target: str
    load_mode: str
    format: str
    patterns: list[str]
    connector_sources: list[str]
    last_file: str | None
    last_status: str | None
    last_imported_at: str | None


class UploadInfo(ApiModel):
    suffixes: list[str]
    max_bytes: int
    needs_confirmation: bool


class SourcesOut(ApiModel):
    profile: str
    data_class: str
    upload: UploadInfo
    connectors: list[SourceConnectorRow]
    files: list[FileSourceRow]


# -- review rates (M6) ---------------------------------------------------------------------------------------------


class ReviewRateRow(ApiModel):
    skill: str
    skill_hash: str
    month: str
    runs: int
    approved_runs: int
    rejected_runs: int
    sample_accuracy: float | None
    findings_drafted: int
    findings_approved: int
    findings_edited: int
    findings_rejected: int
    findings_open: int
    approval_rate: float | None
    edit_rate: float | None
    reject_rate: float | None
    first_run: str
    last_run: str


class ReviewRatesOut(ApiModel):
    rows: list[ReviewRateRow]
