"""Data freshness: what was imported when, and the date the data describes (`data_as_of`)."""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any

from sed.settings import Settings


def import_freshness(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Last completed import per mapping."""
    rows = conn.execute(
        "SELECT mapping_name, MAX(imported_at) AS last_import, COUNT(*) AS files, MAX(as_of) AS latest_as_of "
        "FROM import_batch WHERE status = 'completed' GROUP BY mapping_name ORDER BY mapping_name"
    ).fetchall()
    return [dict(r) for r in rows]


def data_as_of(conn: sqlite3.Connection, settings: Settings) -> date | None:
    """The export moment the store describes: settings.as_of, else the newest as-of of the active-snapshot exports,
    else the newest as-of of any completed import, else None (nothing imported)."""
    if settings.as_of:
        return settings.as_of
    row = conn.execute(
        "SELECT MAX(as_of) FROM import_batch WHERE status = 'completed' AND load_mode = 'active_snapshot'"
    ).fetchone()
    value = row[0] if row else None
    if not value:
        row = conn.execute("SELECT MAX(as_of) FROM import_batch WHERE status = 'completed'").fetchone()
        value = row[0] if row else None
    return date.fromisoformat(str(value)[:10]) if value else None
