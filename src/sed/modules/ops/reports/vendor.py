"""Vendor Review snapshot builder (vendor and contract review, plan A10). Requires `--vendor`.

Contract terms; spend trend; license position; SLA/MTTR/reassignment trend on the vendor's incidents (ticket vendor,
set from its assignment groups); incidents by application; renewal and notice timeline; risks.

Period aggregates (spend, SLA, MTTR, reassignments, incidents) use `req.window`; contract terms, the renewal timeline,
license utilization, the 6-month SLA trend and rule findings use `req.as_of`.
"""

from __future__ import annotations

from typing import Any

from sed import analytics, metrics
from sed.errors import ValidationFailed
from sed.modules.ops.reports import queries
from sed.reports.snapshot import SnapshotParts, SnapshotRequest, fact, table

SPEND_TREND_MONTHS = 12
SLA_TREND_MONTHS = 6
RENEWAL_TIMELINE_DAYS = 365


def _incidents_by_app(conn: Any, vendor_id: str, req: SnapshotRequest, src: str) -> list[dict[str, Any]]:
    rows = []
    for r in metrics.top_apps(conn, metrics.Filters(vendor_id=vendor_id), req.window, n=1000):
        row = {"app": r["app"], "family": r["family"], "opened": r["opened"], "p1p2": r["p1p2"]}
        if r["app_id"] is not None:
            f = metrics.Filters(vendor_id=vendor_id, app_ids=[r["app_id"]])
            sla = metrics.sla(conn, f, req.window, src)
            row.update(
                resolved=sla["total"], sla_pct=sla["pct"], mttr_median_h=metrics.mttr(conn, f, req.window)["median_h"]
            )
        rows.append(row)
    return rows


