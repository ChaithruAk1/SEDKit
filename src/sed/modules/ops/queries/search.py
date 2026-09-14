"""Ticket search and detail: FTS5 over scrubbed short_description/description, filters, sort whitelist, pagination.

User text is untrusted: `fts_query` turns it into quoted phrases, so FTS5 operators (AND, OR, NOT, NEAR, parentheses,
column filters, quotes) are matched as literal words. Only a trailing `*` survives, as a prefix operator.

The page is selected first (ids only, with filters, sort, LIMIT/OFFSET); application, vendor and AI label columns are
joined for those rows only.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from sed.errors import ValidationFailed
from sed.modules.ops.api_models import LabelOut, TicketDetail, TicketPage, TicketRow
from sed.modules.ops.queries.common import Context, marks, ticket_filter_sql, where_sql

MAX_TOKENS = 10
SORTS = {
    "opened_desc": "t.opened_at DESC, t.rowid DESC",
    "opened_asc": "t.opened_at ASC, t.rowid ASC",
    "priority": "t.priority IS NULL, t.priority ASC, t.opened_at DESC, t.rowid DESC",
    "updated_desc": "t.sys_updated_on DESC, t.rowid DESC",
}
APPROVED = ("approved",)
WITH_DRAFTS = ("approved", "completed")
CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def fts_query(text: str | None) -> str | None:
    """Sanitise free text into an FTS5 query: at most 10 whitespace tokens, each a double-quoted phrase (inner quotes
    doubled) combined with implicit AND; a trailing `*` is kept as a prefix operator. None when nothing is left."""
    if not text:
        return None
    parts = []
    text = CONTROL_CHARS.sub(" ", text)  # FTS5 cannot parse a NUL inside a phrase
    for token in text.split()[:MAX_TOKENS]:
        prefix = token.endswith("*")
        core = token.rstrip("*")
        if not core:
            continue
        parts.append('"' + core.replace('"', '""') + '"' + ("*" if prefix else ""))
    return " ".join(parts) or None


def label_statuses(include_drafts: bool) -> tuple[str, ...]:
    return WITH_DRAFTS if include_drafts else APPROVED


def current_label_sql(statuses: tuple[str, ...], alias: str = "t") -> str:
    """Correlated subquery selecting the ai_ticket_label rowid shown for a ticket (the v_ticket rule: resolved stage
    before open stage, manual runs first, then the latest run; only labels computed on the current content hash)."""
    return (
        "(SELECT l2.rowid FROM ai_ticket_label l2 JOIN ai_run r2 ON r2.run_id = l2.run_id "
        f"AND r2.status IN ({', '.join(repr(s) for s in statuses)}) "
        f"WHERE l2.ticket_id = {alias}.ticket_id "
        f"AND l2.input_hash = CASE l2.stage WHEN 'open' THEN {alias}.open_hash ELSE {alias}.resolved_hash END "
        "ORDER BY CASE l2.stage WHEN 'resolved' THEN 0 ELSE 1 END, CASE WHEN r2.skill = 'manual' THEN 0 ELSE 1 END, "
        "r2.run_seq DESC LIMIT 1)"
    )


ROW_COLUMNS = (
    "t.ticket_id, t.number, t.kind, t.priority, t.state, t.is_open, t.stale_open, t.app_id, a.name AS app_name, "
    "v.name AS vendor_name, t.assignment_group, t.opened_at, t.resolved_at, t.short_description, "
    "t.category AS sn_category, l.am_category, l.am_subcategory, l.confidence AS label_confidence, "
    "l.run_id AS label_run_id, lr.status AS label_run_status"
)


def _row_from(r: sqlite3.Row) -> dict[str, Any]:
    return {
        "ticket_id": r["ticket_id"],
        "number": r["number"],
        "kind": r["kind"],
        "priority": r["priority"],
        "state": r["state"],
        "is_open": bool(r["is_open"]),
        "stale_open": bool(r["stale_open"]),
        "app_id": r["app_id"],
        "app_name": r["app_name"],
        "vendor_name": r["vendor_name"],
        "assignment_group": r["assignment_group"],
        "opened_at": r["opened_at"],
        "resolved_at": r["resolved_at"],
        "short_description": r["short_description"],
        "sn_category": r["sn_category"],
        "am_category": r["am_category"],
        "am_subcategory": r["am_subcategory"],
        "label_confidence": r["label_confidence"],
        "label_run_id": r["label_run_id"],
        "label_run_status": r["label_run_status"],
    }


def rows_for(
    conn: sqlite3.Connection, rowids: list[int], statuses: tuple[str, ...], extra_columns: str = ""
) -> list[sqlite3.Row]:
    """Ticket rows with app, vendor and current-label columns for the given rowids, in the given order."""
    if not rowids:
        return []
    extra = f", {extra_columns}" if extra_columns else ""
    sql = (
        f"SELECT t.rowid AS _rowid, {ROW_COLUMNS}{extra} FROM ticket t "
        "LEFT JOIN application a ON a.app_id = t.app_id LEFT JOIN vendor v ON v.vendor_id = t.vendor_id "
        f"LEFT JOIN ai_ticket_label l ON l.rowid = {current_label_sql(statuses)} "
        "LEFT JOIN ai_run lr ON lr.run_id = l.run_id "
        f"WHERE t.rowid IN ({marks(rowids)})"
    )
    by_id = {r["_rowid"]: r for r in conn.execute(sql, rowids)}
    return [by_id[i] for i in rowids if i in by_id]


def ticket_rows(conn: sqlite3.Connection, rowids: list[int], include_drafts: bool = False) -> list[TicketRow]:
    return [TicketRow(**_row_from(r)) for r in rows_for(conn, rowids, label_statuses(include_drafts))]


def search(
    ctx: Context,
    *,
    q: str | None,
    kind: str | None,
    priority: list[int],
    state: str | None,
    is_open: bool | None,
    stale: bool | None,
    sn_category: str | None,
    am_category: str | None,
    sort: str,
    page: int,
    page_size: int,
) -> TicketPage:
    f = ctx.filters
    if sort not in SORTS:
        raise ValidationFailed(f"Unknown sort '{sort}'", {"allowed": sorted(SORTS)})
    clauses, params = ticket_filter_sql(f)
    for column, value in (("t.kind", kind), ("t.state", state), ("t.category", sn_category)):
        if value is not None:
            clauses.append(f"{column} = ?")
            params.append(value)
    if priority:
        clauses.append(f"t.priority IN ({marks(priority)})")
        params += list(priority)
    if is_open is not None:
        clauses.append("t.is_open = ?")
        params.append(1 if is_open else 0)
    if stale is not None:
        clauses.append("t.stale_open = ?")
        params.append(1 if stale else 0)
    if f.period:
        period = ctx.parse(f.period)
        clauses.append("t.opened_at >= ? AND t.opened_at < ?")
        params += [period.start_iso, period.end_iso]
    if f.as_of:
        clauses.append("t.opened_at < ?")
        params.append(ctx.as_of_end_iso)
    statuses = label_statuses(f.include_drafts)
    if am_category is not None:
        clauses.append(
            "t.ticket_id IN (SELECT ticket_id FROM ai_ticket_label WHERE am_category = ?) AND "
            f"(SELECT am_category FROM ai_ticket_label WHERE rowid = {current_label_sql(statuses)}) = ?"
        )
        params += [am_category, am_category]

    match = fts_query(q)
    source = "ticket t"
    if match is not None:
        source = "ticket_fts JOIN ticket t ON t.rowid = ticket_fts.rowid"
        clauses.insert(0, "ticket_fts MATCH ?")
        params.insert(0, match)
    where = where_sql(clauses)
    try:
        total = int(ctx.conn.execute(f"SELECT COUNT(*) FROM {source} WHERE {where}", params).fetchone()[0])
        offset = (page - 1) * page_size
        ids = [
            r[0]
            for r in ctx.conn.execute(
                f"SELECT t.rowid FROM {source} WHERE {where} ORDER BY {SORTS[sort]} LIMIT ? OFFSET ?",
                [*params, page_size, offset],
            )
        ]
    except sqlite3.OperationalError as exc:
        message = str(exc).lower()
        if match is not None and "locked" not in message and "busy" not in message:
            raise ValidationFailed("Search text could not be parsed", {"q": q}) from exc
        raise
    return TicketPage(page=page, page_size=page_size, total=total, items=ticket_rows(ctx.conn, ids, f.include_drafts))


DETAIL_COLUMNS = (
    "t.description, t.close_code, t.close_notes, t.closed_at, t.sys_updated_on, t.caller_pid, t.assigned_to_pid, "
    "t.cmdb_ci_raw, t.business_service_raw, t.problem_id, t.caused_by, t.parent_incident, t.reassignment_count, "
    "t.reopen_count, t.made_sla"
)


def detail(conn: sqlite3.Connection, ticket_id: str, include_drafts: bool = False) -> TicketDetail | None:
    """One ticket with every AI label that may be shown: labels on the ticket's current content hash from approved runs,
    plus completed (unreviewed) runs when include_drafts is set. Rejected, running and failed runs are never shown."""
    found = conn.execute("SELECT rowid FROM ticket WHERE ticket_id = ?", (ticket_id,)).fetchone()
    if not found:
        return None
    statuses = label_statuses(include_drafts)
    rows = rows_for(conn, [found[0]], statuses, DETAIL_COLUMNS)
    if not rows:
        return None
    r = rows[0]
    labels = [
        LabelOut(
            stage=lr["stage"],
            run_id=lr["run_id"],
            run_status=lr["run_status"],
            am_category=lr["am_category"],
            am_subcategory=lr["am_subcategory"],
            symptom_key=lr["symptom_key"],
            misfiled_as=lr["misfiled_as"],
            confidence=lr["confidence"],
            rationale=lr["rationale"],
        )
        for lr in conn.execute(
            "SELECT l.stage, l.run_id, r.status AS run_status, l.am_category, l.am_subcategory, l.symptom_key, "
            "l.misfiled_as, l.confidence, l.rationale FROM ai_ticket_label l JOIN ai_run r ON r.run_id = l.run_id "
            "JOIN ticket t ON t.ticket_id = l.ticket_id "
            f"WHERE l.ticket_id = ? AND r.status IN ({marks(statuses)}) "
            "AND l.input_hash = CASE l.stage WHEN 'open' THEN t.open_hash ELSE t.resolved_hash END "
            "ORDER BY r.run_seq DESC, l.stage",
            (ticket_id, *statuses),
        )
    ]
    return TicketDetail(
        **_row_from(r),
        description=r["description"],
        close_code=r["close_code"],
        close_notes=r["close_notes"],
        closed_at=r["closed_at"],
        sys_updated_on=r["sys_updated_on"],
        caller_pid=r["caller_pid"],
        assigned_to_pid=r["assigned_to_pid"],
        cmdb_ci_raw=r["cmdb_ci_raw"],
        business_service_raw=r["business_service_raw"],
        problem_id=r["problem_id"],
        caused_by=r["caused_by"],
        parent_incident=r["parent_incident"],
        reassignment_count=r["reassignment_count"],
        reopen_count=r["reopen_count"],
        made_sla=None if r["made_sla"] is None else bool(r["made_sla"]),
        labels=labels,
    )
