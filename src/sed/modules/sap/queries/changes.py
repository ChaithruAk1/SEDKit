"""SAP change and transport read models (ChaRM), derived on read from the raw rows and config/sap/charm.yaml.

Counts cover change documents except requests for change (demand, not changes). Status at a past moment comes from
the status history (the first export showing each status); transports keep only their latest import per system, so a
past moment sees an import only when it happened by then.
"""

from __future__ import annotations

import json
import re
import sqlite3
from bisect import bisect_left
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sed.calendar import Period, iso_utc, parse_utc
from sed.modules.sap.charm import (
    CHANGE_TYPES,
    CLOSED_STAGES,
    JIRA_EXPECTED_TYPES,
    OTHER_TYPE,
    STAGE_LABELS,
    STAGES,
    TYPE_LABELS,
    UNKNOWN_STAGE,
    Charm,
)
from sed.modules.sap.queries import l3
from sed.modules.sap.scope import UNASSIGNED, UNKNOWN

REQUEST = "request"
READY_STAGES = ("ready_for_production", "in_production")
FAILED_LOOKBACK_DAYS = 28


@dataclass
class Change:
    change_id: str
    title: str | None
    transaction_type: str | None
    change_type: str
    status: str | None
    stage: str
    status_since: str | None
    created_at: str | None
    area: str
    landscape: str
    priority: str | None
    jira_keys: set[str] = field(default_factory=set)

    @property
    def is_open(self) -> bool:
        return self.stage not in CLOSED_STAGES


@dataclass(frozen=True)
class Import:
    transport: str
    system_id: str
    change_id: str | None
    role: str | None
    landscape: str
    return_code: int | None
    imported_at: str


@dataclass
class ChangeSet:
    charm: Charm
    at: datetime
    changes: dict[str, Change]
    imports: list[Import]  # imports done by `at`

    def area_of(self, change_id: str | None) -> str:
        change = self.changes.get(change_id or "")
        return change.area if change else UNASSIGNED

    def failed(self, imp: Import) -> bool:
        return imp.return_code is not None and imp.return_code >= self.charm.config.thresholds.failed_return_code


def _days(later: datetime, earlier: str | None) -> float | None:
    return round((later - parse_utc(earlier)).total_seconds() / 86400, 1) if earlier else None


def _matches(pattern: re.Pattern[str], text: str | None) -> list[str]:
    return [m.group(1) if pattern.groups else m.group(0) for m in pattern.finditer(text or "")]


def load(conn: sqlite3.Connection, charm: Charm, at: datetime) -> ChangeSet:
    at_iso = iso_utc(at)
    history: dict[str, list[tuple[str, str]]] = {}
    for change_id, seen_at, status in conn.execute(
        "SELECT change_id, seen_at, status_raw FROM sap_change_status ORDER BY change_id, seen_at, rowid"
    ):
        history.setdefault(change_id, []).append((seen_at, status))

    imports_all = [
        Import(
            transport,
            system_id,
            change_id,
            charm.role_of_system(system_id),
            charm.landscape_of_system(system_id),
            rc,
            imported_at,
        )
        for transport, system_id, change_id, rc, imported_at in conn.execute(
            "SELECT transport, system_id, change_id, return_code, imported_at FROM sap_transport_import "
            "WHERE imported_at IS NOT NULL ORDER BY imported_at, transport, system_id"
        )
    ]
    imports = [i for i in imports_all if i.imported_at <= at_iso]
    landscapes_by_change: dict[str, Counter[str]] = {}
    for imp in imports_all:
        if imp.change_id and imp.landscape != UNKNOWN:
            landscapes_by_change.setdefault(imp.change_id, Counter())[imp.landscape] += 1

    changes: dict[str, Change] = {}
    for row in conn.execute(
        "SELECT change_id, title, transaction_type, status_raw, priority, component_raw, cycle_raw, created_at, "
        "changed_at, external_ref FROM sap_change WHERE created_at IS NULL OR created_at <= ?",
        (at_iso,),
    ):
        change_id, title, ttype, status_raw, priority, component, cycle, created, changed, external_ref = row
        past = [(seen, status) for seen, status in history.get(change_id, []) if seen <= at_iso]
        if past:
            status = past[-1][1]
            since = past[-1][0]
            for seen, earlier in reversed(past[:-1]):
                if earlier.casefold() != status.casefold():
                    break
                since = seen
        elif history.get(change_id):
            status, since = history[change_id][0][1], created
        else:
            status, since = status_raw, changed or created
        counted = landscapes_by_change.get(change_id)
        landscape = charm.cycle_landscape(cycle) or (counted.most_common(1)[0][0] if counted else UNKNOWN)
        change = Change(
            change_id=change_id,
            title=title,
            transaction_type=ttype,
            change_type=charm.change_type(ttype),
            status=status,
            stage=charm.stage(status),
            status_since=since,
            created_at=created,
            area=charm.area(component),
            landscape=landscape,
            priority=priority,
        )
        key_re = re.compile(charm.config.jira.key_pattern)
        projects = set(charm.config.jira.projects)
        change.jira_keys = {
            k for k in _matches(key_re, external_ref) + _matches(key_re, title) if k.split("-")[0] in projects
        }
        changes[change_id] = change

    if charm.config.jira.projects and changes:
        id_re = re.compile(charm.config.jira.change_id_pattern)
        marks = ", ".join("?" for _ in charm.config.jira.projects)
        for issue_key, summary, labels_json in conn.execute(
            f"SELECT issue_key, summary, labels_json FROM work_item WHERE project_key IN ({marks})",
            charm.config.jira.projects,
        ):
            labels = json.loads(labels_json) if labels_json else []
            for change_id in set(_matches(id_re, summary) + [m for lbl in labels for m in _matches(id_re, str(lbl))]):
                if change_id in changes:
                    changes[change_id].jira_keys.add(issue_key)
    return ChangeSet(charm, at, changes, imports)


