"""SAP IDoc health read models, derived on read from sap_idoc, its status history and config/sap/idoc.yaml.

IDoc health works on error episodes: an IDoc's first error status (from the history) up to its next processed (ok)
status. A persistent error is one not processed within thresholds.reprocess_grace_hours: quick reprocessing is normal
operation and does not count towards growth or spikes. Status at a past moment comes from the history, so exports only
show the status each IDoc had when the export ran.
"""

from __future__ import annotations

import sqlite3
import statistics
from bisect import bisect_left
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sed.calendar import Period, iso_utc, parse_utc
from sed.modules.sap.idoc import UNKNOWN_GROUP, Idoc, normalise_text
from sed.modules.sap.queries.changes import ChangeSet, select_imports
from sed.modules.sap.scope import UNKNOWN

AGING = (("lt4h", 4), ("h4_24", 24), ("d1_2", 48), ("d2_7", 168), ("gt7d", None))


@dataclass
class ErrorIdoc:
    system_id: str
    docnum: str
    direction: str | None
    message_type: str | None
    basic_type: str | None
    partner: str | None
    created_at: str | None
    first_error_at: str
    first_error_code: str
    error_text: str | None
    reprocessed_at: str | None  # first processed (ok) status after the first error, by `at`
    status_code: str
    group: str
    area: str
    landscape: str

    @property
    def is_open(self) -> bool:
        return self.group == "error"


@dataclass
class IdocSet:
    idoc: Idoc
    at: datetime
    errors: list[ErrorIdoc]

    @property
    def grace(self) -> timedelta:
        return timedelta(hours=self.idoc.config.thresholds.reprocess_grace_hours)

    def age_hours(self, e: ErrorIdoc) -> float:
        return round((self.at - parse_utc(e.first_error_at)).total_seconds() / 3600, 1)

    def persistent(self, e: ErrorIdoc) -> bool:
        """Not processed within the grace time: reprocessed later, closed, or still open after the grace time."""
        first = parse_utc(e.first_error_at)
        if e.reprocessed_at is not None:
            return parse_utc(e.reprocessed_at) - first > self.grace
        return self.at - first > self.grace


def load(conn: sqlite3.Connection, idoc: Idoc, at: datetime) -> IdocSet:
    """Every IDoc (created within thresholds.history_days) that had an error status by `at`, with its episode."""
    at_iso = iso_utc(at)
    since = iso_utc(at - timedelta(days=idoc.config.thresholds.history_days))
    error_codes = idoc.codes("error")
    if not error_codes:
        return IdocSet(idoc, at, [])
    marks = ", ".join("?" for _ in error_codes)
    rows = conn.execute(
        "SELECT h.system_id, h.docnum, h.status_at, h.status_code, h.status_text, i.direction, i.message_type, "
        "i.basic_type, i.partner_number, i.created_at FROM sap_idoc_status h "
        "JOIN sap_idoc i ON i.system_id = h.system_id AND i.docnum = h.docnum "
        "WHERE (h.system_id, h.docnum) IN (SELECT system_id, docnum FROM sap_idoc_status "
        f"WHERE status_code IN ({marks}) AND status_at <= ?) "
        "AND h.status_at <= ? AND (i.created_at IS NULL OR i.created_at >= ?) "
        "ORDER BY h.system_id, h.docnum, h.status_at, h.rowid",
        [*error_codes, at_iso, at_iso, since],
    ).fetchall()
    episodes: dict[tuple[str, str], list[Any]] = {}
    for row in rows:
        episodes.setdefault((row[0], row[1]), []).append(row)
    landscapes = {s.sid: s.landscape for s in idoc.scope.config.systems}
    errors = []
    for (system_id, docnum), history in episodes.items():
        first = next(r for r in history if idoc.group(r[3]) == "error")
        after = [r for r in history if r[2] > first[2]]
        reprocessed = next((r[2] for r in after if idoc.group(r[3]) == "ok"), None)
        last = history[-1]
        texts = [r[4] for r in history if idoc.group(r[3]) == "error" and r[4]]
        _, _, _, _, _, direction, message_type, basic_type, partner, created = first
        errors.append(
            ErrorIdoc(
                system_id=system_id,
                docnum=docnum,
                direction=direction,
                message_type=message_type,
                basic_type=basic_type,
                partner=partner,
                created_at=created,
                first_error_at=first[2],
                first_error_code=first[3],
                error_text=texts[-1] if texts else None,
                reprocessed_at=reprocessed,
                status_code=last[3],
                group=idoc.group(last[3]),
                area=idoc.area(message_type),
                landscape=landscapes.get(system_id, UNKNOWN),
            )
        )
    return IdocSet(idoc, at, errors)


