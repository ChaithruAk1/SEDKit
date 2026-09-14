"""Deterministic metrics (compute-on-read, SQL aggregates; only small result sets reach Python).

All functions take an explicit ``as_of`` / period so results are reproducible. Periods are bucketed in the
reporting timezone (see sed.calendar); timestamps are stored as ISO-8601 UTC text. Every metric key used in
reports is documented in METRICS (unit + definition) and rendered to the Definitions sheet.
"""

from __future__ import annotations

import sqlite3
import statistics
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sed.calendar import Period, iso_utc, parse_utc

INCIDENT_TARGET_H = {1: 4, 2: 8, 3: 40, 4: 120, 5: 240}

METRICS: dict[str, tuple[str, str]] = {
    "inc.opened": ("count", "Incidents opened in the period (opened_at within the period, reporting timezone)."),
    "inc.resolved": ("count", "Incidents resolved in the period (resolved_at within the period)."),
    "inc.backlog": (
        "count",
        "Open incidents at period end: opened before the end and not resolved/closed by then; "
        "tickets flagged stale_open (missing from the latest active snapshot) are excluded.",
    ),
    "inc.sla.pct": (
        "pct",
        "Share of incidents resolved in the period that met their resolution SLA. Source order: "
        "task_sla resolution has_breached, else made_sla, else ops/sla.yaml calendar-hour targets.",
    ),
    "inc.mttr.median_h": (
        "hours",
        "Median hours from opened_at to resolved_at for incidents resolved in the period (calendar hours).",
    ),
    "inc.p1p2.opened": ("count", "Priority 1 and 2 incidents opened in the period."),
    "inc.reopen.pct": ("pct", "Share of incidents resolved in the period with reopen_count > 0."),
    "inc.reassign.avg": ("number", "Average reassignment_count of incidents resolved in the period."),
    "inc.stale_open": ("count", "Incidents open in the store but absent from the latest 'all open incidents' export."),
    "chg.count": ("count", "Change requests closed in the period."),
    "chg.success.pct": ("pct", "Share of closed changes with close_code 'successful'."),
    "chg.incidents_after_72h": (
        "count",
        "Incidents on the same CI opened within 72 hours after a change closed (correlation signal, not causation).",
    ),
    "renewals.count": ("count", "Active contracts (not non-renewing/terminated/expired) ending within the window."),
    "notice.count": (
        "count",
        "Active contracts whose notice deadline (end_date - notice_period_days) falls within the window.",
    ),
    "license.idle_cost": (
        "eur",
        "Sum over under-used license lines (utilization below the license_utilization 'low' risk rule, default 70%) "
        "of max(entitled - active_90d, 0) x unit cost (base currency).",
    ),
    "license.utilization": ("pct", "active_90d / entitled from the latest usage snapshot on or before as_of."),
    "cost.variance.pct": (
        "pct",
        "(actual - budget) / budget for the compared months; each month uses its newest imported budget version.",
    ),
    "vendor.sla.delta_pp": (
        "pp",
        "Average monthly SLA % of the last 3 months minus the 3 months before, for "
        "incidents handled by the vendor's assignment groups.",
    ),
    "attention.count": (
        "count",
        "Open incidents needing attention: P1/P2, at or past the SLA warning ratio, aged, "
        "reopened, ping-pong reassignments or unassigned.",
    ),
}

# Budget lines count only for the newest budget version imported for their month, so importing next fiscal year's
# budget file (a new as_of) does not hide the budgets of earlier months. Use with cost_line aliased as `c`.
BUDGET_VERSION_JOIN = (
    "LEFT JOIN (SELECT period AS bv_period, MAX(as_of) AS bv_as_of FROM cost_line WHERE line_type = 'budget' "
    "GROUP BY period) bv ON bv.bv_period = c.period"
)
BUDGET_SUM = "SUM(CASE WHEN c.line_type = 'budget' AND c.as_of IS bv.bv_as_of THEN c.amount_base END)"


