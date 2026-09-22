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
    else the newest as-of of any completed import, else the newest ticket update, else None (nothing imported).

    The last step matters. Only snapshot exports carry an as-of of their own; a store loaded from delta exports alone
    has none, and this returned None. `resolve_as_of` then fell back to today, so every page of a store whose data is
    not from this week came back empty while the data sat there — the CLI, which reads the newest ticket update, did
    not agree with the dashboard, which did not. Both now answer from the same place.
    """
    if settings.as_of:
        return settings.as_of
    for sql in (
        "SELECT MAX(as_of) FROM import_batch WHERE status = 'completed' AND load_mode = 'active_snapshot'",
        "SELECT MAX(as_of) FROM import_batch WHERE status = 'completed'",
        "SELECT MAX(sys_updated_on) FROM ticket",
    ):
        row = conn.execute(sql).fetchone()
        if value := (row[0] if row else None):
            return date.fromisoformat(str(value)[:10])
    return None