def select(
    ids: IdocSet,
    *,
    system: str | None = None,
    landscape: str | None = None,
    area: str | None = None,
    direction: str | None = None,
) -> list[ErrorIdoc]:
    return [
        e
        for e in ids.errors
        if (system is None or e.system_id == system)
        and (landscape is None or e.landscape == landscape)
        and (area is None or e.area == area)
        and (direction is None or e.direction == direction)
    ]


def aging(ids: IdocSet, errors: list[ErrorIdoc]) -> dict[str, int]:
    """Open errors by age since the first error."""
    out = {key: 0 for key, _ in AGING}
    for e in errors:
        if e.is_open:
            hours = ids.age_hours(e)
            out[next(key for key, limit in AGING if limit is None or hours < limit)] += 1
    return out


def backlog_by_type(ids: IdocSet, errors: list[ErrorIdoc]) -> list[dict[str, Any]]:
    """Open errors per system, message type and direction (most first)."""
    aged_h = ids.idoc.config.thresholds.aged_error_hours
    groups: dict[tuple[str, str | None, str | None], list[ErrorIdoc]] = {}
    for e in errors:
        if e.is_open:
            groups.setdefault((e.system_id, e.message_type, e.direction), []).append(e)
    rows = [
        {
            "system_id": system_id,
            "landscape": items[0].landscape,
            "message_type": message_type,
            "direction": direction,
            "area": items[0].area,
            "errors": len(items),
            "aged": sum(1 for e in items if ids.age_hours(e) > aged_h),
            "partners": len({e.partner for e in items}),
            "oldest_hours": max(ids.age_hours(e) for e in items),
        }
        for (system_id, message_type, direction), items in groups.items()
    ]
    return sorted(rows, key=lambda r: (-r["errors"], r["system_id"], r["message_type"] or ""))


def partners(ids: IdocSet, errors: list[ErrorIdoc], limit: int = 20) -> list[dict[str, Any]]:
    """Open errors per system, message type and partner (most first)."""
    counts = Counter((e.system_id, e.message_type, e.partner) for e in errors if e.is_open)
    oldest: dict[tuple[str, str | None, str | None], float] = {}
    for e in errors:
        if e.is_open:
            key = (e.system_id, e.message_type, e.partner)
            oldest[key] = max(oldest.get(key, 0.0), ids.age_hours(e))
    return [
        {"system_id": s, "message_type": m, "partner": p, "errors": n, "oldest_hours": oldest[(s, m, p)]}
        for (s, m, p), n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0][0], kv[0][1] or "", kv[0][2] or ""))
    ][:limit]


