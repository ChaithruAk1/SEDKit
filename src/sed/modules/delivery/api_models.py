"""Delivery API models (/api/delivery/...)."""

from __future__ import annotations

from sed.api.models import ApiModel, FindingOut


class DeliveryTask(ApiModel):
    task_id: str
    name: str | None
    is_milestone: bool
    baseline_finish: str | None
    finish: str | None
    actual_finish: str | None
    percent_complete: float | None
    slip_days: int | None
    replans: int
    overdue: bool


class DeliveryRaid(ApiModel):
    raid_id: str
    project_id: str
    raid_type: str | None
    title: str | None
    severity: str | None
    status: str | None
    open: bool
    raised_on: str | None
    due_date: str | None
    closed_on: str | None
    days_overdue: int


class DeliveryWeek(ApiModel):
    week_ending: str
    scope_points: float
    done_points: float


class DeliveryProgress(ApiModel):
    stories: int
    points_total: float
    points_done: float
    points_added_window: float
    scope_growth_pct: float | None
    velocity_per_week: float
    forecast_finish: str | None
    weekly: list[DeliveryWeek]


class DeliveryDoc(ApiModel):
    page_id: str
    title: str
    kind: str
    last_updated: str | None


class DeliveryDocuments(ApiModel):
    requirements: int
    adrs: int
    pages: int
    last_updated: str | None
    items: list[DeliveryDoc]


class DeliveryProjectRow(ApiModel):
    project_id: str
    name: str
    app_id: str | None
    app_raw: str | None
    phase: str | None
    reported_rag: str | None
    computed_rag: str
    reasons: list[str]
    target_date: str | None
    start_date: str | None
    jira_keys: list[str]
    confluence_space: str | None
    budget_base: float | None
    plan_status_date: str | None
    next_milestone: DeliveryTask | None
    worst_slip_days: int
    open_high_raid: int
    overdue_raid: int
    points_done_pct: float | None
    forecast_finish: str | None


class DeliveryPortfolioOut(ApiModel):
    as_of: str
    projects: list[DeliveryProjectRow]
    counts: dict[str, int]
    findings: list[FindingOut]


class DeliveryProjectOut(ApiModel):
    as_of: str
    project: DeliveryProjectRow
    tasks: list[DeliveryTask]
    plan_versions: int
    raid: list[DeliveryRaid]
    progress: DeliveryProgress
    documents: DeliveryDocuments
    findings: list[FindingOut]
