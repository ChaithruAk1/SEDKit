"""Definitions of report-level facts that are not in sed.metrics.METRICS (monthly, quarterly and vendor reports).

Keys are the fact keys the builders emit; merged with METRICS and AI_DEFINITIONS by `ops/definitions.py`, which
rejects duplicates. "Period" below means the report window: the period clamped to the as-of date (data date), so an
open period is reported to date.
"""

from __future__ import annotations

REPORT_DEFINITIONS: dict[str, tuple[str, str]] = {
    # monthly
    "inc.sla.delta_pp_vs_prev_month": (
        "pp",
        "SLA met % of incidents resolved in the period minus the same measure for the full previous month.",
    ),
    "work.resolved.count": (
        "count",
        "Jira work items of every issue type resolved in the period (resolved timestamp within the period).",
    ),
    # quarterly
    "cost.actual.qtd": (
        "eur",
        "Actual cost lines (base currency) for the calendar months of the quarter that are complete by the as-of date.",
    ),
    "cost.budget.qtd": (
        "eur",
        "Budget lines of the latest budget version for the same months as cost.actual.qtd.",
    ),
    "cost.variance.qtd_pct": ("pct", "(cost.actual.qtd - cost.budget.qtd) / cost.budget.qtd."),
    "cost.actual.ytd": (
        "eur",
        "Actual cost lines (base currency) from the first month of the fiscal year to the last complete month of "
        "the period.",
    ),
    "cost.budget.ytd": ("eur", "Budget lines of the latest budget version for the same months as cost.actual.ytd."),
    "renewals.2q.count": (
        "count",
        "Active contracts (not non-renewing, terminated or expired) whose end date is in [as_of, as_of + 182 days).",
    ),
    "notice.2q.count": (
        "count",
        "Active contracts whose notice deadline (end_date - notice_period_days) is in [as_of, as_of + 182 days).",
    ),
    "apps.quiet.count": (
        "count",
        "Applications with no tickets in the months set by ops/risk_rules.yaml quiet_app.months_without_tickets "
        "and an annual license cost of at least quiet_app.min_annual_cost_base.",
    ),
    # vendor
    "vendor.name": ("text", "Vendor name from the vendor master."),
    "vendor.contracts.count": (
        "count",
        "Contracts with the vendor that are not terminated or expired and have not ended before the as-of date "
        "(signed contracts that start later included).",
    ),
    "vendor.contracts.annual_value": (
        "eur",
        "Sum of the annual value (base currency) of the contracts counted in vendor.contracts.count.",
    ),
    "vendor.spend.period": (
        "eur",
        "Actual cost lines booked to the vendor for the calendar months of the period that are complete by the "
        "as-of date.",
    ),
    "vendor.spend.prev_period": (
        "eur",
        "Actual cost lines booked to the vendor for the same number of months at the start of the previous period "
        "(like-for-like comparison with vendor.spend.period).",
    ),
    "vendor.sla.pct": (
        "pct",
        "Share of the vendor's incidents (ticket vendor, set from its assignment groups) resolved in the period "
        "that met their resolution SLA; same source order as inc.sla.pct.",
    ),
    "vendor.mttr.median_h": (
        "hours",
        "Median hours from opened_at to resolved_at for the vendor's incidents resolved in the period.",
    ),
    "vendor.reassign.avg": (
        "number",
        "Average reassignment_count of the vendor's incidents resolved in the period.",
    ),
    "vendor.incidents.count": ("count", "Incidents of the vendor opened in the period."),
    "vendor.license.idle_cost": (
        "eur",
        "Sum over the vendor's under-used licenses (utilization below the license_utilization low threshold) of "
        "max(entitled - active_90d, 0) x unit cost, from the latest usage snapshot on or before the as-of date.",
    ),
}
