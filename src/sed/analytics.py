"""Ops rule findings ("system-detected"): notice deadlines, renewals, license use, vendor SLA decline, cost variance and
quiet apps, from config/ops/risk_rules.yaml.

`compute_rule_findings` is the ops module's rule provider (`Module.rule_findings`). Upserting, suppression and
supersede live in the core engine `sed.rule_findings`; the wrappers below keep the ops call sites unchanged.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from typing import Any

from sed import metrics, modules, rule_findings
from sed.paths import Paths
from sed.rule_findings import SEVERITY_ORDER
from sed.settings import load_layered, load_settings

__all__ = [
    "SEVERITY_ORDER",
    "compute_rule_findings",
    "findings_as_of",
    "published_rule_findings",
    "refresh_rule_findings",
]
MODULE_KEY = "ops"


def _eu(amount: float | None) -> str:
    return "n/a" if amount is None else f"€{amount:,.0f}"


def compute_rule_findings(conn: sqlite3.Connection, paths: Paths | None, as_of: date) -> list[dict[str, Any]]:
    rules = load_layered("ops/risk_rules.yaml", paths).get("rules", {})
    settings = load_settings(paths)
    out: list[dict[str, Any]] = []

    nd = rules.get("notice_deadline", {})
    rh = rules.get("renewal_horizon", {})
    horizon = max(int(nd.get("max_days_to_notice", 0) or 0), int(rh.get("max_days_to_end", 0) or 0))
    for c in metrics.renewals(conn, as_of, horizon):
        subject = c["contract_id"]
        if (
            nd.get("enabled")
            and c["days_to_notice"] is not None
            and 0 <= c["days_to_notice"] <= nd["max_days_to_notice"]
        ):
            critical = c["auto_renew"] and c["days_to_notice"] <= nd.get("critical_days", 21)
            out.append(
                {
                    "stable_key": f"renewal_risk:notice:{subject}",
                    "kind": "renewal_risk",
                    "subject_type": "contract",
                    "subject_id": subject,
                    "severity": "critical" if critical else "high",
                    "title": f"Notice deadline in {c['days_to_notice']} days: {c['vendor']} – {c['product']}"
                    + (" (auto-renews)" if c["auto_renew"] else ""),
                    "evidence": [
                        {"fact_key": f"contract.{subject}.notice_deadline", "value": c["notice_deadline"]},
                        {"fact_key": f"contract.{subject}.days_to_notice", "value": c["days_to_notice"]},
                        {"fact_key": f"contract.{subject}.auto_renew", "value": bool(c["auto_renew"])},
                        {"fact_key": f"contract.{subject}.annual_value_base", "value": c["annual_value_base"]},
                    ],
                }
            )
        if rh.get("enabled") and c["days_to_end"] is not None and 0 <= c["days_to_end"] <= rh["max_days_to_end"]:
            out.append(
                {
                    "stable_key": f"renewal_risk:end:{subject}",
                    "kind": "renewal_risk",
                    "subject_type": "contract",
                    "subject_id": subject,
                    "severity": "high" if c["days_to_end"] <= 90 else "medium",
                    "title": f"Contract ends in {c['days_to_end']} days: {c['vendor']} – {c['product']}",
                    "evidence": [
                        {"fact_key": f"contract.{subject}.end_date", "value": c["end_date"]},
                        {"fact_key": f"contract.{subject}.days_to_end", "value": c["days_to_end"]},
                        {"fact_key": f"contract.{subject}.annual_value_base", "value": c["annual_value_base"]},
                    ],
                }
            )

    lu = rules.get("license_utilization", {})
    if lu.get("enabled"):
        for lic in metrics.license_utilization(conn, as_of):
            util, assigned = lic["utilization"], lic["assigned_ratio"]
            if util is None:
                continue
            reason = None
            if util < lu["low"]:
                reason = "under-used"
            elif util > lu["high"] or (assigned is not None and assigned > lu["high"]):
                reason = "over-assigned"
            if not reason:
                continue
            idle = lic["idle_cost_base"] or 0
            severity = "high" if (reason == "under-used" and idle >= 50_000) or reason == "over-assigned" else "medium"
            out.append(
                {
                    "stable_key": f"license_risk:utilization:{lic['license_id']}",
                    "kind": "license_risk",
                    "subject_type": "license",
                    "subject_id": lic["license_id"],
                    "severity": severity,
                    "title": f"License {reason}: {lic['product']} at {util:.0%} utilization"
                    + (f" (idle {_eu(idle)}/yr)" if reason == "under-used" else f" (assigned {assigned:.0%})"),
                    "evidence": [
                        {"fact_key": f"license.{lic['license_id']}.utilization", "value": util},
                        {"fact_key": f"license.{lic['license_id']}.assigned_ratio", "value": assigned},
                        {"fact_key": f"license.{lic['license_id']}.idle_cost_base", "value": idle},
                    ],
                }
            )

    vs = rules.get("vendor_sla_decline", {})
    if vs.get("enabled"):
        for v in metrics.vendor_sla_trend(
            conn, as_of, settings.reporting_tz, months=2 * int(vs.get("window_months", 3))
        ):
            if v["delta_pp"] is not None and v["delta_pp"] <= vs["delta_pp"]:
                last = v["series"][-1]
                out.append(
                    {
                        "stable_key": f"vendor_risk:sla_decline:{v['vendor_id']}",
                        "kind": "vendor_risk",
                        "subject_type": "vendor",
                        "subject_id": v["vendor_id"],
                        "severity": "high" if v["delta_pp"] <= 2 * vs["delta_pp"] else "medium",
                        "title": f"SLA decline for {v['vendor']}: {v['delta_pp']:+.1f} pp (last 3 vs prior 3 months)",
                        "evidence": [
                            {"fact_key": f"vendor.{v['vendor_id']}.sla_delta_pp", "value": v["delta_pp"]},
                            {"fact_key": f"vendor.{v['vendor_id']}.sla_pct.{last['period']}", "value": last["sla_pct"]},
                        ],
                    }
                )

    cv = rules.get("cost_variance", {})
    if cv.get("enabled"):
        last = date(as_of.year, as_of.month, 1) - timedelta(days=1)
        months = [f"{last.year}-{last.month:02d}"]
        prev = date(last.year, last.month, 1) - timedelta(days=1)
        months.append(f"{prev.year}-{prev.month:02d}")
        for row in metrics.cost_vs_budget(conn, months, "app_category"):
            if (
                row["variance_pct"] is not None
                and row["variance_pct"] >= cv["variance_pct"]
                and (row["budget"] or 0) >= 1000
            ):
                key = row["key"]
                out.append(
                    {
                        "stable_key": f"cost_risk:variance:{key}",
                        "kind": "cost_risk",
                        "subject_type": "app_category",
                        "subject_id": key,
                        "severity": "high" if row["variance_pct"] >= 2 * cv["variance_pct"] else "medium",
                        "title": f"Cost overrun {row['variance_pct']:+.0f}% vs budget: {key} "
                        f"({' & '.join(sorted(months))})",
                        "evidence": [
                            {"fact_key": f"cost.{key}.actual", "value": row["actual"]},
                            {"fact_key": f"cost.{key}.budget", "value": row["budget"]},
                            {"fact_key": f"cost.{key}.variance_pct", "value": row["variance_pct"]},
                        ],
                    }
                )

    qa = rules.get("quiet_app", {})
    if qa.get("enabled"):
        for app in metrics.quiet_apps(
            conn, as_of, int(qa["months_without_tickets"]), float(qa["min_annual_cost_base"])
        ):
            out.append(
                {
                    "stable_key": f"rationalization:quiet_app:{app['app_id']}",
                    "kind": "rationalization",
                    "subject_type": "application",
                    "subject_id": app["app_id"],
                    "severity": "medium",
                    "title": f"No tickets for {qa['months_without_tickets']} months but {_eu(app['license_cost'])}/yr "
                    f"license cost: {app['name']}",
                    "evidence": [
                        {"fact_key": f"app.{app['app_id']}.recent_tickets", "value": 0},
                        {"fact_key": f"app.{app['app_id']}.license_cost_base", "value": app["license_cost"]},
                        {"fact_key": f"app.{app['app_id']}.last_ticket", "value": app["last_ticket"]},
                    ],
                }
            )
    return out


def refresh_rule_findings(
    conn: sqlite3.Connection, paths: Paths | None, as_of: date, *, force: bool = False
) -> dict[str, Any]:
    return rule_findings.refresh(conn, paths, as_of, MODULE_KEY, force=force)


def published_rule_findings(conn: sqlite3.Connection, as_of: date) -> list[dict[str, Any]]:
    return rule_findings.published(conn, as_of, modules.get(MODULE_KEY).finding_kinds)


def findings_as_of(conn: sqlite3.Connection, paths: Paths | None, as_of: date) -> list[dict[str, Any]]:
    return rule_findings.as_of_findings(conn, paths, as_of, MODULE_KEY)
