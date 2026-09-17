"""Metric and fact definitions published by the delivery module (Definitions sheet, deck notes, /api/meta)."""

from __future__ import annotations

DEFINITIONS: dict[str, tuple[str, str]] = {
    "delivery.projects.count": ("count", "Projects in the delivery project register (not deleted)."),
    "delivery.projects.red": (
        "count",
        "Projects whose computed health is red: a milestone 30+ days past baseline or overdue, an overdue high RAID "
        "item, or a forecast finish after the target go-live (config/delivery/risk_rules.yaml).",
    ),
    "delivery.projects.amber": (
        "count",
        "Projects whose computed health is amber: a milestone 14+ days past baseline, open high RAID items or scope "
        "growth above the threshold.",
    ),
    "delivery.projects.rag_mismatch": (
        "count",
        "Projects whose RAG reported in the project register differs from the computed health.",
    ),
    "delivery.milestones.completed": (
        "count",
        "Milestones of the latest plan versions with an actual finish inside the report period (to the as-of date).",
    ),
    "delivery.milestones.slipped": (
        "count",
        "Open milestones of the latest plan versions whose forecast finish is at least the slip threshold past the "
        "baseline finish.",
    ),
    "delivery.milestones.due_30d": (
        "count",
        "Open milestones with a forecast finish in the 30 days after the as-of date.",
    ),
    "delivery.raid.open_high": ("count", "Open RAID items of high or critical severity."),
    "delivery.raid.overdue": ("count", "Open RAID items past their due date."),
    "delivery.findings.count": ("count", "Published system-detected delivery risks (rule findings) at the as-of date."),
    "delivery.points.done_pct": (
        "pct",
        "Story points of resolved Jira stories as a share of all story points of the projects' Jira keys.",
    ),
    "delivery.velocity.points_per_week": (
        "number",
        "Story points of stories resolved in the velocity window (default 4 weeks) divided by its weeks, summed "
        "over projects.",
    ),
}