def weekly(
    conn: sqlite3.Connection,
    ids: IdocSet,
    errors: list[ErrorIdoc],
    periods: list[Period],
    *,
    system: str | None = None,
    landscape: str | None = None,
    area: str | None = None,
    direction: str | None = None,
) -> list[dict[str, Any]]:
    """Per period: IDocs created, new errors, new persistent errors, reprocessed errors and their median hours."""
    out = []
    created = _created_counts(conn, ids, periods, system=system, landscape=landscape, area=area, direction=direction)
    for p, idocs in zip(periods, created, strict=True):
        start, end = p.start_iso, p.end_iso
        new = [e for e in errors if start <= e.first_error_at < end]
        fixed = [e for e in errors if e.reprocessed_at and start <= e.reprocessed_at < end]
        hours = [(parse_utc(e.reprocessed_at) - parse_utc(e.first_error_at)).total_seconds() / 3600 for e in fixed]  # type: ignore[arg-type]
        out.append(
            {
                "period": p.label,
                "idocs": idocs,
                "new_errors": len(new),
                "persistent": sum(1 for e in new if ids.persistent(e)),
                "reprocessed": len(fixed),
                "reprocess_median_h": round(statistics.median(hours), 1) if hours else None,
            }
        )
    return out


def _created_counts(
    conn: sqlite3.Connection,
    ids: IdocSet,
    periods: list[Period],
    *,
    system: str | None,
    landscape: str | None,
    area: str | None,
    direction: str | None,
) -> list[int]:
    clauses, params = ["created_at >= ?", "created_at < ?"], [periods[0].start_iso, periods[-1].end_iso]
    if system:
        clauses.append("system_id = ?")
        params.append(system)
    if landscape:
        sids = [s.sid for s in ids.idoc.scope.config.systems if s.landscape == landscape]
        clauses.append(f"system_id IN ({', '.join('?' for _ in sids) or 'NULL'})")
        params += sids
    if direction:
        clauses.append("direction = ?")
        params.append(direction)
    counts = [0] * len(periods)
    bounds = [(p.start_iso, p.end_iso) for p in periods]
    for created_at, message_type in conn.execute(
        f"SELECT created_at, message_type FROM sap_idoc WHERE {' AND '.join(clauses)}", params
    ):
        if area and ids.idoc.area(message_type) != area:
            continue
        for i, (start, end) in enumerate(bounds):
            if start <= created_at < end:
                counts[i] += 1
    return counts


def reprocess_stats(ids: IdocSet, errors: list[ErrorIdoc], period: Period) -> dict[str, Any]:
    """Errors reprocessed in `period`: count, median and 90th percentile hours, share within the grace time."""
    fixed = [e for e in errors if e.reprocessed_at and period.start_iso <= e.reprocessed_at < period.end_iso]
    hours = sorted((parse_utc(e.reprocessed_at) - parse_utc(e.first_error_at)).total_seconds() / 3600 for e in fixed)  # type: ignore[arg-type]
    grace = ids.idoc.config.thresholds.reprocess_grace_hours
    return {
        "reprocessed": len(hours),
        "median_h": round(statistics.median(hours), 1) if hours else None,
        "p90_h": round(hours[min(len(hours) - 1, round(0.9 * (len(hours) - 1)))], 1) if hours else None,
        "within_grace_pct": round(100.0 * sum(1 for h in hours if h <= grace) / len(hours), 1) if hours else None,
    }


def top_texts(errors: list[ErrorIdoc], limit: int = 10) -> list[dict[str, Any]]:
    """The most frequent open error texts, numbers and ids stripped."""
    counts: Counter[str] = Counter()
    types: dict[str, set[str]] = {}
    for e in errors:
        if e.is_open:
            text = normalise_text(e.error_text) or f"status {e.status_code}"
            counts[text] += 1
            types.setdefault(text, set()).add(e.message_type or "?")
    return [
        {"text": text, "errors": n, "message_types": sorted(types[text])}
        for text, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
    ]


def open_errors(ids: IdocSet, errors: list[ErrorIdoc], limit: int = 100) -> list[dict[str, Any]]:
    """Open error IDocs, oldest first."""
    rows = sorted((e for e in errors if e.is_open), key=lambda e: (e.first_error_at, e.system_id, e.docnum))
    return [
        {
            "system_id": e.system_id,
            "docnum": e.docnum,
            "direction": e.direction,
            "message_type": e.message_type,
            "partner": e.partner,
            "status_code": e.status_code,
            "text": e.error_text,
            "first_error_at": e.first_error_at,
            "age_hours": ids.age_hours(e),
        }
        for e in rows[:limit]
    ]