@dataclass
class Filters:
    kind: str = "incident"
    app_ids: list[str] = field(default_factory=list)
    family: str | None = None
    vendor_id: str | None = None
    group: str | None = None
    priorities: list[int] = field(default_factory=list)

    def where(self, alias: str = "t") -> tuple[str, list[Any]]:
        clauses = [f"{alias}.kind = ?"]
        params: list[Any] = [self.kind]
        if self.app_ids:
            clauses.append(f"{alias}.app_id IN ({', '.join('?' for _ in self.app_ids)})")
            params += self.app_ids
        if self.family:
            clauses.append(f"{alias}.app_id IN (SELECT app_id FROM application WHERE app_family = ?)")
            params.append(self.family)
        if self.vendor_id:
            clauses.append(f"{alias}.vendor_id = ?")
            params.append(self.vendor_id)
        if self.group:
            clauses.append(f"{alias}.assignment_group = ?")
            params.append(self.group)
        if self.priorities:
            clauses.append(f"{alias}.priority IN ({', '.join('?' for _ in self.priorities)})")
            params += self.priorities
        return " AND ".join(clauses), params


def _pct(num: float, den: float) -> float | None:
    return round(100.0 * num / den, 2) if den else None


def _hours_expr(start: str, end: str) -> str:
    return f"(julianday({end}) - julianday({start})) * 24.0"


# ---------------------------------------------------------------------------
# tickets and service
# ---------------------------------------------------------------------------


def volume_trend(conn: sqlite3.Connection, f: Filters, periods: list[Period]) -> list[dict[str, Any]]:
    if not periods:
        return []
    where, params = f.where()
    sums, sum_params = [], []
    for p in periods:
        sums.append("SUM(t.opened_at >= ? AND t.opened_at < ?)")
        sum_params += [p.start_iso, p.end_iso]
        sums.append("SUM(t.resolved_at >= ? AND t.resolved_at < ?)")
        sum_params += [p.start_iso, p.end_iso]
    lo, hi = periods[0].start_iso, periods[-1].end_iso
    sql = (
        f"SELECT {', '.join(sums)} FROM ticket t WHERE {where} AND "
        "((t.opened_at >= ? AND t.opened_at < ?) OR (t.resolved_at >= ? AND t.resolved_at < ?))"
    )
    row = conn.execute(sql, [*sum_params, *params, lo, hi, lo, hi]).fetchone()
    out = []
    for i, p in enumerate(periods):
        opened, resolved = int(row[2 * i] or 0), int(row[2 * i + 1] or 0)
        out.append({"period": p.label, "opened": opened, "resolved": resolved, "net": opened - resolved})
    return out


def sla_source(conn: sqlite3.Connection) -> str:
    if conn.execute(
        "SELECT 1 FROM task_sla WHERE sla_type = 'resolution' AND has_breached IS NOT NULL LIMIT 1"
    ).fetchone():
        return "task_sla"
    if conn.execute("SELECT 1 FROM ticket WHERE kind = 'incident' AND made_sla IS NOT NULL LIMIT 1").fetchone():
        return "made_sla"
    return "targets"


def sla(conn: sqlite3.Connection, f: Filters, period: Period, source: str | None = None) -> dict[str, Any]:
    src = source or sla_source(conn)
    where, params = f.where()
    base = f"FROM ticket t WHERE {where} AND t.resolved_at >= ? AND t.resolved_at < ?"
    bounds = [period.start_iso, period.end_iso]
    if src == "task_sla":
        sql = (
            f"SELECT t.priority, COUNT(*), SUM(COALESCE(s.breached, t.made_sla = 0, 0) = 0) FROM ticket t "
            "LEFT JOIN (SELECT ticket_id, MAX(has_breached) AS breached FROM task_sla WHERE sla_type = 'resolution' "
            f"GROUP BY ticket_id) s ON s.ticket_id = t.ticket_id WHERE {where} AND t.resolved_at >= ? "
            "AND t.resolved_at < ? GROUP BY t.priority"
        )
    elif src == "made_sla":
        sql = f"SELECT t.priority, COUNT(*), SUM(COALESCE(t.made_sla, 1)) {base} GROUP BY t.priority"
    else:
        cases = " ".join(f"WHEN {p} THEN {h}" for p, h in INCIDENT_TARGET_H.items())
        sql = (
            f"SELECT t.priority, COUNT(*), SUM({_hours_expr('t.opened_at', 't.resolved_at')} <= "
            f"(CASE t.priority {cases} ELSE 120 END)) {base} GROUP BY t.priority"
        )
    rows = conn.execute(sql, [*params, *bounds]).fetchall()
    by_priority = {
        int(r[0] or 0): {"total": int(r[1]), "met": int(r[2] or 0), "pct": _pct(r[2] or 0, r[1])} for r in rows
    }
    total = sum(v["total"] for v in by_priority.values())
    met = sum(v["met"] for v in by_priority.values())
    return {"pct": _pct(met, total), "met": met, "total": total, "source": src, "by_priority": by_priority}


