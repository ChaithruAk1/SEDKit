"""Vendor Review content: exact facts against hand-written SQL for the planted P2 vendor, and request validation."""

from __future__ import annotations

import json
import sqlite3
import statistics
from typing import Any

from typer.testing import CliRunner

from sed import db
from sed.reports.snapshot import Snapshot, create_snapshot

Q3_TO_DATE = ("2026-06-30T22:00:00Z", "2026-08-31T22:00:00Z")  # 2026-Q3 clamped to the 2026-09-01 data date
# Local (Europe/Paris) month bounds in UTC, March to September 2026 (DST starts 29 March).
MONTH_BOUNDS = {
    "2026-03": ("2026-02-28T23:00:00Z", "2026-03-31T22:00:00Z"),
    "2026-04": ("2026-03-31T22:00:00Z", "2026-04-30T22:00:00Z"),
    "2026-05": ("2026-04-30T22:00:00Z", "2026-05-31T22:00:00Z"),
    "2026-06": ("2026-05-31T22:00:00Z", "2026-06-30T22:00:00Z"),
    "2026-07": ("2026-06-30T22:00:00Z", "2026-07-31T22:00:00Z"),
    "2026-08": ("2026-07-31T22:00:00Z", "2026-08-31T22:00:00Z"),
}


def _snapshot(profile: Any, report: str, period: str, vendor: str | None = None) -> Snapshot:
    conn = db.connect(profile.paths.db)
    try:
        return create_snapshot(conn, profile.paths, report, period, vendor)
    finally:
        conn.close()


def _rows(profile: Any, sql: str, params: tuple = ()) -> list[tuple]:
    conn = sqlite3.connect(str(profile.paths.db))
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _one(profile: Any, sql: str, params: tuple = ()) -> Any:
    return _rows(profile, sql, params)[0]


def _cli(*args: str) -> tuple[int, dict]:
    from sed.cli import app

    result = CliRunner().invoke(app, [*args, "--json"])
    lines = [ln for ln in result.stdout.strip().splitlines() if ln.strip()]
    return result.exit_code, json.loads(lines[-1])


BREACHED = (
    "COALESCE((SELECT MAX(s.has_breached) FROM task_sla s WHERE s.ticket_id = t.ticket_id AND s.sla_type = "
    "'resolution'), CASE WHEN t.made_sla = 0 THEN 1 ELSE 0 END)"
)


def _vendor_sla(profile: Any, vendor_id: str, bounds: tuple[str, str]) -> tuple[int, float | None]:
    total, met = _one(
        profile,
        f"SELECT COUNT(*), SUM({BREACHED} = 0) FROM ticket t WHERE t.kind = 'incident' AND t.vendor_id = ? "
        "AND t.resolved_at >= ? AND t.resolved_at < ?",
        (vendor_id, *bounds),
    )
    return total, (round(100.0 * met / total, 2) if total else None)


def _spend(profile: Any, vendor_id: str, months: tuple[str, ...]) -> float:
    marks = ", ".join("?" for _ in months)
    value = _one(
        profile,
        f"SELECT SUM(amount_base) FROM cost_line WHERE line_type = 'actual' AND vendor_id = ? AND period IN ({marks})",
        (vendor_id, *months),
    )[0]
    return round(value or 0.0, 2)


def test_vendor_facts_match_hand_sql(ops_profile_rw):
    vendor_id = ops_profile_rw.ids["vendor_p2"]
    snap = _snapshot(ops_profile_rw, "vendor", "2026-Q3", vendor_id)
    assert snap.as_of == "2026-09-01" and snap.vendor_id == vendor_id and snap.period_end == "2026-10-01"
    facts = {k: v["value"] for k, v in snap.facts.items()}

    assert facts["vendor.name"] == _one(ops_profile_rw, "SELECT name FROM vendor WHERE vendor_id = ?", (vendor_id,))[0]
    opened = _one(
        ops_profile_rw,
        "SELECT COUNT(*) FROM ticket WHERE kind = 'incident' AND vendor_id = ? AND opened_at >= ? AND opened_at < ?",
        (vendor_id, *Q3_TO_DATE),
    )[0]
    assert facts["vendor.incidents.count"] == opened
    assert facts["vendor.sla.pct"] == _vendor_sla(ops_profile_rw, vendor_id, Q3_TO_DATE)[1]
    resolved = _rows(
        ops_profile_rw,
        "SELECT (julianday(resolved_at) - julianday(opened_at)) * 24.0, COALESCE(reassignment_count, 0) FROM ticket "
        "WHERE kind = 'incident' AND vendor_id = ? AND resolved_at >= ? AND resolved_at < ? AND opened_at IS NOT NULL",
        (vendor_id, *Q3_TO_DATE),
    )
    assert facts["vendor.mttr.median_h"] == round(statistics.median(h for h, _ in resolved), 2)
    assert facts["vendor.reassign.avg"] == round(sum(r for _, r in resolved) / len(resolved), 2)

    assert facts["vendor.spend.period"] == _spend(ops_profile_rw, vendor_id, ("2026-07", "2026-08"))
    assert facts["vendor.spend.prev_period"] == _spend(ops_profile_rw, vendor_id, ("2026-04", "2026-05"))

    count, value = _one(
        ops_profile_rw,
        "SELECT COUNT(*), SUM(annual_value_base) FROM contract WHERE vendor_id = ? AND is_deleted = 0 "
        "AND COALESCE(renewal_status, 'active') NOT IN ('terminated', 'expired') "
        "AND (end_date IS NULL OR end_date >= '2026-09-01')",
        (vendor_id,),
    )
    assert facts["vendor.contracts.count"] == count and facts["vendor.contracts.annual_value"] == round(value or 0.0, 2)

    series = []
    for label, bounds in MONTH_BOUNDS.items():
        n, pct = _vendor_sla(ops_profile_rw, vendor_id, bounds)
        series.append((label, n, pct))
    valid = [pct for _, n, pct in series if n >= 20 and pct is not None]
    assert len(valid) == 6
    delta = round(statistics.fmean(valid[-3:]) - statistics.fmean(valid[:3]), 2)
    assert facts["vendor.sla.delta_pp"] == delta and delta < 0
    trend = snap.tables["sla_trend"]["rows"]
    assert [(r["period"], r["resolved"], r["sla_pct"]) for r in trend] == series