def select(cs: ChangeSet, *, area: str | None = None, landscape: str | None = None) -> list[Change]:
    """Changes (requests for change excluded) of one area and landscape, newest first."""
    rows = [
        c
        for c in cs.changes.values()
        if c.change_type != REQUEST
        and (area is None or c.area == area)
        and (landscape is None or c.landscape == landscape)
    ]
    return sorted(rows, key=lambda c: (c.created_at or "", c.change_id), reverse=True)


def select_imports(cs: ChangeSet, *, area: str | None = None, landscape: str | None = None) -> list[Import]:
    return [
        i
        for i in cs.imports
        if (area is None or cs.area_of(i.change_id) == area) and (landscape is None or i.landscape == landscape)
    ]


def stage_matrix(changes: list[Change]) -> list[dict[str, Any]]:
    """Open changes by stage (rows) and change type (columns), stages in lifecycle order."""
    types = [t for t in CHANGE_TYPES if t != REQUEST]
    counts: dict[str, Counter[str]] = {}
    for c in changes:
        if c.is_open:
            counts.setdefault(c.stage, Counter())[c.change_type if c.change_type in types else OTHER_TYPE] += 1
    order = [s for s in STAGES if s not in CLOSED_STAGES] + [UNKNOWN_STAGE]
    return [
        {
            "stage": stage,
            "label": STAGE_LABELS[stage],
            **{t: counts[stage][t] for t in [*types, OTHER_TYPE]},
            "total": sum(counts[stage].values()),
        }
        for stage in order
        if stage in counts
    ]


def urgent_ratio(changes: list[Change], start: str, end: str) -> tuple[int, int, float | None]:
    """(created, urgent, urgent %) of the changes created in [start, end)."""
    created = [c for c in changes if c.created_at and start <= c.created_at < end]
    urgent = sum(1 for c in created if c.change_type == "urgent")
    return len(created), urgent, round(100.0 * urgent / len(created), 1) if created else None


def urgent_by_area(cs: ChangeSet, window: list[Period], previous: list[Period], *, landscape: str | None = None):
    """Urgent share of new changes per area: the window weeks against the weeks before."""
    labels = cs.charm.scope.area_labels
    changes = select(cs, landscape=landscape)
    out = []
    for code in l3.area_order(cs.charm.scope):
        mine = [c for c in changes if c.area == code]
        created, urgent, ratio = urgent_ratio(mine, window[0].start_iso, window[-1].end_iso)
        p_created, p_urgent, p_ratio = urgent_ratio(mine, previous[0].start_iso, previous[-1].end_iso)
        if not (created or p_created):
            continue
        out.append(
            {
                "area": code,
                "label": labels[code],
                "created": created,
                "urgent": urgent,
                "ratio_pct": ratio,
                "previous_created": p_created,
                "previous_urgent": p_urgent,
                "previous_ratio_pct": p_ratio,
                "delta_pp": round(ratio - p_ratio, 1) if ratio is not None and p_ratio is not None else None,
            }
        )
    return out