def sla_met_sql(source: str) -> str:
    """SQL (ticket alias `t`) that is true when a resolved incident met its resolution SLA, counted as sla() does."""
    if source == "task_sla":
        return (
            "(COALESCE((SELECT MAX(s.has_breached) FROM task_sla s WHERE s.ticket_id = t.ticket_id "
            "AND s.sla_type = 'resolution'), t.made_sla = 0, 0) = 0)"
        )
    if source == "made_sla":
        return "COALESCE(t.made_sla, 1)"
    cases = " ".join(f"WHEN {p} THEN {h}" for p, h in INCIDENT_TARGET_H.items())
    return f"({_hours_expr('t.opened_at', 't.resolved_at')} <= (CASE t.priority {cases} ELSE 120 END))"


def mttr(conn: sqlite3.Connection, f: Filters, period: Period) -> dict[str, Any]:
    where, params = f.where()
    sql = (
        f"SELECT {_hours_expr('t.opened_at', 't.resolved_at')} FROM ticket t WHERE {where} "
        "AND t.resolved_at >= ? AND t.resolved_at < ? AND t.opened_at IS NOT NULL"
    )
    hours = sorted(r[0] for r in conn.execute(sql, [*params, period.start_iso, period.end_iso]) if r[0] is not None)
    if not hours:
        return {"count": 0, "median_h": None, "mean_h": None, "p90_h": None}
    p90 = hours[min(len(hours) - 1, round(0.9 * (len(hours) - 1)))]
    return {
        "count": len(hours),
        "median_h": round(statistics.median(hours), 2),
        "mean_h": round(statistics.fmean(hours), 2),
        "p90_h": round(p90, 2),
    }


def _open_at_clause(alias: str = "t") -> str:
    return (
        f"{alias}.opened_at < ? AND ({alias}.resolved_at IS NULL OR {alias}.resolved_at >= ?) "
        f"AND ({alias}.closed_at IS NULL OR {alias}.closed_at >= ?)"
    )


def backlog(conn: sqlite3.Connection, f: Filters, at: datetime, *, exclude_stale: bool = True) -> dict[str, Any]:
    where, params = f.where()
    at_iso = iso_utc(at)
    stale = " AND t.stale_open = 0" if exclude_stale else ""
    rows = conn.execute(
        f"SELECT t.opened_at, t.assignment_group FROM ticket t WHERE {where} AND {_open_at_clause()}{stale}",
        [*params, at_iso, at_iso, at_iso],
    ).fetchall()
    buckets = {"0-7d": 0, "8-30d": 0, "31-90d": 0, ">90d": 0}
    by_group: dict[str, dict[str, int]] = {}
    for opened, group in rows:
        age = (at - parse_utc(opened)).total_seconds() / 86400
        key = "0-7d" if age <= 7 else "8-30d" if age <= 30 else "31-90d" if age <= 90 else ">90d"
        buckets[key] += 1
        g = by_group.setdefault(group or "(unassigned)", {"0-7d": 0, "8-30d": 0, "31-90d": 0, ">90d": 0, "total": 0})
        g[key] += 1
        g["total"] += 1
    return {
        "total": len(rows),
        "aging": buckets,
        "by_group": dict(sorted(by_group.items(), key=lambda kv: -kv[1]["total"])),
    }


def stale_open_count(conn: sqlite3.Connection, f: Filters) -> int:
    where, params = f.where()
    return int(conn.execute(f"SELECT COUNT(*) FROM ticket t WHERE {where} AND t.stale_open = 1", params).fetchone()[0])


def group_flow(conn: sqlite3.Connection, f: Filters, periods: list[Period]) -> list[dict[str, Any]]:
    """Arrivals vs closures per assignment group per period (backlog growth signal)."""
    where, params = f.where()
    out = []
    for p in periods:
        rows = conn.execute(
            f"SELECT t.assignment_group, SUM(t.opened_at >= ? AND t.opened_at < ?), "
            f"SUM(t.resolved_at >= ? AND t.resolved_at < ?) FROM ticket t WHERE {where} "
            "AND ((t.opened_at >= ? AND t.opened_at < ?) OR (t.resolved_at >= ? AND t.resolved_at < ?)) "
            "GROUP BY t.assignment_group",
            [p.start_iso, p.end_iso, p.start_iso, p.end_iso, *params, p.start_iso, p.end_iso, p.start_iso, p.end_iso],
        ).fetchall()
        for group, arrived, closed in rows:
            out.append({"period": p.label, "group": group, "arrived": int(arrived or 0), "closed": int(closed or 0)})
    return out


