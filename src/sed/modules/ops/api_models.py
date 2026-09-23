"""Ops API response models (frozen for M2; see the HTTP API contract)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from sed.api.models import ApiModel, FindingOut, FreshnessRow, Kpi

Granularity = Literal["week", "month"]


class OpsAppOption(ApiModel):
    app_id: str
    name: str
    family: str | None


class OpsFiltersOut(ApiModel):
    families: list[str]
    apps: list[OpsAppOption]


class OpsOverview(ApiModel):
    period: str
    as_of: str
    data_as_of_last_import: str | None
    kpis: list[Kpi]
    attention_count: int
    stale_open: int
    top_risks: list[FindingOut]
    review_queue_count: int
    freshness: list[FreshnessRow]


class AttentionRow(ApiModel):
    ticket_id: str
    number: str
    priority: int | None
    app: str | None
    state: str | None
    assignment_group: str | None
    assigned_to_pid: str | None
    opened_at: str | None
    age_days: float | None
    reasons: list[str]
    short_description: str | None


class AttentionOut(ApiModel):
    as_of: str
    data_as_of_last_import: str | None
    count: int
    by_reason: dict[str, int]
    items: list[AttentionRow]


class TicketRow(ApiModel):
    ticket_id: str
    number: str
    kind: str
    priority: int | None
    state: str | None
    is_open: bool
    stale_open: bool
    app_id: str | None
    app_name: str | None
    vendor_name: str | None
    assignment_group: str | None
    opened_at: str | None
    resolved_at: str | None
    short_description: str | None
    sn_category: str | None
    am_category: str | None
    am_subcategory: str | None
    label_confidence: float | None
    label_run_id: str | None
    label_run_status: str | None = None
    # The export's own columns for this row, kept by the mapping's `raw_keep`. Text as it came, never parsed, and
    # never a person's name or free text: a mapping may not keep those without a field carrying a PII rule.
    export_fields: dict[str, str] = Field(default_factory=dict)


class TicketPage(ApiModel):
    page: int
    page_size: int
    total: int
    items: list[TicketRow]
    # Every export column this store holds, so the table can offer them all rather than only those on this page.
    export_columns: list[str] = Field(default_factory=list)


class LabelOut(ApiModel):
    stage: str
    run_id: str
    run_status: str | None
    am_category: str
    am_subcategory: str | None
    symptom_key: str | None
    misfiled_as: str | None
    confidence: float | None
    rationale: str | None


class TicketDetail(TicketRow):
    description: str | None
    close_code: str | None
    close_notes: str | None
    closed_at: str | None
    sys_updated_on: str | None
    caller_pid: str | None
    assigned_to_pid: str | None
    cmdb_ci_raw: str | None
    business_service_raw: str | None
    problem_id: str | None
    caused_by: str | None
    parent_incident: str | None
    reassignment_count: int | None
    reopen_count: int | None
    made_sla: bool | None
    # Columns the export carried that SED has no field of its own for, kept by the mapping's `raw_keep`. Text as it
    # came, never parsed, and never a person's name or free text: those need a field with a PII rule.
    export_fields: dict[str, str]
    labels: list[LabelOut]


class VolumeRow(ApiModel):
    period: str
    opened: int
    resolved: int
    net: int


class VolumesOut(ApiModel):
    granularity: Granularity
    items: list[VolumeRow]


class SlaRow(ApiModel):
    period: str
    pct: float | None
    met: int
    total: int


class SlaPriorityRow(ApiModel):
    priority: str
    pct: float | None
    met: int
    total: int


class SlaOut(ApiModel):
    granularity: Granularity
    sla_source: str
    items: list[SlaRow]
    by_priority: list[SlaPriorityRow]


class MttrRow(ApiModel):
    period: str
    count: int
    median_h: float | None
    mean_h: float | None
    p90_h: float | None


class MttrOut(ApiModel):
    granularity: Granularity
    items: list[MttrRow]


class AgingOut(ApiModel):
    d0_7: int
    d8_30: int
    d31_90: int
    d90p: int


class BacklogGroupRow(ApiModel):
    group: str | None
    total: int
    d0_7: int
    d8_30: int
    d31_90: int
    d90p: int


class FlowRow(ApiModel):
    period: str
    group: str | None
    arrived: int
    closed: int


class BacklogOut(ApiModel):
    at: str
    total: int
    stale_excluded: int
    aging: AgingOut
    by_group: list[BacklogGroupRow]
    flow: list[FlowRow]


class AppRow(ApiModel):
    app_id: str
    name: str
    family: str | None
    criticality: str | None
    lifecycle: str | None
    primary_vendor: str | None
    annual_license_cost_base: float | None
    cost_ytd_base: float | None
    incidents_per_month_3m: float | None
    sla_pct_3m: float | None
    license_utilization: float | None
    open_risks: int


class AppsOut(ApiModel):
    items: list[AppRow]


class ChangeRow(ApiModel):
    number: str
    change_type: str | None
    close_code: str | None
    start_date: str | None
    closed_at: str | None
    short_description: str | None


class CostRow(ApiModel):
    key: str
    label: str
    actual: float | None
    budget: float | None
    variance_pct: float | None


class RenewalRow(ApiModel):
    contract_id: str
    contract_number: str | None
    vendor: str | None
    app: str | None
    product: str | None
    end_date: str | None
    days_to_end: int | None
    notice_deadline: str | None
    days_to_notice: int | None
    auto_renew: bool | None
    renewal_status: str | None
    annual_value_base: float | None


class LicenseRow(ApiModel):
    license_id: str
    app: str | None
    vendor: str | None
    product: str | None
    entitled_qty: float | None
    assigned_qty: float | None
    active_qty_90d: float | None
    utilization: float | None
    assigned_ratio: float | None
    unit_cost_base: float | None
    idle_cost_base: float | None


class JiraRow(ApiModel):
    issue_key: str
    issue_type: str | None
    status: str | None
    priority: str | None
    created: str | None
    resolved: str | None
    summary: str | None


class DocRow(ApiModel):
    page_id: str
    space_key: str | None
    title: str
    page_type: str | None
    last_updated: str | None


class App360Out(ApiModel):
    app: AppRow
    kpis: list[Kpi]
    volumes: list[VolumeRow]
    open_tickets: list[TicketRow]
    changes: list[ChangeRow]
    cost: list[CostRow]
    contracts: list[RenewalRow]
    licenses: list[LicenseRow]
    jira: list[JiraRow]
    docs: list[DocRow]
    findings: list[FindingOut]


class CostsOut(ApiModel):
    group_by: Literal["app", "vendor", "category", "app_category"]
    months: list[str]
    rows: list[CostRow]
    total_actual: float | None
    total_budget: float | None


class RenewalsOut(ApiModel):
    as_of: str
    days: int
    items: list[RenewalRow]


class LicensesOut(ApiModel):
    as_of: str
    idle_cost_total: float
    items: list[LicenseRow]


class VendorPoint(ApiModel):
    period: str
    sla_pct: float | None
    mttr_median_h: float | None
    reassign_avg: float | None
    tickets: int


class VendorTrendRow(ApiModel):
    vendor_id: str
    vendor: str
    delta_pp: float | None
    series: list[VendorPoint]


class VendorTrendsOut(ApiModel):
    months: int
    items: list[VendorTrendRow]
