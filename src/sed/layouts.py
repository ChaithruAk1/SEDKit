"""Named column layouts for the dashboard's tables (schema 011).

A real export carries far more columns than a table shows at once, and which ones matter depends on the job: a weekly
review and a vendor meeting want different sets. A layout names one set of columns, in display order, so it can be
chosen again rather than rebuilt. One layout per table may be the default, which is the one a table opens with.

Layouts hold column keys only: never data, never a value from a ticket. They are stored rather than kept in the
browser so they survive a new browser or a reinstall.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from sed.errors import ValidationFailed

MAX_NAME = 60
MAX_COLUMNS = 200
MAX_KEY = 80


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clean_name(name: str) -> str:
    clean = " ".join(str(name).split())
    if not clean:
        raise ValidationFailed("A layout needs a name")
    if len(clean) > MAX_NAME:
        raise ValidationFailed(f"A layout name may be at most {MAX_NAME} characters")
    return clean


def _clean_columns(columns: list[str]) -> list[str]:
    """Column keys, in display order, de-duplicated and order-preserving."""
    if not columns:
        raise ValidationFailed("A layout needs at least one column")
    if len(columns) > MAX_COLUMNS:
        raise ValidationFailed(f"A layout may hold at most {MAX_COLUMNS} columns")
    out: list[str] = []
    for key in columns:
        text = str(key).strip()
        if not text or len(text) > MAX_KEY:
            raise ValidationFailed(f"Invalid column key {key!r}")
        if text not in out:
            out.append(text)
    return out


def _clean_table_key(table_key: str) -> str:
    text = str(table_key).strip()
    if not text or len(text) > MAX_KEY:
        raise ValidationFailed(f"Invalid table key {table_key!r}")
    return text


def _row(r: sqlite3.Row) -> dict[str, Any]:
    return {
        "layout_id": r["layout_id"],
        "table_key": r["table_key"],
        "name": r["name"],
        "columns": json.loads(r["columns_json"]),
        "is_default": bool(r["is_default"]),
        "updated_at": r["updated_at"],
    }


def list_layouts(conn: sqlite3.Connection, table_key: str) -> list[dict[str, Any]]:
    """Every layout for one table, the default first, then by name."""
    rows = conn.execute(
        "SELECT layout_id, table_key, name, columns_json, is_default, updated_at FROM table_layout "
        "WHERE table_key = ? ORDER BY is_default DESC, name COLLATE NOCASE",
        (_clean_table_key(table_key),),
    ).fetchall()
    return [_row(r) for r in rows]


def save_layout(
    conn: sqlite3.Connection, table_key: str, name: str, columns: list[str], *, make_default: bool = False
) -> dict[str, Any]:
    """Create a layout, or replace the columns of one already saved under that name for that table."""
    key, clean_name, cols = _clean_table_key(table_key), _clean_name(name), _clean_columns(columns)
    now = _now()
    conn.execute(
        "INSERT INTO table_layout (table_key, name, columns_json, is_default, created_at, updated_at) "
        "VALUES (?, ?, ?, 0, ?, ?) "
        "ON CONFLICT (table_key, name) DO UPDATE SET "
        "columns_json = excluded.columns_json, updated_at = excluded.updated_at",
        (key, clean_name, json.dumps(cols, ensure_ascii=False), now, now),
    )
    row = conn.execute(
        "SELECT layout_id FROM table_layout WHERE table_key = ? AND name = ?", (key, clean_name)
    ).fetchone()
    if make_default:
        set_default(conn, int(row["layout_id"]))
    found = conn.execute(
        "SELECT layout_id, table_key, name, columns_json, is_default, updated_at FROM table_layout WHERE layout_id = ?",
        (int(row["layout_id"]),),
    ).fetchone()
    return _row(found)


def set_default(conn: sqlite3.Connection, layout_id: int) -> dict[str, Any]:
    """Make one layout the default for its table, clearing the previous one (at most one default per table)."""
    found = conn.execute("SELECT table_key FROM table_layout WHERE layout_id = ?", (layout_id,)).fetchone()
    if not found:
        raise ValidationFailed(f"No layout with id {layout_id}")
    conn.execute("UPDATE table_layout SET is_default = 0 WHERE table_key = ?", (found["table_key"],))
    conn.execute("UPDATE table_layout SET is_default = 1, updated_at = ? WHERE layout_id = ?", (_now(), layout_id))
    row = conn.execute(
        "SELECT layout_id, table_key, name, columns_json, is_default, updated_at FROM table_layout WHERE layout_id = ?",
        (layout_id,),
    ).fetchone()
    return _row(row)


def delete_layout(conn: sqlite3.Connection, layout_id: int) -> dict[str, Any]:
    found = conn.execute("SELECT table_key, name FROM table_layout WHERE layout_id = ?", (layout_id,)).fetchone()
    if not found:
        raise ValidationFailed(f"No layout with id {layout_id}")
    conn.execute("DELETE FROM table_layout WHERE layout_id = ?", (layout_id,))
    return {"layout_id": layout_id, "table_key": found["table_key"], "name": found["name"], "deleted": True}