def quality(conn: sqlite3.Connection, f: Filters, period: Period) -> dict[str, Any]:
    where, params = f.where()
    row = conn.execute(
        f"SELECT COUNT(*), SUM(COALESCE(t.reopen_count, 0) > 0), AVG(COALESCE(t.reassignment_count, 0)), "
        f"SUM(t.priority <= 2) FROM ticket t WHERE {where} AND t.resolved_at >= ? AND t.resolved_at < ?",
        [*params, period.start_iso, period.end_iso],
    ).fetchone()
    total = int(row[0] or 0)
    return {
        "resolved": total,
        "reopen_pct": _pct(row[1] or 0, total),
        "reassign_avg": round(row[2], 2) if row[2] is not None else None,
        "p1p2_share_pct": _pct(row[3] or 0, total),
    }


def p1p2_opened(conn: sqlite3.Connection, f: Filters, period: Period, limit: int = 50) -> list[dict[str, Any]]:
    where, params = f.where()
    rows = conn.execute(
        f"SELECT t.number, t.priority, a.name AS app, t.state, t.assignment_group, t.opened_at, t.resolved_at, "
        f"t.short_description FROM ticket t LEFT JOIN application a ON a.app_id = t.app_id WHERE {where} "
        "AND t.priority <= 2 AND t.opened_at >= ? AND t.opened_at < ? ORDER BY t.priority, t.opened_at LIMIT ?",
        [*params, period.start_iso, period.end_iso, limit],
    ).fetchall()
    return [dict(r) for r in rows]


def changes(conn: sqlite3.Connection, period: Period, app_ids: list[str] | None = None) -> dict[str, Any]:
    app_clause, app_params = "", []
    if app_ids:
        app_clause = f" AND t.app_id IN ({', '.join('?' for _ in app_ids)})"
        app_params = list(app_ids)
    rows = conn.execute(
        "SELECT t.number, t.change_type, t.close_code, t.cmdb_ci_raw, t.closed_at, a.name AS app, t.short_description "
        f"FROM ticket t LEFT JOIN application a ON a.app_id = t.app_id WHERE t.kind = 'change_request'{app_clause} "
        "AND t.closed_at >= ? AND t.closed_at < ?",
        [*app_params, period.start_iso, period.end_iso],
    ).fetchall()
    by_type: dict[str, int] = {}
    successful = 0
    failed = []
    after_72h = 0
    for r in rows:
        by_type[r["change_type"] or "unknown"] = by_type.get(r["change_type"] or "unknown", 0) + 1
        if (r["close_code"] or "").lower() == "successful":
            successful += 1
        else:
            failed.append(
                {k: r[k] for k in ("number", "app", "change_type", "close_code", "closed_at", "short_description")}
            )
        if r["cmdb_ci_raw"] and r["closed_at"]:
            end = iso_utc(parse_utc(r["closed_at"]) + timedelta(hours=72))
            after_72h += conn.execute(
                "SELECT COUNT(*) FROM ticket WHERE kind = 'incident' AND cmdb_ci_raw = ? AND opened_at >= ? AND "
                "opened_at < ?",
                (r["cmdb_ci_raw"], r["closed_at"], end),
            ).fetchone()[0]
    return {
        "count": len(rows),
        "by_type": by_type,
        "success_pct": _pct(successful, len(rows)),
        "failed": failed,
        "incidents_after_72h": after_72h,
    }


def top_apps(conn: sqlite3.Connection, f: Filters, period: Period, n: int = 10) -> list[dict[str, Any]]:
    where, params = f.where()
    rows = conn.execute(
        f"SELECT t.app_id, COALESCE(a.name, '(unattributed)') AS app, a.app_family AS family, COUNT(*) AS opened, "
        f"SUM(t.priority <= 2) AS p1p2 FROM ticket t LEFT JOIN application a ON a.app_id = t.app_id WHERE {where} "
        "AND t.opened_at >= ? AND t.opened_at < ? GROUP BY t.app_id ORDER BY opened DESC LIMIT ?",
        [*params, period.start_iso, period.end_iso, n],
    ).fetchall()
    return [dict(r) for r in rows]