def stuck(cs: ChangeSet, changes: list[Change]) -> list[dict[str, Any]]:
    """Open changes whose status has not changed for longer than the stage allows (longest first)."""
    labels = cs.charm.scope.area_labels
    out = []
    for c in changes:
        limit = cs.charm.stuck_after(c.stage)
        days = _days(cs.at, c.status_since)
        if c.is_open and limit is not None and days is not None and days > limit:
            out.append(
                {
                    **_change_row(cs, c),
                    "status": c.status,
                    "days_in_status": days,
                    "threshold_days": limit,
                    "jira_keys": sorted(c.jira_keys),
                    "area_label": labels[c.area],
                }
            )
    return sorted(out, key=lambda r: (-r["days_in_status"], r["change_id"]))


def _change_row(cs: ChangeSet, c: Change) -> dict[str, Any]:
    return {
        "change_id": c.change_id,
        "title": c.title,
        "change_type": c.change_type,
        "type_label": TYPE_LABELS.get(c.change_type, TYPE_LABELS[OTHER_TYPE]),
        "area": c.area,
        "area_label": cs.charm.scope.area_labels[c.area],
        "landscape": c.landscape,
        "stage": c.stage,
        "stage_label": STAGE_LABELS[c.stage],
        "created_at": c.created_at,
    }


def waiting_for_production(
    cs: ChangeSet, *, area: str | None = None, landscape: str | None = None
) -> list[dict[str, Any]]:
    """Transports of tested changes (ready for or in production) imported into a QA system without errors and not
    into production, longer than allowed after the QA import. Transports of changes still in test count as stuck
    changes instead."""
    limit = cs.charm.config.thresholds.waiting_for_production_days
    by_transport: dict[str, list[Import]] = {}
    for imp in select_imports(cs, area=area, landscape=landscape):
        by_transport.setdefault(imp.transport, []).append(imp)
    out = []
    for transport, rows in by_transport.items():
        qa = [i for i in rows if i.role == "qa" and not cs.failed(i)]
        if not qa or any(i.role == "prod" for i in rows):
            continue
        first = min(qa, key=lambda i: i.imported_at)
        change = cs.changes.get(first.change_id or "")
        if change and change.stage not in READY_STAGES:
            continue
        days = _days(cs.at, first.imported_at)
        if days is not None and days > limit:
            out.append(
                {
                    "transport": transport,
                    "change_id": first.change_id,
                    "title": change.title if change else None,
                    "landscape": first.landscape,
                    "qa_system": first.system_id,
                    "qa_imported_at": first.imported_at,
                    "days_waiting": days,
                }
            )
    return sorted(out, key=lambda r: (-r["days_waiting"], r["transport"]))


def failed_imports(
    cs: ChangeSet, since: str, *, area: str | None = None, landscape: str | None = None
) -> list[dict[str, Any]]:
    """Imports whose latest return code is at or above the failure code, imported at or after `since` (newest first)."""
    out = []
    for imp in select_imports(cs, area=area, landscape=landscape):
        if cs.failed(imp) and imp.imported_at >= since:
            change = cs.changes.get(imp.change_id or "")
            out.append(
                {
                    "transport": imp.transport,
                    "system_id": imp.system_id,
                    "role": imp.role,
                    "landscape": imp.landscape,
                    "return_code": imp.return_code,
                    "imported_at": imp.imported_at,
                    "change_id": imp.change_id,
                    "title": change.title if change else None,
                    "change_type": change.change_type if change else None,
                }
            )
    return sorted(out, key=lambda r: (r["imported_at"], r["transport"], r["system_id"]), reverse=True)


def production_imports(
    cs: ChangeSet, periods: list[Period], *, area: str | None = None, landscape: str | None = None
) -> list[dict[str, Any]]:
    """Imports into production systems per period: imports, failed imports and distinct changes."""
    prod = [i for i in select_imports(cs, area=area, landscape=landscape) if i.role == "prod"]
    out = []
    for p in periods:
        start, end = p.start_iso, p.end_iso
        rows = [i for i in prod if start <= i.imported_at < end]
        out.append(
            {
                "period": p.label,
                "imports": len(rows),
                "failed": sum(1 for i in rows if cs.failed(i)),
                "changes": len({i.change_id for i in rows if i.change_id}),
            }
        )
    return out


