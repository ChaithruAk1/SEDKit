"""Metric and fact definitions published by the sap module (Definitions sheet, deck notes, /api/meta)."""

from __future__ import annotations

SCOPE_TEXT = (
    "SAP tickets are incidents in the SAP scope (config/sap/scope.yaml): assigned to an SAP L3 group, or with an SAP "
    "ServiceNow category or custom field."
)

DEFINITIONS: dict[str, tuple[str, str]] = {
    "sap.l3.opened": ("count", f"SAP incidents opened in the period. {SCOPE_TEXT}"),
    "sap.l3.resolved": ("count", "SAP incidents resolved in the period."),
    "sap.l3.backlog": (
        "count",
        "Open SAP incidents at the end of the period (or the as-of date); stale_open tickets are excluded.",
    ),
    "sap.l3.aged_30d": ("count", "Open SAP incidents opened more than 30 days before the backlog date."),
    "sap.l3.sla.pct": (
        "pct",
        "Share of SAP incidents resolved in the period that met their resolution SLA (same source order as the "
        "portfolio SLA: task_sla, made_sla, calendar-hour targets).",
    ),
    "sap.l3.mttr.median_h": (
        "hours",
        "Median calendar hours from opened to resolved, SAP incidents resolved in the period.",
    ),
    "sap.l3.p1p2.opened": ("count", "Priority 1 and 2 SAP incidents opened in the period."),
    "sap.l3.p1p2.open": ("count", "Open priority 1 and 2 SAP incidents at the backlog date."),
    "sap.l3.attention.count": (
        "count",
        "Open SAP incidents needing attention: P1/P2, at or past the SLA warning ratio, aged, reopened, ping-pong "
        "reassignments or unassigned.",
    ),
    "sap.findings.count": ("count", "Published system-detected SAP risks (rule findings of the sap module)."),
    "sap.area.net_growth": (
        "count",
        "Arrivals minus closures of an SAP area over the rule window (config/sap/risk_rules.yaml).",
    ),
    "sap.area.weeks_growing": (
        "count",
        "Complete weeks in the rule window in which an SAP area received more than it closed.",
    ),
}
