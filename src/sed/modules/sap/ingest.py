"""SAP ingest targets: ChaRM change documents (with their status history), transport imports and IDocs (with their
status history).

Rows keep the export's raw values; see sed.modules.sap.charm for how they are read. Person fields arrive pseudonymised
by the mappings (`pii: person`) and free text scrubbed (`pii: free_text`).
"""

from __future__ import annotations

from typing import Any

from sed.ingest.target import Ctx, Reject, Target

CHANGE_COLUMNS = (
    "change_id",
    "transaction_type",
    "title",
    "status_raw",
    "priority",
    "component_raw",
    "cycle_raw",
    "created_at",
    "changed_at",
    "requester_pid",
    "developer_pid",
    "change_manager_pid",
    "external_ref",
    "ticket_ref",
)
TRANSPORT_COLUMNS = (
    "transport",
    "system_id",
    "change_id",
    "transport_type",
    "description",
    "owner_pid",
    "released_at",
    "import_status",
    "return_code",
    "imported_at",
)


def _text(value: Any) -> str | None:
    text = " ".join(str(value).split()) if value is not None else ""
    return text or None


def _required(rec: dict[str, Any], name: str) -> str:
    value = _text(rec.get(name))
    if value is None:
        raise Reject(f"missing {name}")
    return value


def _same_status(a: str | None, b: str | None) -> bool:
    return (a or "").casefold() == (b or "").casefold()


def build_change(rec: dict[str, Any], raw: dict[str, Any], ctx: Ctx) -> dict[str, Any]:
    created = rec.get("created_at")
    return {
        "change_id": _required(rec, "change_id"),
        "transaction_type": (_text(rec.get("transaction_type")) or "").upper() or None,
        "title": rec.get("title"),
        "status_raw": _text(rec.get("status")),
        "priority": _text(rec.get("priority")),
        "component_raw": _text(rec.get("component")),
        "cycle_raw": _text(rec.get("cycle")),
        "created_at": created,
        "changed_at": rec.get("changed_at") or created,
        "requester_pid": rec.get("requester"),
        "developer_pid": rec.get("developer"),
        "change_manager_pid": rec.get("change_manager"),
        "external_ref": _text(rec.get("external_ref")),
        "ticket_ref": _text(rec.get("ticket_ref")),
    }


def record_status_history(ctx: Ctx, rows: list[dict[str, Any]]) -> None:
    """Add a sap_change_status row whenever a file shows a change with another status than the last one recorded.

    A row older than the last recorded status (a late export) adds nothing, so importing files out of order never
    rewrites history.
    """
    ids = sorted({r["change_id"] for r in rows})
    last: dict[str, tuple[str, str]] = {}
    for start in range(0, len(ids), 500):
        chunk = ids[start : start + 500]
        marks = ", ".join("?" for _ in chunk)
        for change_id, seen_at, status in ctx.conn.execute(
            f"SELECT change_id, seen_at, status_raw FROM sap_change_status WHERE change_id IN ({marks}) "
            "ORDER BY change_id, seen_at, rowid",
            chunk,
        ):
            last[change_id] = (seen_at, status)
    new: list[tuple[str, str, str, int | None]] = []
    for row in sorted(rows, key=lambda r: (r["changed_at"] or "", r["change_id"])):
        status, changed_at = row["status_raw"], row["changed_at"]
        if not status or not changed_at:
            continue
        previous = last.get(row["change_id"])
        if previous and (changed_at <= previous[0] or _same_status(previous[1], status)):
            continue
        last[row["change_id"]] = (changed_at, status)
        new.append((row["change_id"], changed_at, status, ctx.batch_id))
    ctx.conn.executemany(
        "INSERT OR IGNORE INTO sap_change_status (change_id, seen_at, status_raw, batch_id) VALUES (?, ?, ?, ?)", new
    )


def build_transport(rec: dict[str, Any], raw: dict[str, Any], ctx: Ctx) -> dict[str, Any]:
    rc = rec.get("return_code")
    return {
        "transport": _required(rec, "transport").upper(),
        "system_id": _required(rec, "system_id").upper(),
        "change_id": _text(rec.get("change_id")),
        "transport_type": _text(rec.get("transport_type")),
        "description": rec.get("description"),
        "owner_pid": rec.get("owner"),
        "released_at": rec.get("released_at"),
        "import_status": _text(rec.get("import_status")),
        "return_code": int(rc) if rc not in (None, "") else None,
        "imported_at": rec.get("imported_at"),
    }


