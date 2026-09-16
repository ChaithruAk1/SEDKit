"""SAP L3 support read models: SAP tickets are ops tickets in the SAP scope (sed.modules.sap.scope).

Every number comes from the ops metric functions (sed.metrics) with the scope as an extra ticket predicate, so SAP
figures follow the portfolio definitions exactly: SLA source order, backlog excluding stale tickets, calendar-hour MTTR.
Breakdowns by area are aggregated from the per-group results; landscapes are filtered by application.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from sed import metrics
from sed.calendar import Period
from sed.modules.sap.scope import UNASSIGNED, UNKNOWN, Scope

AGING = (("0-7d", "d0_7"), ("8-30d", "d8_30"), ("31-90d", "d31_90"), (">90d", "d90p"))


def filters(
    scope: Scope,
    *,
    area: str | None = None,
    landscape: str | None = None,
    priorities: list[int] | None = None,
    kind: str = "incident",
) -> metrics.Filters:
    sql, params = scope.ticket_sql(area=area, landscape=landscape)
    return metrics.Filters(
        kind=kind,
        priorities=list(priorities or []),
        scope_sql=sql,
        scope_params=params,
        scope_drives=scope.tickets is not None,
    )


def _avg(values: list[float | None]) -> float | None:
    vals = [v for v in values if v is not None]
    return round(sum(vals) / len(vals), 2) if vals else None


def area_order(scope: Scope) -> list[str]:
    return [a.code for a in scope.config.areas] + [UNASSIGNED]


def landscape_order(scope: Scope) -> list[str]:
    return [x.code for x in scope.config.landscapes] + [UNKNOWN]


def trend(
    conn: sqlite3.Connection,
    scope: Scope,
    periods: list[Period],
    source: str,
    *,
    area: str | None = None,
    landscape: str | None = None,
) -> list[dict[str, Any]]:
    """Opened, resolved, net and SLA % per period."""
    f = filters(scope, area=area, landscape=landscape)
    rows = metrics.volume_trend(conn, f, periods)
    for row, period in zip(rows, periods, strict=True):
        row["sla_pct"] = metrics.sla(conn, f, period, source)["pct"]
    return rows


def backlog(
    conn: sqlite3.Connection, scope: Scope, at: datetime, *, area: str | None = None, landscape: str | None = None
) -> dict[str, Any]:
    """Open SAP tickets at `at` (stale tickets excluded): total, aging and per-area aging (areas in config order)."""
    result = metrics.backlog(conn, filters(scope, area=area, landscape=landscape), at)
    by_area: dict[str, dict[str, int]] = {}
    for group, buckets in result["by_group"].items():
        acc = by_area.setdefault(scope.area_of(None if group == "(unassigned)" else group), _empty_aging())
        acc["total"] += buckets["total"]
        for src, dst in AGING:
            acc[dst] += buckets[src]
    labels = scope.area_labels
    return {
        "total": result["total"],
        "aging": {dst: result["aging"][src] for src, dst in AGING},
        "by_area": [
            {"area": code, "label": labels[code], **by_area[code]} for code in area_order(scope) if code in by_area
        ],
    }


def _empty_aging() -> dict[str, int]:
    return {"total": 0, **{dst: 0 for _, dst in AGING}}


def backlog_by_landscape(
    conn: sqlite3.Connection, scope: Scope, at: datetime, *, area: str | None = None
) -> list[dict[str, Any]]:
    labels = scope.landscape_labels
    out = []
    for code in landscape_order(scope):
        total = metrics.backlog(conn, filters(scope, area=area, landscape=code), at)["total"]
        if total or code != UNKNOWN:
            out.append({"landscape": code, "label": labels[code], "open": total})
    return out


def flow_by_area(
    conn: sqlite3.Connection, scope: Scope, periods: list[Period], *, landscape: str | None = None
) -> list[dict[str, Any]]:
    """Arrivals and closures per period per area (periods oldest first, areas in config order)."""
    acc: dict[tuple[str, str], list[int]] = {}
    for row in metrics.group_flow(conn, filters(scope, landscape=landscape), periods):
        slot = acc.setdefault((row["period"], scope.area_of(row["group"])), [0, 0])
        slot[0] += row["arrived"]
        slot[1] += row["closed"]
    labels = scope.area_labels
    return [
        {"period": p.label, "area": code, "label": labels[code], "arrived": a, "closed": c, "net": a - c}
        for p in periods
        for code in area_order(scope)
        if (p.label, code) in acc
        for a, c in [acc[(p.label, code)]]
    ]


def area_summary(
    conn: sqlite3.Connection, scope: Scope, period: Period, at: datetime, source: str
) -> list[dict[str, Any]]:
    """Per area: open and aged (> 30 days) at `at`, opened/resolved and SLA % in `period`."""
    open_by_area = {r["area"]: r for r in backlog(conn, scope, at)["by_area"]}
    flow = {r["area"]: r for r in flow_by_area(conn, scope, [period])}
    labels = scope.area_labels
    out = []
    for code in area_order(scope):
        opened = flow.get(code, {}).get("arrived", 0)
        resolved = flow.get(code, {}).get("closed", 0)
        open_row = open_by_area.get(code, _empty_aging())
        if code == UNASSIGNED and not (opened or resolved or open_row["total"]):
            continue
        out.append(
            {
                "area": code,
                "label": labels[code],
                "open": open_row["total"],
                "aged_30d": open_row["d31_90"] + open_row["d90p"],
                "opened": opened,
                "resolved": resolved,
                "sla_pct": metrics.sla(conn, filters(scope, area=code), period, source)["pct"] if resolved else None,
            }
        )
    return out


def attention(
    conn: sqlite3.Connection,
    scope: Scope,
    at: datetime,
    thresholds: Any,
    *,
    area: str | None = None,
    landscape: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Open SAP incidents needing attention (same reasons as the ops attention list), with their area; no assignees."""
    result = metrics.attention(conn, filters(scope, area=area, landscape=landscape), at, thresholds, limit)
    labels = scope.area_labels
    items = []
    for item in result["items"]:
        code = scope.area_of(item["assignment_group"])
        row = {k: v for k, v in item.items() if k not in ("assigned_to_pid", "reassignment_count", "reopen_count")}
        items.append({**row, "ticket_id": f"incident:{item['number']}", "area": code, "area_label": labels[code]})
    return {"count": result["count"], "items": items}


def week_kpis(
    conn: sqlite3.Connection, scope: Scope, week: Period, previous: list[Period], source: str
) -> dict[str, Any]:
    """Opened, resolved, SLA % and MTTR median for `week`, with the averages of the `previous` weeks."""
    f = filters(scope)
    vol = metrics.volume_trend(conn, f, [*previous, week])
    now = metrics.sla(conn, f, week, source)["pct"]
    mttr_now = metrics.mttr(conn, f, week)["median_h"]
    return {
        "opened": vol[-1]["opened"],
        "opened_avg": _avg([v["opened"] for v in vol[:-1]]),
        "resolved": vol[-1]["resolved"],
        "resolved_avg": _avg([v["resolved"] for v in vol[:-1]]),
        "sla_pct": now,
        "sla_pct_avg": _avg([metrics.sla(conn, f, p, source)["pct"] for p in previous]),
        "mttr_median_h": mttr_now,
        "mttr_median_h_avg": _avg([metrics.mttr(conn, f, p)["median_h"] for p in previous]),
    }
