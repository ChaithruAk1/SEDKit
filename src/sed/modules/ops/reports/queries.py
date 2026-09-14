"""Shared SQL and helpers for the monthly, quarterly and vendor builders.

Everything that `sed.metrics` / `sed.analytics` already computes is called from there; this file only holds the
aggregates those modules do not offer (quarter/year-to-date cost totals, problem status, Jira delivery, upcoming
changes, portfolio health, vendor contracts) plus small calendar and risk helpers. Conventions match `sed.metrics`:
bounds are half-open `[start, end)`, ticket timestamps are ISO-8601 UTC text compared as strings, contract dates are
ISO dates, and cost lines are bucketed by their `period` (YYYY-MM). Budgets use the newest budget version per month,
exactly like `metrics.cost_vs_budget`.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from typing import Any

from sed.analytics import SEVERITY_ORDER
from sed.calendar import Period, parse_period, parse_utc, shift_label
from sed.metrics import BUDGET_SUM, BUDGET_VERSION_JOIN
from sed.paths import Paths
from sed.settings import load_layered

INACTIVE_CONTRACT_STATUSES = ("terminated", "expired")
DEFAULT_LICENSE_LOW = 0.70
# Evidence keys (suffixes) that carry the money at stake behind a rule finding, in order of preference.
EXPOSURE_SUFFIXES = (".annual_value_base", ".idle_cost_base", ".license_cost_base", ".actual")


# ---------------------------------------------------------------------------
# calendar helpers
# ---------------------------------------------------------------------------


def shift_period(period: Period, delta: int, fiscal_year_start: int) -> Period:
    """`period` moved by `delta` periods of its own kind (keeps the fiscal-year setting, unlike Period.previous)."""
    return parse_period(shift_label(period.label, delta), period.tz, fiscal_year_start)


def complete_months(start: date, end: date) -> list[str]:
    """Labels (YYYY-MM) of the calendar months lying fully inside `[start, end)`, oldest first."""
    out: list[str] = []
    y, m = (start.year, start.month) if start.day == 1 else _next_month(start.year, start.month)
    while True:
        ny, nm = _next_month(y, m)
        if date(ny, nm, 1) > end:
            return out
        out.append(f"{y}-{m:02d}")
        y, m = ny, nm


def months_ending(end: date, count: int) -> list[str]:
    """The `count` complete calendar months before the exclusive bound `end`, oldest first."""
    y, m = end.year, end.month
    out = []
    for _ in range(count):
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
        out.append(f"{y}-{m:02d}")
    return list(reversed(out))


def fiscal_year_start_date(day: date, fiscal_year_start: int) -> date:
    """First day of the fiscal year containing `day`."""
    year = day.year if day.month >= fiscal_year_start else day.year - 1
    return date(year, fiscal_year_start, 1)


def _next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def _marks(values: list[Any]) -> str:
    return ", ".join("?" for _ in values)


def _money(value: float | None) -> float | None:
    return round(value, 2) if value is not None else None


def variance_pct(actual: float | None, budget: float | None) -> float | None:
    """(actual - budget) / budget in percent, rounded like metrics.cost_vs_budget; None without a budget."""
    if actual is None or not budget:
        return None
    return round(100.0 * (actual - budget) / budget, 2)


# ---------------------------------------------------------------------------
# cost
# ---------------------------------------------------------------------------


def actual_months(conn: sqlite3.Connection, months: list[str]) -> set[str]:
    """The months among `months` with an imported actual cost line in the base currency (any application or vendor).

    A month whose actual lines all lack an FX rate counts as not imported, so it is never reported as zero spend.
    """
    if not months:
        return set()
    rows = conn.execute(
        "SELECT DISTINCT period FROM cost_line WHERE line_type = 'actual' AND amount_base IS NOT NULL "
        f"AND period IN ({_marks(months)})",
        months,
    ).fetchall()
    return {r[0] for r in rows}


def unconverted_lines(conn: sqlite3.Connection, months: list[str], *, vendor_id: str | None = None) -> int:
    """Cost lines in `months` with an amount but no base-currency amount (no FX rate); every cost total leaves them
    out, so the reports show this count next to the totals."""
    if not months:
        return 0
    vendor_clause, params = ("AND vendor_id = ?", [vendor_id]) if vendor_id else ("", [])
    return int(
        conn.execute(
            "SELECT COUNT(*) FROM cost_line WHERE amount IS NOT NULL AND amount_base IS NULL "
            f"AND period IN ({_marks(months)}) {vendor_clause}",
            [*months, *params],
        ).fetchone()[0]
    )


def fx_note(count: int) -> str:
    """Label suffix for cost facts when lines without an FX rate were left out."""
    return f" (excl. {count} lines without FX rate)" if count else ""


def cost_months(conn: sqlite3.Connection, start: date, end: date) -> list[str]:
    """Months to aggregate cost over: complete calendar months inside `[start, end)` whose actuals were imported.

    Cost exports usually lag the ticket data, so a month without any actual line is left out of both the actual and
    the budget side instead of being compared as zero spend against a full budget.
    """
    months = complete_months(start, end)
    covered = actual_months(conn, months)
    return [m for m in months if m in covered]


def like_for_like_months(conn: sqlite3.Connection, months: list[str], period: Period) -> list[str]:
    """The previous period's counterparts of `months` (same position in the period) that have imported actuals."""
    span = len(complete_months(period.start_local, period.end_local))
    previous = [shift_label(m, -span) for m in months]
    covered = actual_months(conn, previous)
    return [m for m in previous if m in covered]