IDOC_COLUMNS = (
    "system_id",
    "docnum",
    "direction",
    "message_type",
    "basic_type",
    "partner_type",
    "partner_number",
    "status_code",
    "status_text",
    "created_at",
    "status_at",
)
STATUS_TEXT_LIMIT = 300
DIRECTIONS = {
    "1": "outbound",
    "2": "inbound",
    "outbound": "outbound",
    "inbound": "inbound",
    "out": "outbound",
    "in": "inbound",
}


def build_idoc(rec: dict[str, Any], raw: dict[str, Any], ctx: Ctx) -> dict[str, Any]:
    from sed.modules.sap.idoc import normalise_code

    docnum = _required(rec, "docnum")
    direction = (_text(rec.get("direction")) or "").casefold()
    created = rec.get("created_at")
    return {
        "system_id": _required(rec, "system_id").upper(),
        "docnum": docnum.zfill(16) if docnum.isdigit() else docnum,
        "direction": DIRECTIONS.get(direction),
        "message_type": (_text(rec.get("message_type")) or "").upper() or None,
        "basic_type": _text(rec.get("basic_type")),
        "partner_type": _text(rec.get("partner_type")),
        "partner_number": _text(rec.get("partner_number")),
        "status_code": normalise_code(_required(rec, "status_code")),
        "status_text": (rec.get("status_text") or "")[:STATUS_TEXT_LIMIT] or None,
        "created_at": created,
        "status_at": rec.get("status_at") or created,
    }


def record_idoc_history(ctx: Ctx, rows: list[dict[str, Any]]) -> None:
    """Add a sap_idoc_status row whenever a file shows an IDoc with another status code than the last one recorded
    (a row older than the last recorded status adds nothing)."""
    keys = sorted({(r["system_id"], r["docnum"]) for r in rows})
    last: dict[tuple[str, str], tuple[str, str]] = {}
    for start in range(0, len(keys), 400):
        chunk = keys[start : start + 400]
        clause = " OR ".join("(system_id = ? AND docnum = ?)" for _ in chunk)
        params = [v for key in chunk for v in key]
        for system_id, docnum, status_at, code in ctx.conn.execute(
            f"SELECT system_id, docnum, status_at, status_code FROM sap_idoc_status WHERE {clause} "
            "ORDER BY system_id, docnum, status_at, rowid",
            params,
        ):
            last[(system_id, docnum)] = (status_at, code)
    new: list[tuple[Any, ...]] = []
    for row in sorted(rows, key=lambda r: (r["status_at"] or "", r["system_id"], r["docnum"])):
        key = (row["system_id"], row["docnum"])
        if not row["status_at"]:
            continue
        previous = last.get(key)
        if previous and (row["status_at"] <= previous[0] or previous[1] == row["status_code"]):
            continue
        last[key] = (row["status_at"], row["status_code"])
        new.append((*key, row["status_at"], row["status_code"], row["status_text"], ctx.batch_id))
    ctx.conn.executemany(
        "INSERT OR IGNORE INTO sap_idoc_status (system_id, docnum, status_at, status_code, status_text, batch_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        new,
    )


TARGETS = {
    "sap_change": Target(
        "sap_change",
        "sap_change",
        ("change_id",),
        CHANGE_COLUMNS,
        build_change,
        updated_field="changed_at",
        after_load=record_status_history,
        order=200,
    ),
    "sap_transport_import": Target(
        "sap_transport_import",
        "sap_transport_import",
        ("transport", "system_id"),
        TRANSPORT_COLUMNS,
        build_transport,
        updated_field="imported_at",
        order=210,
    ),
    "sap_idoc": Target(
        "sap_idoc",
        "sap_idoc",
        ("system_id", "docnum"),
        IDOC_COLUMNS,
        build_idoc,
        updated_field="status_at",
        after_load=record_idoc_history,
        order=220,
    ),
}