def build(req: SnapshotRequest) -> SnapshotParts:
    conn, paths, settings, period, window, as_of = (
        req.conn,
        req.paths,
        req.settings,
        req.period,
        req.window,
        req.as_of,
    )
    vendor_id = req.vendor_id
    name = queries.vendor_name(conn, vendor_id) if vendor_id else None
    if not vendor_id or name is None:
        raise ValidationFailed("The vendor report needs --vendor with a known vendor id")
    fys = settings.fiscal_year_start
    suffix = " (to date)" if window.end_local < period.end_local else ""
    src = metrics.sla_source(conn)
    f = metrics.Filters(vendor_id=vendor_id)

    months = queries.complete_months(window.start_local, window.end_local)
    prev_period = queries.shift_period(period, -1, fys)
    prev_months = queries.complete_months(prev_period.start_local, prev_period.end_local)[: len(months)]
    spend = queries.cost_totals(conn, months, vendor_id=vendor_id)
    prev_spend = queries.cost_totals(conn, prev_months, vendor_id=vendor_id)

    sla = metrics.sla(conn, f, window, src)
    mttr = metrics.mttr(conn, f, window)
    quality = metrics.quality(conn, f, window)
    opened = metrics.volume_trend(conn, f, [window])[0]["opened"]
    trend = next(
        (
            v
            for v in metrics.vendor_sla_trend(conn, as_of, settings.reporting_tz, months=SLA_TREND_MONTHS)
            if v["vendor_id"] == vendor_id
        ),
        None,
    )

    contracts = queries.vendor_contracts(conn, vendor_id, as_of)
    counted = [c for c in contracts if c["counted"]]
    contract_ids = {c["contract_id"] for c in contracts}
    timeline = [
        {**r, "auto_renew": None if r["auto_renew"] is None else ("yes" if r["auto_renew"] else "no")}
        for r in metrics.renewals(conn, as_of, RENEWAL_TIMELINE_DAYS)
        if r["contract_id"] in contract_ids
        and (
            (r["days_to_end"] is not None and 0 <= r["days_to_end"] < RENEWAL_TIMELINE_DAYS)
            or (r["days_to_notice"] is not None and 0 <= r["days_to_notice"] < RENEWAL_TIMELINE_DAYS)
        )
    ]

    license_ids = queries.vendor_license_ids(conn, vendor_id)
    low = queries.license_low_threshold(paths)
    licenses = [x for x in metrics.license_utilization(conn, as_of) if x["license_id"] in license_ids]
    for x in licenses:
        util = x["utilization"]
        x["position"] = (
            "no usage data"
            if util is None
            else "under-used"
            if util < low
            else "over-used"
            if util > 1.0 or (x["assigned_ratio"] or 0) > 1.0
            else "in range"
        )
    licenses.sort(key=lambda x: (-(x["idle_cost_base"] or 0.0), x["license_id"]))
    idle_cost = round(sum((x["idle_cost_base"] or 0.0 for x in licenses if x["position"] == "under-used"), 0.0), 2)

    subjects = {("vendor", vendor_id)} | {("contract", c) for c in contract_ids} | {("license", i) for i in license_ids}
    risks = queries.risk_rows(
        [x for x in analytics.findings_as_of(conn, paths, as_of) if (x["subject_type"], x["subject_id"]) in subjects]
    )

    facts = {
        "period.label": fact(period.label, "text", "Period"),
        "period.start": fact(period.start_local.isoformat(), "date", "Period start"),
        "period.end": fact(period.last_day.isoformat(), "date", "Period end"),
        "vendor.name": fact(name, "text", "Vendor", "vendor.name"),
        "vendor.contracts.count": fact(len(counted), "count", "Current and future contracts", "vendor.contracts.count"),
        "vendor.contracts.annual_value": fact(
            round(sum((c["annual_value_base"] or 0.0 for c in counted), 0.0), 2),
            "eur",
            "Annual contract value",
            "vendor.contracts.annual_value",
        ),
        "vendor.spend.period": fact(spend["actual"], "eur", f"Vendor spend{suffix}", "vendor.spend.period"),
        "vendor.spend.prev_period": fact(
            prev_spend["actual"], "eur", f"Vendor spend, {prev_period.label} (same months)", "vendor.spend.prev_period"
        ),
        "vendor.sla.pct": fact(sla["pct"], "pct", f"SLA met, incidents resolved{suffix}", "vendor.sla.pct"),
        "vendor.sla.delta_pp": fact(
            trend["delta_pp"] if trend else None, "pp", "SLA trend: last 3 vs prior 3 months", "vendor.sla.delta_pp"
        ),
        "vendor.mttr.median_h": fact(mttr["median_h"], "hours", f"MTTR median{suffix}", "vendor.mttr.median_h"),
        "vendor.reassign.avg": fact(quality["reassign_avg"], "number", "Avg reassignments", "vendor.reassign.avg"),
        "vendor.incidents.count": fact(opened, "count", f"Incidents opened{suffix}", "vendor.incidents.count"),
        "vendor.license.idle_cost": fact(
            idle_cost, "eur", "Idle license cost (under-used lines)", "vendor.license.idle_cost"
        ),
    }

    tables = {
        "contracts": table(
            f"Contracts with {name} (term at {as_of.isoformat()})",
            [
                ("contract_number", "Contract", "text"),
                ("app", "Application", "text"),
                ("product", "Product", "text"),
                ("term", "Term", "text"),
                ("renewal_status", "Status", "text"),
                ("start_date", "Start", "date"),
                ("end_date", "End", "date"),
                ("days_to_end", "Days to end", "count"),
                ("notice_period_days", "Notice (days)", "count"),
                ("notice_deadline", "Notice deadline", "date"),
                ("auto_renew", "Auto-renew", "text"),
                ("annual_value_base", "Annual value", "eur"),
            ],
            contracts,
        ),
        "spend_trend": table(
            f"Spend with {name}, last {SPEND_TREND_MONTHS} complete months",
            [
                ("period", "Month", "text"),
                ("actual", "Actual", "eur"),
                ("budget", "Budget", "eur"),
                ("variance_pct", "Variance %", "pct"),
            ],
            queries.cost_by_month(
                conn, queries.months_ending(window.end_local, SPEND_TREND_MONTHS), vendor_id=vendor_id
            ),
        ),
        "licenses": table(
            f"License position ({name})",
            [
                ("license_id", "License", "text"),
                ("app", "Application", "text"),
                ("product", "Product", "text"),
                ("license_metric", "Metric", "text"),
                ("entitled_qty", "Entitled", "number"),
                ("assigned_qty", "Assigned", "number"),
                ("active_qty_90d", "Active 90d", "number"),
                ("utilization", "Utilization", "ratio"),
                ("assigned_ratio", "Assigned ratio", "ratio"),
                ("position", "Position", "text"),
                ("annual_cost_base", "Annual cost", "eur"),
                ("idle_cost_base", "Idle cost / yr", "eur"),
            ],
            licenses,
        ),
        "sla_trend": table(
            f"SLA, MTTR and reassignments on {name} incidents, last {SLA_TREND_MONTHS} months",
            [
                ("period", "Month", "text"),
                ("resolved", "Resolved", "count"),
                ("sla_pct", "SLA %", "pct"),
                ("mttr_median_h", "MTTR median", "hours"),
                ("reassign_avg", "Avg reassignments", "number"),
            ],
            trend["series"] if trend else [],
        ),
        "incidents_by_app": table(
            f"{name} incidents by application",
            [
                ("app", "Application", "text"),
                ("family", "Family", "text"),
                ("opened", "Opened", "count"),
                ("p1p2", "P1/P2", "count"),
                ("resolved", "Resolved", "count"),
                ("sla_pct", "SLA %", "pct"),
                ("mttr_median_h", "MTTR median", "hours"),
            ],
            _incidents_by_app(conn, vendor_id, req, src),
        ),
        "renewal_timeline": table(
            f"Renewal and notice timeline (next {RENEWAL_TIMELINE_DAYS} days from {as_of.isoformat()})",
            [
                ("contract_number", "Contract", "text"),
                ("app", "Application", "text"),
                ("product", "Product", "text"),
                ("notice_deadline", "Notice deadline", "date"),
                ("days_to_notice", "Days to notice", "count"),
                ("end_date", "End date", "date"),
                ("days_to_end", "Days to end", "count"),
                ("auto_renew", "Auto-renew", "text"),
                ("annual_value_base", "Annual value", "eur"),
            ],
            timeline,
        ),
        "risks": table(
            f"Risks for {name} (system-detected: vendor, contracts, licenses)",
            [
                ("severity", "Severity", "text"),
                ("kind", "Kind", "text"),
                ("title", "Finding", "text"),
                ("subject_id", "Subject", "text"),
                ("exposure_eur", "Exposure", "eur"),
            ],
            risks,
        ),
    }
    return SnapshotParts(facts=facts, tables=tables, sla_source=src, freshness=metrics.freshness(conn))
