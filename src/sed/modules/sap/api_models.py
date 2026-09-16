"""Response models of the SAP API (/api/sap/...)."""

from __future__ import annotations

from sed.api.models import ApiModel, FindingOut, Kpi


class SapOption(ApiModel):
    value: str
    label: str


class SapAreaRow(ApiModel):
    area: str
    label: str
    open: int
    aged_30d: int
    opened: int
    resolved: int
    sla_pct: float | None


class SapLandscapeRow(ApiModel):
    landscape: str
    label: str
    open: int


class SapOverview(ApiModel):
    as_of: str
    period: str
    data_as_of_last_import: str | None
    configured: bool
    kpis: list[Kpi]
    areas: list[SapAreaRow]
    landscapes: list[SapLandscapeRow]
    findings: list[FindingOut]


class SapTrendRow(ApiModel):
    period: str
    opened: int
    resolved: int
    net: int
    sla_pct: float | None


class SapAging(ApiModel):
    d0_7: int
    d8_30: int
    d31_90: int
    d90p: int


class SapBacklogAreaRow(ApiModel):
    area: str
    label: str
    total: int
    d0_7: int
    d8_30: int
    d31_90: int
    d90p: int


class SapFlowRow(ApiModel):
    period: str
    area: str
    label: str
    arrived: int
    closed: int
    net: int


class SapSlaPriorityRow(ApiModel):
    priority: str
    total: int
    met: int
    pct: float | None


class SapAttentionRow(ApiModel):
    ticket_id: str  # opens the ops ticket detail (/api/ops/tickets/{ticket_id}, page #/ops/tickets?ticket=...)
    number: str
    priority: int | None
    area: str
    area_label: str
    app: str | None
    state: str | None
    assignment_group: str | None
    opened_at: str | None
    age_days: float
    reasons: str
    short_description: str | None


class SapL3Out(ApiModel):
    as_of: str
    at: str
    sla_source: str
    area: str | None
    landscape: str | None
    areas: list[SapOption]
    landscapes: list[SapOption]
    backlog_total: int
    aging: SapAging
    by_area: list[SapBacklogAreaRow]
    by_landscape: list[SapLandscapeRow]
    trend: list[SapTrendRow]
    flow: list[SapFlowRow]
    sla_by_priority: list[SapSlaPriorityRow]
    attention_count: int
    attention: list[SapAttentionRow]


class SapStageRow(ApiModel):
    stage: str
    label: str
    normal: int
    urgent: int
    standard: int
    defect_correction: int
    general: int
    other: int
    total: int


class SapUrgentAreaRow(ApiModel):
    area: str
    label: str
    created: int
    urgent: int
    ratio_pct: float | None
    previous_created: int
    previous_urgent: int
    previous_ratio_pct: float | None
    delta_pp: float | None


class SapImportWeekRow(ApiModel):
    period: str
    imports: int
    failed: int
    changes: int


class SapChangeRow(ApiModel):
    change_id: str
    title: str | None
    change_type: str
    type_label: str
    area: str
    area_label: str
    landscape: str
    stage: str
    stage_label: str
    created_at: str | None


class SapStuckChangeRow(SapChangeRow):
    status: str | None
    days_in_status: float
    threshold_days: int
    jira_keys: list[str]


class SapWaitingTransportRow(ApiModel):
    transport: str
    change_id: str | None
    title: str | None
    landscape: str
    qa_system: str
    qa_imported_at: str
    days_waiting: float


class SapFailedImportRow(ApiModel):
    transport: str
    system_id: str
    role: str | None
    landscape: str
    return_code: int | None
    imported_at: str
    change_id: str | None
    title: str | None
    change_type: str | None


class SapImportIncidentsRow(ApiModel):
    change_id: str | None
    transports: int
    title: str | None
    change_type: str | None
    area: str
    area_label: str
    landscape: str
    system_id: str
    imported_at: str
    return_code: int | None
    incidents: int
    incidents_before: int
    lift: int
    numbers: list[str]


class SapChangesOut(ApiModel):
    as_of: str
    at: str
    period: str  # the last full week: production imports and urgent-ratio windows end with it
    area: str | None
    landscape: str | None
    areas: list[SapOption]
    landscapes: list[SapOption]
    kpis: list[Kpi]
    stages: list[SapStageRow]
    urgent_by_area: list[SapUrgentAreaRow]
    production_imports: list[SapImportWeekRow]
    stuck: list[SapStuckChangeRow]
    waiting: list[SapWaitingTransportRow]
    failed: list[SapFailedImportRow]
    incidents_after_imports: list[SapImportIncidentsRow]
    without_jira_count: int
    without_jira: list[SapChangeRow]


class SapIdocAging(ApiModel):
    lt4h: int
    h4_24: int
    d1_2: int
    d2_7: int
    gt7d: int


class SapIdocTypeRow(ApiModel):
    system_id: str
    landscape: str
    message_type: str | None
    direction: str | None
    area: str
    errors: int
    aged: int
    partners: int
    oldest_hours: float


class SapIdocPartnerRow(ApiModel):
    system_id: str
    message_type: str | None
    partner: str | None
    errors: int
    oldest_hours: float


class SapIdocWeekRow(ApiModel):
    period: str
    idocs: int
    new_errors: int
    persistent: int
    reprocessed: int
    reprocess_median_h: float | None


class SapIdocTextRow(ApiModel):
    text: str
    errors: int
    message_types: list[str]


class SapIdocSpikeRow(ApiModel):
    change_id: str | None
    title: str | None
    change_type: str | None
    system_id: str
    imported_at: str
    return_code: int | None
    errors: int
    errors_before: int
    lift: int


class SapIdocErrorRow(ApiModel):
    system_id: str
    docnum: str
    direction: str | None
    message_type: str | None
    partner: str | None
    status_code: str
    text: str | None
    first_error_at: str
    age_hours: float


class SapIdocsOut(ApiModel):
    as_of: str
    at: str
    period: str  # the last full week: weekly figures end with it
    system: str | None
    landscape: str | None
    area: str | None
    direction: str | None
    systems: list[SapOption]
    areas: list[SapOption]
    landscapes: list[SapOption]
    kpis: list[Kpi]
    aging: SapIdocAging
    by_type: list[SapIdocTypeRow]
    partners: list[SapIdocPartnerRow]
    weekly: list[SapIdocWeekRow]
    top_texts: list[SapIdocTextRow]
    spikes: list[SapIdocSpikeRow]
    open_errors_count: int
    open_errors: list[SapIdocErrorRow]