def incidents_after_imports(
    conn: sqlite3.Connection,
    cs: ChangeSet,
    start: str,
    end: str,
    *,
    area: str | None = None,
    landscape: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Production imports in [start, end) with the SAP incidents of the same landscape (and the change's area, when
    known) opened within the incident window after the import. A correlation signal, not causation."""
    scope = cs.charm.scope
    if scope.tickets is None:
        scope = scope.resolve(conn)
    hours = cs.charm.config.thresholds.incident_window_hours
    grouped: dict[tuple[str, str], list[Import]] = {}
    for imp in select_imports(cs, area=area, landscape=landscape):
        if imp.role == "prod" and start <= imp.imported_at < end:
            grouped.setdefault((imp.change_id or imp.transport, imp.system_id), []).append(imp)
    if not grouped:
        return []
    horizon = iso_utc(parse_utc(max(i.imported_at for rows in grouped.values() for i in rows)) + timedelta(hours=hours))
    where, params = l3.filters(scope).where()
    tickets = {t[0]: t for t in scope.tickets or ()}
    by_place: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for ticket_id, number, opened_at in conn.execute(
        f"SELECT t.ticket_id, t.number, t.opened_at FROM ticket t WHERE {where} "
        "AND t.opened_at >= ? AND t.opened_at < ? ORDER BY t.opened_at, t.number",
        [*params, start, horizon],
    ):
        _, group, app_id = tickets[ticket_id]
        place = (scope.area_of(group), scope.app_landscape.get(app_id or "", UNKNOWN))
        by_place.setdefault(place, []).append((opened_at, number))
    out = []
    for (_ref, system_id), rows in grouped.items():
        first = min(i.imported_at for i in rows)
        until = iso_utc(parse_utc(first) + timedelta(hours=hours))
        change = cs.changes.get(rows[0].change_id or "")
        change_area = change.area if change else UNASSIGNED
        lands = rows[0].landscape
        candidates: list[tuple[str, str]] = []
        for (t_area, t_land), items in by_place.items():
            if t_land == lands and (change_area == UNASSIGNED or t_area == change_area):
                candidates += items
        candidates.sort()
        lo = bisect_left(candidates, (first, ""))
        hi = bisect_left(candidates, (until, ""))
        numbers = [n for _, n in candidates[lo:hi]]
        out.append(
            {
                "change_id": rows[0].change_id,
                "transports": len(rows),
                "title": change.title if change else None,
                "change_type": change.change_type if change else None,
                "area": change_area,
                "area_label": scope.area_labels[change_area],
                "landscape": lands,
                "system_id": system_id,
                "imported_at": first,
                "return_code": max((i.return_code for i in rows if i.return_code is not None), default=None),
                "incidents": len(numbers),
                "numbers": numbers[:5],
            }
        )
    out.sort(key=lambda r: (-r["incidents"], r["imported_at"]), reverse=False)
    return [r for r in out if r["incidents"]][:limit]


def without_jira(cs: ChangeSet, changes: list[Change]) -> list[dict[str, Any]]:
    """Open changes of the types that should trace to a Jira story but have no link either way (newest first)."""
    return [
        _change_row(cs, c) for c in changes if c.is_open and c.change_type in JIRA_EXPECTED_TYPES and not c.jira_keys
    ]


def summary(
    conn: sqlite3.Connection, cs: ChangeSet, week: Period, *, area: str | None = None, landscape: str | None = None
) -> dict[str, Any]:
    """Headline change figures: open, urgent share over the 8 weeks ending with `week` (and the 8 before), stuck,
    without Jira, failed imports in the last 28 days, transports waiting for production, production imports in
    `week`."""
    selected = select(cs, area=area, landscape=landscape)
    window = [week.previous(k) for k in range(7, -1, -1)]
    previous = [window[0].previous(k) for k in range(8, 0, -1)]
    created, urgent, ratio = urgent_ratio(selected, window[0].start_iso, window[-1].end_iso)
    _, _, previous_ratio = urgent_ratio(selected, previous[0].start_iso, previous[-1].end_iso)
    since = iso_utc(cs.at - timedelta(days=FAILED_LOOKBACK_DAYS))
    return {
        "open": sum(1 for c in selected if c.is_open),
        "created_8w": created,
        "urgent_8w": urgent,
        "urgent_ratio_8w": ratio,
        "urgent_ratio_previous_8w": previous_ratio,
        "stuck": len(stuck(cs, selected)),
        "without_jira": len(without_jira(cs, selected)),
        "failed_4w": len(failed_imports(cs, since, area=area, landscape=landscape)),
        "waiting": len(waiting_for_production(cs, area=area, landscape=landscape)),
        "prod_imports_week": production_imports(cs, [week], area=area, landscape=landscape)[0]["imports"],
    }