def category_breakdown(conn: sqlite3.Connection, f: Filters, period: Period) -> list[dict[str, Any]]:
    """ServiceNow category vs (approved) AI app-owner category for incidents opened in the period."""
    where, params = f.where()
    rows = conn.execute(
        "SELECT COALESCE(t.category, '(none)') AS sn_category, COALESCE(t.am_category, '(not labelled)') AS "
        "am_category, "
        f"COUNT(*) AS n FROM v_ticket t WHERE {where} AND t.opened_at >= ? AND t.opened_at < ? "
        "GROUP BY 1, 2 ORDER BY n DESC",
        [*params, period.start_iso, period.end_iso],
    ).fetchall()
    return [dict(r) for r in rows]


def attention(
    conn: sqlite3.Connection, f: Filters, as_of: datetime, thresholds: Any, limit: int = 200
) -> dict[str, Any]:
    where, params = f.where()
    at = iso_utc(as_of)
    rows = conn.execute(
        f"SELECT t.number, t.priority, a.name AS app, t.state, t.assignment_group, t.assigned_to_pid, t.opened_at, "
        f"t.reassignment_count, t.reopen_count, t.short_description FROM ticket t "
        f"LEFT JOIN application a ON a.app_id = t.app_id WHERE {where} AND t.is_open = 1 AND t.stale_open = 0 "
        "AND t.opened_at < ?",
        [*params, at],
    ).fetchall()
    items = []
    for r in rows:
        reasons = []
        elapsed_h = (as_of - parse_utc(r["opened_at"])).total_seconds() / 3600
        target = INCIDENT_TARGET_H.get(r["priority"] or 4, 120)
        if (r["priority"] or 9) <= 2:
            reasons.append(f"P{r['priority']} open")
        if elapsed_h >= target:
            reasons.append("past SLA target")
        elif elapsed_h >= thresholds.sla_warning_ratio * target:
            reasons.append("near SLA target")
        if elapsed_h / 24 > thresholds.aged_ticket_days:
            reasons.append(f"aged > {thresholds.aged_ticket_days}d")
        if (r["reopen_count"] or 0) > 0:
            reasons.append("reopened")
        if (r["reassignment_count"] or 0) >= thresholds.reassignment_pingpong:
            reasons.append("ping-pong reassignments")
        if not r["assigned_to_pid"]:
            reasons.append("unassigned")
        if reasons:
            items.append(
                {
                    **dict(r),
                    "age_days": round(elapsed_h / 24, 1),
                    "reasons": ", ".join(reasons),
                    "_rank": (min(r["priority"] or 9, 9), -elapsed_h),
                }
            )
    items.sort(key=lambda x: x["_rank"])
    for item in items:
        item.pop("_rank")
    by_reason: dict[str, int] = {}
    for item in items:
        for reason in item["reasons"].split(", "):
            key = reason.split(" ")[0] if reason.startswith("P") else reason
            by_reason[key] = by_reason.get(key, 0) + 1
    return {"count": len(items), "by_reason": by_reason, "items": items[:limit]}


# ---------------------------------------------------------------------------
# cost, license and vendor
# ---------------------------------------------------------------------------


