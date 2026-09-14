"""Manual alias assignment shared by the CLI (`sed alias assign`) and the API (`POST /api/aliases`)."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from sed import db
from sed.errors import ValidationFailed
from sed.paths import Paths

APP_KINDS = {"app", "ci", "jira_project", "jira_component", "confluence_space"}


def resolve_target_id(conn: sqlite3.Connection, kind: str, target: str) -> str:
    """Canonical id for a target given as an id or an exact (case-insensitive) name."""
    if kind in APP_KINDS:
        row = conn.execute(
            "SELECT app_id FROM application WHERE app_id = ? OR lower(name) = lower(?)", (target, target)
        ).fetchone()
    elif kind == "vendor":
        row = conn.execute(
            "SELECT vendor_id FROM vendor WHERE vendor_id = ? OR lower(name) = lower(?)", (target, target)
        ).fetchone()
    else:
        row = conn.execute("SELECT name FROM assignment_group WHERE lower(name) = lower(?)", (target,)).fetchone()
    if not row:
        raise ValidationFailed(f"Unknown {kind} target '{target}'")
    return str(row[0])


def alias_targets(conn: sqlite3.Connection, kind: str, q: str | None = None, limit: int = 50) -> list[dict[str, str]]:
    """Candidate targets for an alias kind, optionally filtered by a name/id substring."""
    like = f"%{(q or '').strip().lower()}%"
    if kind in APP_KINDS:
        table, id_col = "application", "app_id"
    elif kind == "vendor":
        table, id_col = "vendor", "vendor_id"
    elif kind == "group":
        table, id_col = "assignment_group", "name"
    else:
        raise ValidationFailed(f"Unknown alias kind '{kind}'")
    sql = (
        f"SELECT {id_col} AS id, name FROM {table} "
        f"WHERE is_deleted = 0 AND (lower(name) LIKE ? OR lower({id_col}) LIKE ?)"
    )
    rows = conn.execute(sql + " ORDER BY name LIMIT ?", (like, like, int(limit))).fetchall()
    return [{"id": str(r["id"]), "name": str(r["name"])} for r in rows]


def assign_alias(
    paths: Paths, kind: str, raw: str, target: str, *, reviewer: str, reresolve: bool = True
) -> dict[str, Any]:
    """Record a manual alias (never overwritten by imports), log the decision and optionally re-link rows."""
    from sed.ingest.loader import reresolve as reresolve_rows
    from sed.ingest.resolve import ALIAS_KINDS, Resolver

    if kind not in ALIAS_KINDS:
        raise ValidationFailed(f"kind must be one of {', '.join(ALIAS_KINDS)}")
    conn = db.connect(paths.db)
    try:
        target_id = resolve_target_id(conn, kind, target)
        resolver = Resolver(conn)
        resolver.add_alias(kind, raw, target_id, "manual")
        with db.write_tx(conn):
            resolver.flush(None)
            conn.execute(
                "INSERT INTO review_decision (target_type, target_id, decision, payload_json, reviewer, decided_at) "
                "VALUES ('alias', ?, 'approve', ?, ?, ?)",
                (f"{kind}:{raw}", json.dumps({"target": target_id}), reviewer, db.utc_now()),
            )
    finally:
        conn.close()
    result: dict[str, Any] = {"kind": kind, "raw": raw, "target_id": target_id}
    if reresolve:
        result["reresolved"] = reresolve_rows(paths)
    return result