def test_vendor_tables(ops_profile_rw):
    vendor_id = ops_profile_rw.ids["vendor_p2"]
    snap = _snapshot(ops_profile_rw, "vendor", "2026-Q3", vendor_id)
    t = snap.tables
    contracts = {
        r[0]
        for r in _rows(
            ops_profile_rw, "SELECT contract_number FROM contract WHERE vendor_id = ? AND is_deleted = 0", (vendor_id,)
        )
    }
    assert {r["contract_number"] for r in t["contracts"]["rows"]} == contracts
    licenses = {
        r[0]
        for r in _rows(
            ops_profile_rw, "SELECT license_id FROM license WHERE vendor_id = ? AND is_deleted = 0", (vendor_id,)
        )
    }
    assert {r["license_id"] for r in t["licenses"]["rows"]} == licenses
    idle = sum(r["idle_cost_base"] or 0.0 for r in t["licenses"]["rows"] if r["position"] == "under-used")
    assert snap.facts["vendor.license.idle_cost"]["value"] == round(idle, 2)

    spend = t["spend_trend"]["rows"]
    assert [r["period"] for r in spend][-3:] == ["2026-06", "2026-07", "2026-08"] and len(spend) == 12
    assert spend[-1]["actual"] == _spend(ops_profile_rw, vendor_id, ("2026-08",))

    by_app = {r["app"]: r["opened"] for r in t["incidents_by_app"]["rows"]}
    assert sum(by_app.values()) == snap.facts["vendor.incidents.count"]["value"]

    timeline = {
        r[0]
        for r in _rows(
            ops_profile_rw,
            "SELECT contract_number FROM contract WHERE vendor_id = ? AND is_deleted = 0 "
            "AND COALESCE(renewal_status, 'active') NOT IN ('non_renewing', 'terminated', 'expired') "
            "AND ((end_date >= '2026-09-01' AND end_date < '2027-09-01') "
            "OR (notice_deadline >= '2026-09-01' AND notice_deadline < '2027-09-01'))",
            (vendor_id,),
        )
    }
    assert {r["contract_number"] for r in t["renewal_timeline"]["rows"]} == timeline

    risk_keys = {(r["kind"], r["subject_id"]) for r in t["risks"]["rows"]}
    assert ("vendor_risk", vendor_id) in risk_keys
    own = contracts | licenses | {vendor_id}
    assert all(subject in own for _, subject in risk_keys)


def test_vendor_month_period_compares_with_the_previous_month(ops_profile_rw):
    vendor_id = ops_profile_rw.ids["vendor_p2"]
    snap = _snapshot(ops_profile_rw, "vendor", "2026-08", vendor_id)
    assert snap.as_of == "2026-09-01"
    assert snap.facts["vendor.spend.period"]["value"] == _spend(ops_profile_rw, vendor_id, ("2026-08",))
    assert snap.facts["vendor.spend.prev_period"]["value"] == _spend(ops_profile_rw, vendor_id, ("2026-07",))
    assert not snap.facts["vendor.spend.period"]["label"].endswith("(to date)")


def test_vendor_requires_a_known_vendor_with_exit_2(ops_profile_rw):
    data_dir = str(ops_profile_rw.paths.data_dir)
    code, out = _cli(
        "report", "snapshot", "vendor", "--period", "2026-Q3", "--profile", "synthetic", "--data-dir", data_dir
    )
    assert code == 2 and out["error"]["kind"] == "validation" and "--vendor" in out["error"]["message"]
    code, out = _cli(
        "report", "snapshot", "vendor", "--period", "2026-Q3", "--vendor", "V-UNKNOWN", "--profile", "synthetic",
        "--data-dir", data_dir,
    )  # fmt: skip
    assert code == 2 and out["error"]["kind"] == "validation" and "Unknown vendor" in out["error"]["message"]
    assert _one(ops_profile_rw, "SELECT COUNT(*) FROM report_snapshot")[0] == 0
