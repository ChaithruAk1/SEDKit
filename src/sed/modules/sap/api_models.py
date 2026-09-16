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
