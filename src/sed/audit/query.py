"""Reading the audit trail for the Audit page and its workbook (docs/audit.md).

Read-only: a reader never creates or changes the file. Reading the trail is not itself recorded (the owner chose to
record actions, not reads); downloading it is, by the route that hands out the workbook.

Dates in filters are days in the reporting time zone (settings `reporting_tz`), turned into UTC bounds, because every
entry's time is stored in UTC.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sed.audit.record import ACTIONS, audit_path
from sed.audit.store import COLUMNS, connect, verify
from sed.paths import Paths

OUTCOME_WORDS = {"started": "Started", "done": "Done", "failed": "Failed", "refused": "Refused"}
# An attempt whose outcome was never recorded: SED stopped half-way, or it is still running.
OPEN_SQL = (
    "e.outcome = 'started' AND NOT EXISTS (SELECT 1 FROM audit_entry o "
    "WHERE o.correlation_id = e.correlation_id AND o.outcome != 'started')"
)
EXPORT_LIMIT = 100_000


@dataclass(frozen=True)
class AuditFilters:
    actor: str | None = None
    action: str | None = None
    outcome: str | None = None
    since: date | None = None  # first day included
    until: date | None = None  # last day included
    q: str | None = None  # words in the summary, the person or the target
    correlation: str | None = None  # the entries of one action: its start and how it ended


def _utc(day: date, tz: ZoneInfo) -> str:
    return datetime.combine(day, time.min, tzinfo=tz).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _like(text: str) -> str:
    escaped = text.replace("!", "!!").replace("%", "!%").replace("_", "!_")
    return f"%{escaped}%"


def _where(filters: AuditFilters, tz: ZoneInfo) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    for column, value in (
        ("e.actor", filters.actor),
        ("e.action", filters.action),
        ("e.outcome", filters.outcome),
        ("e.correlation_id", filters.correlation),
    ):
        if value:
            clauses.append(f"{column} = ?")
            params.append(value)
    if filters.since:
        clauses.append("e.at >= ?")
        params.append(_utc(filters.since, tz))
    if filters.until:
        clauses.append("e.at < ?")
        params.append(_utc(filters.until + timedelta(days=1), tz))
    if filters.q and filters.q.strip():
        pattern = _like(filters.q.strip())
        clauses.append(
            "(e.summary LIKE ? ESCAPE '!' OR e.actor LIKE ? ESCAPE '!' OR e.actor_name LIKE ? ESCAPE '!' "
            "OR e.target_id LIKE ? ESCAPE '!')"
        )
        params += [pattern] * 4
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


def _entry(row: Any) -> dict[str, Any]:
    data = dict(row)
    return {
        "seq": data["seq"],
        "at": data["at"],
        "actor": data["actor"],
        "actor_name": data["actor_name"],
        "method": data["method"],
        "verified": bool(data["verified"]),
        "channel": data["channel"],
        "action": data["action"],
        "action_label": ACTIONS.get(data["action"], data["action"]),
        "outcome": data["outcome"],
        "target_type": data["target_type"],
        "target_id": data["target_id"],
        "summary": data["summary"],
        "detail": json.loads(data["detail"]) if data["detail"] else {},
        "changes": json.loads(data["changes"]) if data["changes"] else None,
        "correlation_id": data["correlation_id"],
        "open": bool(data.get("open")),
        "prev_hash": data["prev_hash"],
        "entry_hash": data["entry_hash"],
    }


def read_page(
    paths: Paths, filters: AuditFilters, *, tz: ZoneInfo, page: int = 1, page_size: int = 100
) -> dict[str, Any]:
    """Entries newest first, with every person in the trail (to filter by) and the result of the chain check."""
    path = audit_path(paths)
    out: dict[str, Any] = {
        "items": [],
        "total": 0,
        "page": page,
        "page_size": page_size,
        "people": [],
        "actions": [{"key": key, "label": label} for key, label in ACTIONS.items()],
        "integrity": verify(path),
    }
    if not path.is_file():
        return out
    where, params = _where(filters, tz)
    conn = connect(path, readonly=True)
    try:
        conn.execute("BEGIN")  # the count, the page and the people from one snapshot
        try:
            out["total"] = int(conn.execute(f"SELECT COUNT(*) FROM audit_entry e{where}", params).fetchone()[0])
            rows = conn.execute(
                f"SELECT e.seq, {', '.join('e.' + c for c in COLUMNS)}, ({OPEN_SQL}) AS open FROM audit_entry e{where} "
                "ORDER BY e.seq DESC LIMIT ? OFFSET ?",
                [*params, page_size, (page - 1) * page_size],
            ).fetchall()
            people = conn.execute(
                "SELECT actor, MAX(actor_name) AS name, MAX(verified) AS verified FROM audit_entry "
                "GROUP BY actor ORDER BY name COLLATE NOCASE, actor"
            ).fetchall()
        finally:
            conn.execute("COMMIT")
    finally:
        conn.close()
    out["items"] = [_entry(r) for r in rows]
    out["people"] = [{"id": p["actor"], "name": p["name"], "verified": bool(p["verified"])} for p in people]
    return out


def write_workbook(paths: Paths, filters: AuditFilters, *, tz: ZoneInfo) -> tuple[Path, int]:
    """The matching entries, oldest first, as a workbook in the profile's out folder. Each row keeps its fingerprints,
    so a copy kept elsewhere can later show whether the trail was rewritten."""
    import xlsxwriter

    path = audit_path(paths)
    rows: list[dict[str, Any]] = []
    if path.is_file():
        where, params = _where(filters, tz)
        conn = connect(path, readonly=True)
        try:
            found = conn.execute(
                f"SELECT e.seq, {', '.join('e.' + c for c in COLUMNS)}, ({OPEN_SQL}) AS open FROM audit_entry e{where} "
                "ORDER BY e.seq LIMIT ?",
                [*params, EXPORT_LIMIT],
            ).fetchall()
        finally:
            conn.close()
        rows = [_entry(r) for r in found]
    folder = paths.out / "exports"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    suffix = "" if paths.data_class == "real" else f"_{paths.data_class.upper()}"
    target = folder / f"audit_{stamp}{suffix}.xlsx"
    headers = [
        ("Entry", lambda e: e["seq"]),
        ("When (UTC)", lambda e: e["at"]),
        ("Who", lambda e: e["actor"]),
        ("Name", lambda e: e["actor_name"]),
        ("Identified by", lambda e: e["method"]),
        ("Proven", lambda e: "yes" if e["verified"] else "no"),
        ("Where", lambda e: e["channel"].replace("_", " ")),
        ("Action", lambda e: e["action_label"]),
        ("Outcome", lambda e: "no outcome recorded" if e["open"] else OUTCOME_WORDS.get(e["outcome"], e["outcome"])),
        ("What", lambda e: e["summary"]),
        ("Target", lambda e: e["target_id"] or ""),
        ("Details", lambda e: json.dumps(e["detail"], ensure_ascii=False, sort_keys=True) if e["detail"] else ""),
        ("Changes", lambda e: json.dumps(e["changes"], ensure_ascii=False) if e["changes"] else ""),
        ("Action id", lambda e: e["correlation_id"] or ""),
        ("Previous fingerprint", lambda e: e["prev_hash"]),
        ("Fingerprint", lambda e: e["entry_hash"]),
    ]
    book = xlsxwriter.Workbook(
        str(target), {"constant_memory": True, "strings_to_formulas": False, "strings_to_urls": False}
    )
    try:
        sheet = book.add_worksheet("Audit trail")
        bold = book.add_format({"bold": True})
        for col, (title, _) in enumerate(headers):
            sheet.write_string(0, col, title, bold)
        for row_number, entry in enumerate(rows, start=1):
            for col, (_, value) in enumerate(headers):
                cell = value(entry)
                if isinstance(cell, int):
                    sheet.write_number(row_number, col, cell)
                else:
                    sheet.write_string(row_number, col, str(cell))
        sheet.freeze_panes(1, 0)
    finally:
        book.close()
    return target, len(rows)
