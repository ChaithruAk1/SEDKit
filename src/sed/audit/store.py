"""The audit trail's store: one append-only SQLite file per profile, `DATA_DIR\\audit\\audit.db`.

Kept apart from `sed.db` on purpose: `sed db restore` replaces `sed.db` wholesale and old backups are pruned, and
neither may ever take audit entries with them; and a download (a GET route) must record an entry while GET routes
never write `sed.db`.

* **Append only.** Triggers refuse UPDATE and DELETE, and SED has no code path that tries. Nothing prunes the log:
  entries are kept forever.
* **Hash chain.** Each entry stores the SHA-256 of the previous entry's hash and its own content, so an entry edited
  or removed by hand shows as a break (`verify`). It is not a seal: the chain has no key, so someone who knows how it
  is built can rewrite the whole file, just as anyone with the Windows login can delete it.
* **Its own connections.** Writers take `BEGIN IMMEDIATE` (the chain needs the last hash read inside the write lock);
  readers open the file read-only and never create it.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sed.db import split_sql
from sed.errors import PreconditionFailed

SCHEMA_VERSION = 1
BUSY_TIMEOUT_MS = 15_000
FIELDS = (
    "entry_id",
    "at",
    "actor",
    "actor_name",
    "method",
    "verified",
    "channel",
    "action",
    "outcome",
    "target_type",
    "target_id",
    "summary",
    "detail",
    "changes",
    "correlation_id",
    "profile",
    "sed_version",
)
COLUMNS = (*FIELDS, "prev_hash", "entry_hash")
SCHEMA = """
CREATE TABLE audit_entry (
    seq            INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id       TEXT NOT NULL UNIQUE,
    at             TEXT NOT NULL,                 -- ISO-8601 UTC
    actor          TEXT NOT NULL,                 -- verified address, or windows:<account>, or unknown
    actor_name     TEXT NOT NULL,
    method         TEXT NOT NULL,                 -- microsoft | google | github | developer_mode | windows | none
    verified       INTEGER NOT NULL CHECK (verified IN (0, 1)),
    channel        TEXT NOT NULL,                 -- dashboard | command_line
    action         TEXT NOT NULL,                 -- sign_in, download, pull, import, clear, ... (sed.audit.record)
    outcome        TEXT NOT NULL,                 -- started | done | failed | refused
    target_type    TEXT,
    target_id      TEXT,
    summary        TEXT NOT NULL,                 -- one plain sentence
    detail         TEXT,                          -- JSON object
    changes        TEXT,                          -- JSON list of {field, before, after}
    correlation_id TEXT,                          -- ties an attempt to its outcome
    profile        TEXT NOT NULL,
    sed_version    TEXT NOT NULL,
    prev_hash      TEXT NOT NULL,
    entry_hash     TEXT NOT NULL
);
CREATE INDEX ix_audit_at ON audit_entry(at);
CREATE INDEX ix_audit_actor ON audit_entry(actor, at);
CREATE INDEX ix_audit_action ON audit_entry(action, at);
CREATE INDEX ix_audit_correlation ON audit_entry(correlation_id);
CREATE TRIGGER audit_entry_never_changed BEFORE UPDATE ON audit_entry
BEGIN
    SELECT RAISE(ABORT, 'audit entries are never changed');
END;
CREATE TRIGGER audit_entry_never_deleted BEFORE DELETE ON audit_entry
BEGIN
    SELECT RAISE(ABORT, 'audit entries are never deleted');
END;
"""


def canonical(row: dict[str, Any]) -> str:
    return json.dumps({f: row.get(f) for f in FIELDS}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def entry_hash(prev_hash: str, row: dict[str, Any]) -> str:
    return hashlib.sha256(f"{prev_hash}\n{canonical(row)}".encode()).hexdigest()


def connect(path: Path, *, readonly: bool = False) -> sqlite3.Connection:
    """A connection with the store's pragmas. A writer creates the file and its schema; a reader never does."""
    if readonly:
        if not path.is_file():
            raise PreconditionFailed(f"No audit trail yet at {path}")
        conn = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True, check_same_thread=False)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), timeout=BUSY_TIMEOUT_MS / 1000, check_same_thread=False)
    conn.isolation_level = None
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    if readonly:
        conn.execute("PRAGMA query_only=ON")
    else:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")  # an entry that was acknowledged survives a power cut
        _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if version == SCHEMA_VERSION:
        return
    if version > SCHEMA_VERSION:
        raise PreconditionFailed(f"The audit trail was written by a newer SED (schema v{version})")
    with _immediate(conn):
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if version > SCHEMA_VERSION:
            raise PreconditionFailed(f"The audit trail was written by a newer SED (schema v{version})")
        if version < 1:
            for statement in split_sql(SCHEMA):
                conn.execute(statement)
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")


@contextmanager
def _immediate(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def append(path: Path, row: dict[str, Any], *, moved: Callable[[], Path | None] | None = None) -> dict[str, Any]:
    """Add one entry at the end of the chain and return it as stored (with its hashes).

    `moved()` is asked once the write lock is held: when the data folder was moved while this writer waited (`sed data
    move` holds the lock while it copies), it answers the trail's new path and the entry is written there instead."""
    conn = connect(path)
    relocated: Path | None = None
    try:
        with _immediate(conn):
            relocated = moved() if moved is not None else None
            if relocated is None:
                last = conn.execute("SELECT entry_hash FROM audit_entry ORDER BY seq DESC LIMIT 1").fetchone()
                prev = last[0] if last else ""
                stored = {**{f: row.get(f) for f in FIELDS}, "prev_hash": prev, "entry_hash": entry_hash(prev, row)}
                conn.execute(
                    f"INSERT INTO audit_entry ({', '.join(COLUMNS)}) VALUES ({', '.join('?' for _ in COLUMNS)})",
                    [stored[c] for c in COLUMNS],
                )
                stored["seq"] = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    finally:
        conn.close()
    if relocated is not None:
        return append(relocated, row)
    return stored


def verify(path: Path) -> dict[str, Any]:
    """Walk the chain: how many entries, whether it is intact, and the number of the first entry that is missing or
    does not fit. Entries removed from the end show too: SQLite keeps the highest number it ever handed out."""
    result: dict[str, Any] = {"entries": 0, "intact": True, "first_break": None, "first_at": None, "last_at": None}
    if not path.is_file():
        return result
    conn = connect(path, readonly=True)
    try:
        conn.execute("BEGIN")  # one snapshot: an entry appended meanwhile must not look like a gap at the end
        try:
            return _walk(conn, result)
        finally:
            conn.execute("COMMIT")
    finally:
        conn.close()


def _walk(conn: sqlite3.Connection, result: dict[str, Any]) -> dict[str, Any]:
    prev, expected = "", 1
    for row in conn.execute(f"SELECT seq, {', '.join(COLUMNS)} FROM audit_entry ORDER BY seq"):
        data = dict(row)
        fits = data["seq"] == expected and data["prev_hash"] == prev and data["entry_hash"] == entry_hash(prev, data)
        if not fits:
            return {**result, "intact": False, "first_break": min(data["seq"], expected)}
        result["entries"] += 1
        result["first_at"] = result["first_at"] or data["at"]
        result["last_at"] = data["at"]
        prev, expected = data["entry_hash"], data["seq"] + 1
    handed_out = conn.execute("SELECT seq FROM sqlite_sequence WHERE name = 'audit_entry'").fetchone()
    if handed_out is not None and int(handed_out[0]) >= expected:
        return {**result, "intact": False, "first_break": expected}
    return result
