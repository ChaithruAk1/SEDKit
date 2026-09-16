"""Metric and fact definitions published by the sap module (Definitions sheet, deck notes, /api/meta)."""

from __future__ import annotations

SCOPE_TEXT = (
    "SAP tickets are incidents in the SAP scope (config/sap/scope.yaml): assigned to an SAP L3 group, or with an SAP "
    "ServiceNow category or custom field."
)

CHARM_TEXT = (
    "Change type, stage and area come from the exported transaction type, user status and component "
    "(config/sap/charm.yaml)."
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
    "sap.changes.open": (
        "count",
        f"Open SAP changes: ChaRM change documents (requests for change excluded) whose stage is not confirmed or "
        f"withdrawn. {CHARM_TEXT}",
    ),
    "sap.changes.urgent_ratio_8w": (
        "pct",
        "Urgent changes as a share of all changes (requests excluded) created in the last 8 complete weeks.",
    ),
    "sap.changes.stuck": (
        "count",
        "Open changes whose status has not changed for longer than their stage allows (config/sap/charm.yaml "
        "thresholds.stuck_days); the status date is the first export that showed it.",
    ),
    "sap.changes.without_jira": (
        "count",
        "Open normal, urgent and defect-correction changes without a Jira story: no Jira key in the change and no "
        "Jira issue of the SAP projects naming the change.",
    ),
    "sap.changes.prod_imports": (
        "count",
        "Imports of transports into production systems (role prod in config/sap/scope.yaml) in the week; the latest "
        "import per transport and system counts.",
    ),
    "sap.transports.failed_4w": (
        "count",
        "Transport imports (any system) whose latest return code is at or above the failure code "
        "(thresholds.failed_return_code), imported in the 28 days before the as-of date.",
    ),
    "sap.transports.waiting": (
        "count",
        "Transports of tested changes (ready for or in production) imported into QA without errors and not into "
        "production longer than thresholds.waiting_for_production_days after the QA import.",
    ),
    "sap.changes.incidents_after_import": (
        "count",
        "SAP incidents of the same landscape (and the change's area, when known) opened within "
        "thresholds.incident_window_hours after a production import. A correlation signal, not causation.",
    ),
}
