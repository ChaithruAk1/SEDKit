"""Rule-origin findings ("system-detected"): deterministic risks that never depend on the AI.

`refresh_rule_findings` recomputes every enabled rule from config/risk_rules.yaml for an as-of date and upserts
`finding` rows (origin='rule'):
* same stable_key and still firing -> evidence/severity refreshed in place;
* acknowledged or suppressed rows stay hidden unless their evidence changed materially (then they re-activate);
* active rows whose condition no longer holds -> status 'superseded'.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import date, timedelta
from typing import Any

from sed import db, metrics
from sed.paths import Paths
from sed.settings import load_layered, load_settings

SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}
STATE_KEY = "rule_findings_as_of"


def _finding_id() -> str:
    # Random, not derived from (stable_key, timestamp): a finding can be superseded and re-inserted within one second.
    return "rule-" + uuid.uuid4().hex[:20]


def _eu(amount: float | None) -> str:
    return "n/a" if amount is None else f"€{amount:,.0f}"


def compute_rule_findings(conn: sqlite3.Connection, paths: Paths | None, as_of: date) -> list[dict[str, Any]]:
    rules = load_layered("risk_rules.yaml", paths).get("rules", {})
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


def _material_change(old: dict[str, Any], new: dict[str, Any]) -> bool:
    if SEVERITY_ORDER.get(new["severity"], 0) > SEVERITY_ORDER.get(old.get("severity") or "low", 0):
        return True
    old_ev = {e["fact_key"]: e["value"] for e in json.loads(old.get("payload_json") or "{}").get("evidence", [])}
    for e in new["evidence"]:
        before = old_ev.get(e["fact_key"])
        after = e["value"]
        numeric = isinstance(before, int | float) and isinstance(after, int | float)
        if numeric and before and abs(after - before) / abs(before) > 0.25:
            return True
    return False


def refresh_rule_findings(
    conn: sqlite3.Connection, paths: Paths | None, as_of: date, *, force: bool = False
) -> dict[str, Any]:
    """Upsert persisted rule findings. The persisted state tracks the newest as-of: an older as-of is skipped unless
    `force`, so a report for a past period never supersedes findings that are still current (see `findings_as_of`)."""
    state_as_of = db.get_meta(conn, STATE_KEY)
    if state_as_of and as_of.isoformat() < state_as_of and not force:
        return {"skipped": True, "state_as_of": state_as_of}
    computed = {f["stable_key"]: f for f in compute_rule_findings(conn, paths, as_of)}
    now = db.utc_now()
    stats: dict[str, Any] = {"inserted": 0, "updated": 0, "reactivated": 0, "superseded": 0, "hidden": 0}
    with db.write_tx(conn):
        db.set_meta(conn, STATE_KEY, as_of.isoformat())
        existing = {
            r["stable_key"]: dict(r)
            for r in conn.execute(
                "SELECT * FROM finding WHERE origin = 'rule' AND status IN ('active', 'acknowledged') ORDER BY "
                "created_at"
            )
        }
        for key, f in computed.items():
            payload = json.dumps(
                {"evidence": f["evidence"], "as_of": as_of.isoformat()}, ensure_ascii=False, default=str
            )
            old = existing.get(key)
            if old is None:
                conn.execute(
                    "INSERT INTO finding (finding_id, run_id, origin, stable_key, kind, subject_type, subject_id, "
                    "period, "
                    "title, body_md, severity, payload_json, status, created_at) "
                    "VALUES (?, NULL, 'rule', ?, ?, ?, ?, ?, ?, NULL, ?, ?, 'active', ?)",
                    (
                        _finding_id(),
                        key,
                        f["kind"],
                        f["subject_type"],
                        f["subject_id"],
                        as_of.isoformat(),
                        f["title"],
                        f["severity"],
                        payload,
                        now,
                    ),
                )
                stats["inserted"] += 1
                continue
            suppressed = old["status"] == "acknowledged" or (old["suppress_until"] or "") > as_of.isoformat()
            if suppressed and not _material_change(old, f):
                stats["hidden"] += 1
                conn.execute(
                    "UPDATE finding SET payload_json = ?, title = ? WHERE finding_id = ?",
                    (payload, f["title"], old["finding_id"]),
                )
                continue
            new_status = "active"
            if suppressed:
                stats["reactivated"] += 1
            else:
                stats["updated"] += 1
            conn.execute(
                "UPDATE finding SET title = ?, severity = ?, payload_json = ?, status = ?, suppress_until = NULL, "
                "period = ? WHERE finding_id = ?",
                (f["title"], f["severity"], payload, new_status, as_of.isoformat(), old["finding_id"]),
            )
        for key, old in existing.items():
            if key not in computed and old["status"] == "active":
                conn.execute("UPDATE finding SET status = 'superseded' WHERE finding_id = ?", (old["finding_id"],))
                stats["superseded"] += 1
    return stats


def published_rule_findings(conn: sqlite3.Connection, as_of: date) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT finding_id, stable_key, kind, subject_type, subject_id, severity, title, payload_json, status, "
        "suppress_until FROM finding WHERE origin = 'rule' AND status = 'active' "
        "AND (suppress_until IS NULL OR suppress_until <= ?)",
        (as_of.isoformat(),),
    ).fetchall()
    out = [dict(r) for r in rows]
    out.sort(key=lambda r: (-SEVERITY_ORDER.get(r["severity"] or "low", 0), r["kind"], r["title"]))
    return out


def findings_as_of(conn: sqlite3.Connection, paths: Paths | None, as_of: date) -> list[dict[str, Any]]:
    """Rule findings as they stood at `as_of`, for report snapshots.

    At or after the persisted state's as-of this refreshes and returns the published rows. For an older as-of the rules
    are computed read-only; human acknowledgements/suppressions still apply, and finding ids are reused by stable_key
    (None when the finding no longer exists in the current state).
    """
    state_as_of = db.get_meta(conn, STATE_KEY)
    if not state_as_of or as_of.isoformat() >= state_as_of:
        refresh_rule_findings(conn, paths, as_of)
        return published_rule_findings(conn, as_of)
    persisted = {
        r["stable_key"]: dict(r)
        for r in conn.execute(
            "SELECT * FROM finding WHERE origin = 'rule' AND status IN ('active', 'acknowledged', 'superseded') "
            "ORDER BY created_at"
        )
    }
    out = []
    for f in compute_rule_findings(conn, paths, as_of):
        old = persisted.get(f["stable_key"])
        if old and old["status"] != "superseded":
            suppressed = old["status"] == "acknowledged" or (old["suppress_until"] or "") > as_of.isoformat()
            if suppressed and not _material_change(old, f):
                continue
        payload = json.dumps({"evidence": f["evidence"], "as_of": as_of.isoformat()}, ensure_ascii=False, default=str)
        out.append(
            {
                "finding_id": old["finding_id"] if old else None,
                "stable_key": f["stable_key"],
                "kind": f["kind"],
                "subject_type": f["subject_type"],
                "subject_id": f["subject_id"],
                "severity": f["severity"],
                "title": f["title"],
                "payload_json": payload,
                "status": "active",
                "suppress_until": None,
            }
        )
    out.sort(key=lambda r: (-SEVERITY_ORDER.get(r["severity"] or "low", 0), r["kind"], r["title"]))
    return out