def spikes_after_imports(
    ids: IdocSet,
    cs: ChangeSet,
    start: str,
    end: str,
    *,
    system: str | None = None,
    landscape: str | None = None,
    min_lift: int | None = -1,
) -> list[dict[str, Any]]:
    """Production imports in [start, end) with the persistent IDoc errors of the same system in the spike window after
    the import against the same window before it. Rows below `min_lift` are left out (default thresholds.spike_min_lift;
    None keeps every import followed by errors); highest lift first."""
    if min_lift == -1:
        min_lift = ids.idoc.config.thresholds.spike_min_lift
    window = timedelta(hours=ids.idoc.config.thresholds.spike_window_hours)
    by_system: dict[str, list[str]] = {}
    for e in ids.errors:
        if ids.persistent(e):
            by_system.setdefault(e.system_id, []).append(e.first_error_at)
    for times in by_system.values():
        times.sort()
    grouped: dict[tuple[str, str], list[Any]] = {}
    for imp in select_imports(cs, landscape=landscape):
        if imp.role == "prod" and start <= imp.imported_at < end and (system is None or imp.system_id == system):
            grouped.setdefault((imp.change_id or imp.transport, imp.system_id), []).append(imp)
    out = []
    for (_ref, system_id), rows in grouped.items():
        first = min(i.imported_at for i in rows)
        moment = parse_utc(first)
        times = by_system.get(system_id, [])
        after = bisect_left(times, iso_utc(moment + window)) - bisect_left(times, first)
        before = bisect_left(times, first) - bisect_left(times, iso_utc(moment - window))
        change = cs.changes.get(rows[0].change_id or "")
        out.append(
            {
                "change_id": rows[0].change_id,
                "title": change.title if change else None,
                "change_type": change.change_type if change else None,
                "system_id": system_id,
                "imported_at": first,
                "return_code": max((i.return_code for i in rows if i.return_code is not None), default=None),
                "errors": after,
                "errors_before": before,
                "lift": after - before,
            }
        )
    out.sort(key=lambda r: (-r["lift"], -r["errors"], r["imported_at"]))
    return [r for r in out if r["errors"] and (min_lift is None or r["lift"] >= min_lift)]


def summary(
    ids: IdocSet,
    week: Period,
    *,
    system: str | None = None,
    landscape: str | None = None,
    area: str | None = None,
    direction: str | None = None,
) -> dict[str, Any]:
    """Headline IDoc figures: open and aged errors at `ids.at`; new persistent errors in `week` with the average of the
    4 weeks before; reprocessing in `week`."""
    errors = select(ids, system=system, landscape=landscape, area=area, direction=direction)
    aged_h = ids.idoc.config.thresholds.aged_error_hours
    previous = [week.previous(k) for k in range(4, 0, -1)]

    def persistent_in(p: Period) -> int:
        return sum(1 for e in errors if p.start_iso <= e.first_error_at < p.end_iso and ids.persistent(e))

    before = [persistent_in(p) for p in previous]
    stats = reprocess_stats(ids, errors, week)
    unknown = sum(1 for e in errors if e.group == UNKNOWN_GROUP)
    return {
        "errors_open": sum(1 for e in errors if e.is_open),
        "errors_aged": sum(1 for e in errors if e.is_open and ids.age_hours(e) > aged_h),
        "new_persistent_week": persistent_in(week),
        "new_persistent_avg4w": round(sum(before) / len(before), 2),
        "reprocess_median_h": stats["median_h"],
        "reprocessed_within_grace_pct": stats["within_grace_pct"],
        "unknown_status": unknown,
    }