def cost_totals(conn: sqlite3.Connection, months: list[str], *, vendor_id: str | None = None) -> dict[str, Any]:
    """Actual and budget (newest version per month) totals in base currency over `months`, optionally for one vendor.

    `actual` is None when no actual cost line exists for any of the months (nothing imported yet, or no month to
    report), and 0.0 when actuals exist but none were booked to the vendor.
    """
    if not months:
        return {"actual": None, "budget": None}
    vendor_clause, params = ("AND c.vendor_id = ?", [vendor_id]) if vendor_id else ("", [])
    row = conn.execute(
        f"SELECT SUM(CASE WHEN c.line_type = 'actual' THEN c.amount_base END), {BUDGET_SUM} "
        f"FROM cost_line c {BUDGET_VERSION_JOIN} WHERE c.period IN ({_marks(months)}) {vendor_clause}",
        [*months, *params],
    ).fetchone()
    actual = _money(row[0] or 0.0) if actual_months(conn, months) else None
    return {"actual": actual, "budget": _money(row[1])}


def cost_by_month(conn: sqlite3.Connection, months: list[str], *, vendor_id: str | None = None) -> list[dict[str, Any]]:
    """One row per month in `months`: period, actual, budget, variance_pct.

    A month's actual is 0.0 when actuals were imported for that month but none were booked (to the vendor), and None
    when no actual cost line exists for the month at all.
    """
    if not months:
        return []
    vendor_clause, params = ("AND c.vendor_id = ?", [vendor_id]) if vendor_id else ("", [])
    rows = conn.execute(
        "SELECT c.period, SUM(CASE WHEN c.line_type = 'actual' THEN c.amount_base END) AS actual, "
        f"{BUDGET_SUM} AS budget FROM cost_line c {BUDGET_VERSION_JOIN} "
        f"WHERE c.period IN ({_marks(months)}) {vendor_clause} GROUP BY c.period",
        [*months, *params],
    ).fetchall()
    by_period = {r["period"]: r for r in rows}
    covered = actual_months(conn, months)
    out = []
    for month in months:
        r = by_period.get(month)
        actual = (_money(r["actual"] or 0.0) if r else 0.0) if month in covered else None
        budget = _money(r["budget"]) if r else None
        out.append({"period": month, "actual": actual, "budget": budget, "variance_pct": variance_pct(actual, budget)})
    return out


# ---------------------------------------------------------------------------
# service (monthly)
# ---------------------------------------------------------------------------


def app_families(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT app_family FROM application WHERE is_deleted = 0 AND app_family IS NOT NULL "
        "ORDER BY app_family"
    ).fetchall()
    return [r[0] for r in rows]