def renewals(conn: sqlite3.Connection, as_of: date, days: int) -> list[dict[str, Any]]:
    end = (as_of + timedelta(days=days)).isoformat()
    rows = conn.execute(
        "SELECT c.contract_id, c.contract_number, COALESCE(v.name, c.vendor_raw) AS vendor, "
        "COALESCE(a.name, c.app_raw) AS app, c.product, c.end_date, c.notice_deadline, c.notice_period_days, "
        "c.auto_renew, c.renewal_status, c.annual_value_base FROM contract c "
        "LEFT JOIN vendor v ON v.vendor_id = c.vendor_id LEFT JOIN application a ON a.app_id = c.app_id "
        "WHERE c.is_deleted = 0 AND COALESCE(c.renewal_status, 'active') NOT IN ('non_renewing', 'terminated', "
        "'expired') "
        "AND ((c.end_date >= ? AND c.end_date <= ?) OR (c.notice_deadline >= ? AND c.notice_deadline <= ?)) "
        "ORDER BY COALESCE(c.notice_deadline, c.end_date)",
        (as_of.isoformat(), end, as_of.isoformat(), end),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["days_to_end"] = (date.fromisoformat(r["end_date"]) - as_of).days if r["end_date"] else None
        d["days_to_notice"] = (date.fromisoformat(r["notice_deadline"]) - as_of).days if r["notice_deadline"] else None
        out.append(d)
    return out


def license_utilization(conn: sqlite3.Connection, as_of: date) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT l.license_id, COALESCE(a.name, l.app_raw) AS app, COALESCE(v.name, l.vendor_raw) AS vendor, l.product, "
        "l.license_metric, l.entitled_qty, l.unit_cost_base, u.as_of_date, u.assigned_qty, u.active_qty_90d "
        "FROM license l LEFT JOIN application a ON a.app_id = l.app_id LEFT JOIN vendor v ON v.vendor_id = l.vendor_id "
        "LEFT JOIN license_usage u ON u.license_id = l.license_id AND u.as_of_date = ("
        "  SELECT MAX(as_of_date) FROM license_usage WHERE license_id = l.license_id AND as_of_date <= ?) "
        "WHERE l.is_deleted = 0",
        (as_of.isoformat(),),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        entitled = r["entitled_qty"] or 0
        active = r["active_qty_90d"]
        d["utilization"] = round(active / entitled, 4) if entitled and active is not None else None
        d["assigned_ratio"] = (
            round(r["assigned_qty"] / entitled, 4) if entitled and r["assigned_qty"] is not None else None
        )
        d["idle_cost_base"] = (
            round(max(entitled - active, 0) * (r["unit_cost_base"] or 0), 2) if active is not None else None
        )
        d["annual_cost_base"] = round(entitled * (r["unit_cost_base"] or 0), 2)
        out.append(d)
    return out


def cost_vs_budget(conn: sqlite3.Connection, months: list[str], group_by: str = "app") -> list[dict[str, Any]]:
    if not months:
        return []
    col = {
        "app": "COALESCE(a.name, c.app_raw)",
        "vendor": "COALESCE(v.name, c.vendor_raw)",
        "category": "c.cost_category",
        "app_category": "COALESCE(a.name, c.app_raw) || ' / ' || COALESCE(c.cost_category, '')",
    }[group_by]
    marks = ", ".join("?" for _ in months)
    rows = conn.execute(
        f"SELECT {col} AS key, SUM(CASE WHEN c.line_type = 'actual' THEN c.amount_base END) AS actual, "
        f"{BUDGET_SUM} AS budget "
        f"FROM cost_line c {BUDGET_VERSION_JOIN} LEFT JOIN application a ON a.app_id = c.app_id "
        "LEFT JOIN vendor v ON v.vendor_id = c.vendor_id "
        f"WHERE c.period IN ({marks}) GROUP BY key ORDER BY actual DESC",
        months,
    ).fetchall()
    out = []
    for r in rows:
        actual, budget = r["actual"] or 0.0, r["budget"]
        variance = round(100.0 * (actual - budget) / budget, 2) if budget else None
        out.append(
            {
                "key": r["key"],
                "actual": round(actual, 2),
                "budget": round(budget, 2) if budget else None,
                "variance_pct": variance,
            }
        )
    return out


def spend_by_vendor(conn: sqlite3.Connection, months: list[str]) -> list[dict[str, Any]]:
    return cost_vs_budget(conn, months, "vendor")


def vendor_sla_trend(
    conn: sqlite3.Connection, as_of: date, tz: str, months: int = 6, min_tickets: int = 20
) -> list[dict[str, Any]]:
    """Monthly SLA %, MTTR and reassignments per vendor (one query, bucketed in the reporting timezone)."""
    from sed.calendar import parse_period, shift_label, to_local

    last = parse_period(shift_label(f"{as_of.year}-{as_of.month:02d}", -1), tz)
    periods = [parse_period(shift_label(last.label, -k), tz) for k in range(months - 1, -1, -1)]
    src = sla_source(conn)
    rows = conn.execute(
        f"SELECT t.vendor_id, v.name, t.resolved_at, {_hours_expr('t.opened_at', 't.resolved_at')} AS hours, "
        f"CASE WHEN {sla_met_sql(src)} THEN 0 ELSE 1 END AS breached, COALESCE(t.reassignment_count, 0) AS "
        "reassign "
        "FROM ticket t JOIN vendor v ON v.vendor_id = t.vendor_id WHERE t.kind = 'incident' "
        "AND t.resolved_at >= ? AND t.resolved_at < ?",
        (periods[0].start_iso, periods[-1].end_iso),
    ).fetchall()
    labels = {p.label: i for i, p in enumerate(periods)}
    acc: dict[str, dict[str, Any]] = {}
    for vendor_id, name, resolved_at, hours, breached, reassign in rows:
        local = to_local(resolved_at, tz)
        idx = labels.get(f"{local.year}-{local.month:02d}")
        if idx is None:
            continue
        v = acc.setdefault(
            vendor_id, {"name": name, "buckets": [{"n": 0, "met": 0, "hours": [], "reassign": 0} for _ in periods]}
        )
        b = v["buckets"][idx]
        b["n"] += 1
        b["met"] += 0 if breached else 1
        b["reassign"] += reassign
        if hours is not None:
            b["hours"].append(hours)
    out = []
    for vendor_id, v in acc.items():
        name = v["name"]
        series = [
            {
                "period": p.label,
                "sla_pct": _pct(b["met"], b["n"]),
                "resolved": b["n"],
                "mttr_median_h": round(statistics.median(b["hours"]), 2) if b["hours"] else None,
                "reassign_avg": round(b["reassign"] / b["n"], 2) if b["n"] else None,
            }
            for p, b in zip(periods, v["buckets"], strict=True)
        ]
        valid = [x for x in series if x["resolved"] >= min_tickets and x["sla_pct"] is not None]
        delta = None
        if len(valid) >= 6:
            recent = [x["sla_pct"] for x in valid[-3:]]
            prior = [x["sla_pct"] for x in valid[-6:-3]]
            delta = round(statistics.fmean(recent) - statistics.fmean(prior), 2)
        out.append({"vendor_id": vendor_id, "vendor": name, "delta_pp": delta, "series": series})
    return sorted(out, key=lambda x: (x["delta_pp"] is None, x["delta_pp"] or 0))


def quiet_apps(conn: sqlite3.Connection, as_of: date, months: int, min_annual_cost: float) -> list[dict[str, Any]]:
    since = datetime(as_of.year, as_of.month, 1, tzinfo=UTC)
    for _ in range(months):
        since = (since - timedelta(days=1)).replace(day=1)
    rows = conn.execute(
        "SELECT a.app_id, a.name, a.business_criticality, a.life_cycle_stage, "
        "(SELECT COUNT(*) FROM ticket t WHERE t.app_id = a.app_id AND t.opened_at >= ?) AS recent_tickets, "
        "(SELECT MAX(opened_at) FROM ticket t WHERE t.app_id = a.app_id) AS last_ticket, "
        "(SELECT COALESCE(SUM(entitled_qty * unit_cost_base), 0) FROM license l WHERE l.app_id = a.app_id "
        " AND l.is_deleted = 0) AS license_cost "
        "FROM application a WHERE a.is_deleted = 0",
        (iso_utc(since),),
    ).fetchall()
    return [dict(r) for r in rows if r["recent_tickets"] == 0 and (r["license_cost"] or 0) >= min_annual_cost]


def jira_progress(conn: sqlite3.Connection, period: Period) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT COALESCE(a.name, w.project_key) AS app, SUM(w.resolved >= ? AND w.resolved < ?) AS resolved, "
        "SUM(w.resolved IS NULL) AS open, SUM(CASE WHEN w.resolved >= ? AND w.resolved < ? THEN w.story_points END) "
        "AS points "
        "FROM work_item w LEFT JOIN application a ON a.app_id = w.app_id GROUP BY 1 ORDER BY resolved DESC",
        (period.start_iso, period.end_iso, period.start_iso, period.end_iso),
    ).fetchall()
    return [dict(r) for r in rows]


def freshness(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT mapping_name, MAX(imported_at) AS last_import, COUNT(*) AS files, MAX(as_of) AS latest_as_of "
        "FROM import_batch WHERE status = 'completed' GROUP BY mapping_name ORDER BY mapping_name"
    ).fetchall()
    out = [dict(r) for r in rows]
    latest = conn.execute("SELECT MAX(sys_updated_on) FROM ticket").fetchone()[0]
    out.append(
        {"mapping_name": "ticket data (max sys_updated_on)", "last_import": latest, "files": None, "latest_as_of": None}
    )
    return out
