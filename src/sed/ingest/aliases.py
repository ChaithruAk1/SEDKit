"""Manual alias assignment shared by the CLI (`sed alias assign`) and the API (`POST /api/aliases`)."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from sed import db
from sed.errors import ValidationFailed
from sed.paths import Paths


def resolve_target_id(conn: sqlite3.Connection, kind: str, target: str) -> str:
    """Canonical id for a target given as an id or an exact (case-insensitive) name."""
    from sed.modules import entity_for_alias_kind

    entity = entity_for_alias_kind(kind)
    row = conn.execute(
        f"SELECT {entity.id_col} FROM {entity.table} WHERE {entity.id_col} = ? OR lower({entity.name_col}) = lower(?)",
        (target, target),
    ).fetchone()
    if not row:
        raise ValidationFailed(f"Unknown {kind} target '{target}'")
    return str(row[0])


def alias_targets(conn: sqlite3.Connection, kind: str, q: str | None = None, limit: int = 50) -> list[dict[str, str]]:
    """Candidate targets for an alias kind, optionally filtered by a name/id substring."""
    from sed.modules import entity_for_alias_kind

    like = f"%{(q or '').strip().lower()}%"
    entity = entity_for_alias_kind(kind)
    table, id_col, name_col = entity.table, entity.id_col, entity.name_col
    sql = (
        f"SELECT {id_col} AS id, {name_col} AS name FROM {table} "
        f"WHERE is_deleted = 0 AND (lower({name_col}) LIKE ? OR lower({id_col}) LIKE ?)"
    )
    rows = conn.execute(sql + f" ORDER BY {name_col} LIMIT ?", (like, like, int(limit))).fetchall()
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
