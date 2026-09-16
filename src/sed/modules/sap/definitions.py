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
    "sap.idocs.errors_open": (
        "count",
        "IDocs whose latest status (by the as-of date) is in an error group of config/sap/idoc.yaml, among IDocs "
        "created within thresholds.history_days.",
    ),
    "sap.idocs.errors_aged": (
        "count",
        "Open IDoc errors whose first error status is older than thresholds.aged_error_hours (config/sap/idoc.yaml).",
    ),
    "sap.idocs.new_persistent": (
        "count",
        "IDocs whose first error falls in the week and that were not processed within "
        "thresholds.reprocess_grace_hours (reprocessed later, closed, or still open); compared with the 4-week "
        "average.",
    ),
    "sap.idocs.reprocess_median_h": (
        "hours",
        "Median hours from an IDoc's first error to its next processed status, for IDocs reprocessed in the week.",
    ),
    "sap.idocs.reprocessed_in_grace_pct": (
        "pct",
        "Share of the IDocs reprocessed in the week that were processed within thresholds.reprocess_grace_hours.",
    ),
    "sap.idocs.spike_after_import": (
        "count",
        "Persistent IDoc errors on a system within thresholds.spike_window_hours after a production import into it, "
        "against the same window before (lift). A correlation signal, not causation.",
    ),
    "sap.ai.labelled": (
        "count",
        "SAP incidents and problems opened in the period that carry an approved AI triage label for their current "
        "content (sed-triage-batch run with SAP subcategories, config/sap/taxonomy.yaml). AI-assisted.",
    ),
    "sap.ai.labelled_pct": ("pct", "Share of the SAP incidents and problems opened in the period with an AI label."),
    "sap.ai.sample_accuracy_pct": (
        "pct",
        "Human-reviewed accuracy on the random stratified review sample of the approved triage run behind most of "
        "these labels, weighted by stratum size.",
    ),
    "sap.ai.sample_ci_low_pct": ("pct", "Lower bound of the Wilson 95% interval around the AI sample accuracy."),
    "sap.ai.sample_ci_high_pct": ("pct", "Upper bound of the Wilson 95% interval around the AI sample accuracy."),
    "sap.changes.incidents_after_import": (
        "count",
        "SAP incidents of the same landscape (and the change's area, when known) opened within "
        "thresholds.incident_window_hours after a production import, against the same window before it (lift); "
        "imports with a lift of at least thresholds.incident_min_lift are listed. A correlation signal, not causation.",
    ),
}