def top_recurring(
    conn: sqlite3.Connection, window: Period, history: list[Period], limit: int = 10
) -> list[dict[str, Any]]:
    """Incident groups (application x ServiceNow category x subcategory) opened in `window`, with recurrence.

    `history` are the full periods before the window (oldest first): `prev_3m` counts the last three of them and
    `months_seen` counts how many of history + window had at least one incident in the group. Only groups seen in
    at least two months are recurring.
    """
    periods = [*history, window]
    prev = history[-3:]
    seen = " + ".join("MAX(t.opened_at >= ? AND t.opened_at < ?)" for _ in periods)
    seen_params = [v for p in periods for v in (p.start_iso, p.end_iso)]
    prev_start = prev[0].start_iso if prev else window.start_iso
    rows = conn.execute(
        "SELECT COALESCE(a.name, '(unattributed)') AS app, a.app_family AS family, "
        "COALESCE(t.category, '(none)') AS category, COALESCE(t.subcategory, '(none)') AS subcategory, "
        "SUM(t.opened_at >= ? AND t.opened_at < ?) AS incidents, "
        "SUM(t.opened_at >= ? AND t.opened_at < ?) AS prev_3m, "
        "SUM(t.priority <= 2 AND t.opened_at >= ? AND t.opened_at < ?) AS p1p2, "
        f"({seen}) AS months_seen, "
        "GROUP_CONCAT(DISTINCT CASE WHEN t.opened_at >= ? AND t.opened_at < ? THEN t.problem_id END) AS problems "
        "FROM ticket t LEFT JOIN application a ON a.app_id = t.app_id "
        "WHERE t.kind = 'incident' AND t.opened_at >= ? AND t.opened_at < ? "
        "GROUP BY t.app_id, t.category, t.subcategory "
        "HAVING incidents > 0 AND months_seen >= 2 "
        "ORDER BY incidents DESC, app, category, subcategory LIMIT ?",
        [
            window.start_iso,
            window.end_iso,
            prev_start,
            window.start_iso,
            window.start_iso,
            window.end_iso,
            *seen_params,
            window.start_iso,
            window.end_iso,
            periods[0].start_iso,
            window.end_iso,
            limit,
        ],
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["problems"] = ", ".join(sorted(x for x in (r["problems"] or "").split(",") if x)) or None
        out.append(d)
    return out


def problems(conn: sqlite3.Connection, window: Period) -> list[dict[str, Any]]:
    """Problems open at some point of `window` (opened before its end, not resolved before its start).

    `status` is the status at the window end (open | resolved); `state` is the latest exported ServiceNow state.
    Linked incidents count only incidents opened before the window end.
    """
    end = window.end_iso
    rows = conn.execute(
        "SELECT p.number, COALESCE(a.name, '(unattributed)') AS app, p.priority, p.state, p.opened_at, p.resolved_at, "
        "COALESCE(l.linked, 0) AS incidents_linked, COALESCE(l.in_period, 0) AS incidents_in_period, "
        "p.short_description FROM ticket p LEFT JOIN application a ON a.app_id = p.app_id "
        "LEFT JOIN (SELECT problem_id, COUNT(*) AS linked, SUM(opened_at >= ?) AS in_period FROM ticket "
        "  WHERE kind = 'incident' AND problem_id IS NOT NULL AND opened_at < ? GROUP BY problem_id) l "
        "  ON l.problem_id = p.number "
        "WHERE p.kind = 'problem' AND p.opened_at < ? AND (p.resolved_at IS NULL OR p.resolved_at >= ?)",
        [window.start_iso, end, end, window.start_iso],
    ).fetchall()
    at = window.end_utc
    out = []
    for r in rows:
        d = dict(r)
        is_open = r["resolved_at"] is None or r["resolved_at"] >= end
        opened = parse_utc(r["opened_at"])
        d["status"] = "open" if is_open else "resolved"
        d["age_days"] = round((at - opened).total_seconds() / 86400, 1) if is_open else None
        d["days_to_resolve"] = (
            None if is_open else round((parse_utc(r["resolved_at"]) - opened).total_seconds() / 86400, 1)
        )
        if is_open:
            d["resolved_at"] = None
        out.append(d)
    out.sort(key=lambda d: (d["status"] != "open", d["priority"] or 9, d["opened_at"]))
    return out


def improvements(conn: sqlite3.Connection, window: Period) -> list[dict[str, Any]]:
    """Jira delivery per application/project: work items resolved in `window` and the backlog open at its end."""
    s, e = window.start_iso, window.end_iso
    rows = conn.execute(
        "SELECT COALESCE(a.name, w.project_key) AS app, w.project_key AS project, "
        "SUM(w.resolved >= ? AND w.resolved < ?) AS resolved, "
        "SUM(w.resolved >= ? AND w.resolved < ? AND w.issue_type = 'Story') AS stories, "
        "SUM(w.resolved >= ? AND w.resolved < ? AND w.issue_type = 'Bug') AS bugs, "
        "SUM(CASE WHEN w.resolved >= ? AND w.resolved < ? THEN w.story_points END) AS points, "
        "SUM(w.created < ? AND (w.resolved IS NULL OR w.resolved >= ?)) AS open_at_end "
        "FROM work_item w LEFT JOIN application a ON a.app_id = w.app_id "
        "GROUP BY 1, 2 HAVING resolved > 0 ORDER BY resolved DESC, app",
        [s, e, s, e, s, e, s, e, e, e],
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["points"] = round(r["points"], 1) if r["points"] is not None else None
        out.append(d)
    return out


def work_resolved_count(conn: sqlite3.Connection, window: Period) -> int:
    return int(
        conn.execute(
            "SELECT COUNT(*) FROM work_item WHERE resolved >= ? AND resolved < ?", (window.start_iso, window.end_iso)
        ).fetchone()[0]
    )


def upcoming_changes(conn: sqlite3.Connection, start: datetime, end: datetime) -> list[dict[str, Any]]:
    """Change requests whose planned start lies in `[start, end)` (UTC datetimes), soonest first."""
    from sed.calendar import iso_utc

    rows = conn.execute(
        "SELECT t.number, COALESCE(a.name, '(unattributed)') AS app, t.change_type, t.risk, t.state, t.start_date, "
        "t.end_date, t.short_description FROM ticket t LEFT JOIN application a ON a.app_id = t.app_id "
        "WHERE t.kind = 'change_request' AND t.start_date >= ? AND t.start_date < ? ORDER BY t.start_date, t.number",
        (iso_utc(start), iso_utc(end)),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# portfolio (quarterly)
# ---------------------------------------------------------------------------


def portfolio_health(conn: sqlite3.Connection, window: Period, months: list[str]) -> list[dict[str, Any]]:
    """Applications by business criticality x life-cycle stage: incidents in `window`, license and actual cost."""
    month_clause = f"AND period IN ({_marks(months)})" if months else "AND 0"
    rows = conn.execute(
        "SELECT COALESCE(a.business_criticality, '(unknown)') AS criticality, "
        "COALESCE(a.life_cycle_stage, '(unknown)') AS lifecycle, COUNT(*) AS apps, "
        "SUM(COALESCE(i.opened, 0)) AS incidents, SUM(COALESCE(i.p1p2, 0)) AS p1p2, "
        "SUM(COALESCE(l.cost, 0)) AS license_cost, SUM(COALESCE(c.actual, 0)) AS spend "
        "FROM application a "
        "LEFT JOIN (SELECT app_id, COUNT(*) AS opened, SUM(priority <= 2) AS p1p2 FROM ticket "
        "  WHERE kind = 'incident' AND opened_at >= ? AND opened_at < ? GROUP BY app_id) i ON i.app_id = a.app_id "
        "LEFT JOIN (SELECT app_id, SUM(COALESCE(entitled_qty, 0) * COALESCE(unit_cost_base, 0)) AS cost "
        "  FROM license WHERE is_deleted = 0 GROUP BY app_id) l ON l.app_id = a.app_id "
        "LEFT JOIN (SELECT app_id, SUM(amount_base) AS actual FROM cost_line "
        f"  WHERE line_type = 'actual' {month_clause} GROUP BY app_id) c ON c.app_id = a.app_id "
        "WHERE a.is_deleted = 0 GROUP BY 1, 2 ORDER BY 1, 2",
        [window.start_iso, window.end_iso, *months],
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["license_cost"] = round(r["license_cost"] or 0.0, 2)
        d["spend"] = round(r["spend"] or 0.0, 2)
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# vendor
# ---------------------------------------------------------------------------


def vendor_name(conn: sqlite3.Connection, vendor_id: str) -> str | None:
    row = conn.execute("SELECT name FROM vendor WHERE vendor_id = ?", (vendor_id,)).fetchone()
    return row[0] if row else None


def vendor_contracts(conn: sqlite3.Connection, vendor_id: str, as_of: date) -> list[dict[str, Any]]:
    """Every (non-deleted) contract of the vendor with its term at `as_of`: current, future or ended.

    `counted` marks contracts that are neither terminated nor expired and have not ended before `as_of`.
    """
    rows = conn.execute(
        "SELECT c.contract_id, c.contract_number, COALESCE(a.name, c.app_raw) AS app, c.product, c.start_date, "
        "c.end_date, c.notice_period_days, c.notice_deadline, c.auto_renew, c.renewal_status, c.annual_value_base "
        "FROM contract c LEFT JOIN application a ON a.app_id = c.app_id WHERE c.vendor_id = ? AND c.is_deleted = 0",
        (vendor_id,),
    ).fetchall()
    day = as_of.isoformat()
    out = []
    for r in rows:
        d = dict(r)
        ended = r["end_date"] is not None and r["end_date"] < day
        future = r["start_date"] is not None and r["start_date"] > day
        d["term"] = "ended" if ended else "future" if future else "current"
        d["auto_renew"] = None if r["auto_renew"] is None else ("yes" if r["auto_renew"] else "no")
        d["counted"] = not ended and (r["renewal_status"] or "active") not in INACTIVE_CONTRACT_STATUSES
        d["days_to_end"] = (date.fromisoformat(r["end_date"]) - as_of).days if r["end_date"] else None
        out.append(d)
    order = {"current": 0, "future": 1, "ended": 2}
    out.sort(key=lambda d: (order[d["term"]], d["end_date"] or "9999-12-31", d["contract_number"] or ""))
    return out


def vendor_license_ids(conn: sqlite3.Connection, vendor_id: str) -> set[str]:
    rows = conn.execute("SELECT license_id FROM license WHERE vendor_id = ? AND is_deleted = 0", (vendor_id,))
    return {r[0] for r in rows}


# ---------------------------------------------------------------------------
# rule configuration and findings
# ---------------------------------------------------------------------------


def risk_rule(paths: Paths | None, name: str) -> dict[str, Any]:
    return dict((load_layered("ops/risk_rules.yaml", paths).get("rules") or {}).get(name) or {})


def license_low_threshold(paths: Paths | None) -> float:
    """Utilization below which a license line counts as under-used (risk_rules license_utilization.low)."""
    return float(risk_rule(paths, "license_utilization").get("low", DEFAULT_LICENSE_LOW))


def finding_exposure(finding: dict[str, Any]) -> float | None:
    """Money at stake behind a rule finding, read from its evidence (contract value, idle cost, license or actual
    cost); None when the evidence carries no amount."""
    try:
        evidence = json.loads(finding.get("payload_json") or "{}").get("evidence") or []
    except (TypeError, ValueError):
        return None
    values = {str(e.get("fact_key")): e.get("value") for e in evidence if isinstance(e, dict)}
    for suffix in EXPOSURE_SUFFIXES:
        for key, value in values.items():
            if key.endswith(suffix) and isinstance(value, int | float) and not isinstance(value, bool):
                return round(float(value), 2)
    return None


def risk_rows(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rule findings with their exposure, ordered by severity then exposure (severity x spend)."""
    rows = [{**f, "exposure_eur": finding_exposure(f)} for f in findings]
    rows.sort(
        key=lambda r: (-SEVERITY_ORDER.get(r.get("severity") or "low", 0), -(r["exposure_eur"] or 0.0), r["title"])
    )
    return rows
