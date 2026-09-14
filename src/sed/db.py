"""SQLite access: connections, short write transactions, migrations, backups.

Concurrency model (Windows, multi-process): WAL mode, stdlib sqlite3 with isolation_level=None and an
explicit ``BEGIN IMMEDIATE`` for every write so the write lock is taken up front (no lock-upgrade
deadlocks). Readers use short-lived connections with ``PRAGMA query_only=ON``.
"""

from __future__ import annotations

import contextlib
import ctypes
import os
import re
import shutil
import sqlite3
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any

from sed.errors import Busy, PreconditionFailed, ValidationFailed

BUSY_TIMEOUT_MS = 15_000
SCHEMA_PACKAGE = "sed.schema"
_MIGRATION_RE = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(db_path: Path, *, readonly: bool = False) -> sqlite3.Connection:
    """Open a connection with the project's standard pragmas."""
    if readonly and not db_path.exists():
        raise PreconditionFailed(f"Database not found: {db_path}. Run `sed init` first.")
    conn = sqlite3.connect(
        str(db_path),
        isolation_level=None,
        timeout=BUSY_TIMEOUT_MS / 1000,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    if readonly:
        conn.execute("PRAGMA query_only=ON")
    return conn


def enable_wal(conn: sqlite3.Connection) -> str:
    mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
    return str(mode).lower()


def _is_busy(exc: sqlite3.OperationalError) -> bool:
    msg = str(exc).lower()
    return "locked" in msg or "busy" in msg


def _safe_rollback(conn: sqlite3.Connection) -> None:
    """Roll back only if a transaction is still open; never mask the original error."""
    if conn.in_transaction:
        with contextlib.suppress(sqlite3.Error):
            conn.execute("ROLLBACK")


@contextmanager
def write_tx(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Short write transaction. Raises Busy (exit 3) if the write lock cannot be taken in time."""
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError as exc:
        if _is_busy(exc):
            raise Busy("Database is busy (another writer held the lock too long); retry shortly.") from exc
        raise
    try:
        yield conn
    except BaseException:
        _safe_rollback(conn)
        raise
    else:
        try:
            conn.execute("COMMIT")
        except sqlite3.Error as exc:
            _safe_rollback(conn)
            if isinstance(exc, sqlite3.OperationalError) and _is_busy(exc):
                raise Busy("Database is busy at commit; retry shortly.") from exc
            raise


# ---------------------------------------------------------------------------
# meta table
# ---------------------------------------------------------------------------


def get_meta(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    except sqlite3.OperationalError:
        return default
    return row[0] if row else default


def all_meta(conn: sqlite3.Connection) -> dict[str, str]:
    try:
        return {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM meta ORDER BY key")}
    except sqlite3.OperationalError:
        return {}


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    """Upsert a meta key. Call inside write_tx."""
    conn.execute(
        "INSERT INTO meta(key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (key, value, utc_now()),
    )


# ---------------------------------------------------------------------------
# migrations
# ---------------------------------------------------------------------------


def migrations() -> list[tuple[int, str, str]]:
    """(version, filename, sql) for every schema/NNN_name.sql, ordered."""
    found = []
    for entry in resources.files(SCHEMA_PACKAGE).iterdir():
        m = _MIGRATION_RE.match(entry.name)
        if m:
            found.append((int(m.group(1)), entry.name, entry.read_text(encoding="utf-8")))
    found.sort()
    versions = [v for v, _, _ in found]
    if versions != list(range(1, len(versions) + 1)):
        raise PreconditionFailed(f"Schema migrations must be numbered 001..N without gaps, got {versions}")
    return found


def latest_version() -> int:
    return len(migrations())


def user_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def split_sql(script: str) -> list[str]:
    """Split a script into complete statements (handles triggers via sqlite3.complete_statement)."""
    statements, buf = [], []
    for line in script.splitlines(keepends=True):
        stripped = line.strip()
        if not buf and (not stripped or stripped.startswith("--")):
            continue
        buf.append(line)
        candidate = "".join(buf)
        if sqlite3.complete_statement(candidate):
            statements.append(candidate.strip())
            buf = []
    if "".join(buf).strip():
        raise PreconditionFailed("Incomplete SQL statement at end of migration script")
    return statements


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone()
    return row is not None


def migrate(conn: sqlite3.Connection, db_path: Path, backups_dir: Path | None) -> dict[str, Any]:
    """Apply pending migrations, each in its own IMMEDIATE transaction.

    * Backs up an existing (non-empty) DB before the first pending migration.
    * Foreign keys are switched OFF around each migration (PRAGMA foreign_keys is a no-op inside a
      transaction) so table rebuilds cannot cascade-delete child rows; PRAGMA foreign_key_check runs
      before COMMIT and any violation rolls the migration back.
    * The version is re-read under the write lock, so concurrent migrators do not re-apply a script.
    """
    all_migrations = migrations()
    latest = len(all_migrations)
    current = user_version(conn)
    if current > latest:
        raise PreconditionFailed(
            f"Database schema v{current} is newer than this sed (v{latest}); upgrade the code instead.",
        )
    pending = [m for m in all_migrations if m[0] > current]
    backup_file = None
    if pending and current > 0 and backups_dir is not None:
        backup_file = backup(conn, backups_dir, reason=f"pre-migrate-v{current}")
    applied = []
    for version, name, sql in pending:
        conn.execute("PRAGMA foreign_keys=OFF")
        try:
            with write_tx(conn):
                if user_version(conn) >= version:
                    continue
                for stmt in split_sql(sql):
                    conn.execute(stmt)
                violations = conn.execute("PRAGMA foreign_key_check").fetchall()
                if violations:
                    raise PreconditionFailed(
                        f"Migration {name} left {len(violations)} foreign-key violations; rolled back.",
                        [tuple(v) for v in violations[:20]],
                    )
                conn.execute(f"PRAGMA user_version={version}")
                if _has_table(conn, "meta"):
                    set_meta(conn, "schema_version", str(version))
        finally:
            conn.execute("PRAGMA foreign_keys=ON")
        applied.append(name)
    return {
        "from_version": current,
        "to_version": user_version(conn),
        "applied": applied,
        "backup": str(backup_file) if backup_file else None,
    }


# ---------------------------------------------------------------------------
# backup / restore
# ---------------------------------------------------------------------------


def backup(
    conn: sqlite3.Connection,
    backups_dir: Path,
    *,
    reason: str = "manual",
    keep: int | None = 7,
    protect: tuple[Path, ...] = (),
) -> Path:
    """Online backup. ``keep=None`` skips pruning; pruning never removes the new file or ``protect`` paths."""
    if keep is not None and keep < 1:
        raise ValidationFailed("keep must be at least 1")
    backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    safe_reason = re.sub(r"[^a-z0-9-]+", "-", reason.lower()).strip("-") or "backup"
    target = backups_dir / f"sed-{stamp}-{safe_reason}.db"
    dest = sqlite3.connect(str(target))
    try:
        conn.backup(dest)
    finally:
        dest.close()
    if keep is not None:
        prune_backups(backups_dir, keep=keep, protect=(target, *protect))
    return target


def prune_backups(backups_dir: Path, *, keep: int, protect: tuple[Path, ...] = ()) -> list[Path]:
    if keep < 1:
        raise ValidationFailed("keep must be at least 1")
    protected = {p.resolve() for p in protect}
    files = sorted(backups_dir.glob("sed-*.db"), key=lambda p: p.name, reverse=True)
    removed = []
    for old in files[keep:]:
        if old.resolve() in protected:
            continue
        old.unlink(missing_ok=True)
        removed.append(old)
    return removed


def last_backup_age_hours(backups_dir: Path) -> float | None:
    files = sorted(backups_dir.glob("sed-*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return None
    return (time.time() - files[0].stat().st_mtime) / 3600


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def serve_lock_holder(lock_path: Path) -> int | None:
    """PID of a live `sed serve` holding the lock, else None (stale locks are ignored)."""
    if not lock_path.exists():
        return None
    try:
        pid = int(lock_path.read_text(encoding="utf-8").strip().split()[0])
    except (ValueError, IndexError, OSError):
        return None
    return pid if pid_alive(pid) else None


def inspect_backup(backup_file: Path) -> dict[str, Any]:
    """Open a candidate backup read-only (never creates a file) and validate it is an sed database."""
    if not backup_file.is_file():
        raise PreconditionFailed(f"Backup file not found: {backup_file}")
    uri = f"file:{backup_file.resolve().as_posix()}?mode=ro"
    try:
        src = sqlite3.connect(uri, uri=True)
        try:
            integrity = src.execute("PRAGMA quick_check").fetchone()[0]
            version = int(src.execute("PRAGMA user_version").fetchone()[0])
            has_meta = src.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'").fetchone()
            meta = {r[0]: r[1] for r in src.execute("SELECT key, value FROM meta")} if has_meta else {}
        finally:
            src.close()
    except sqlite3.DatabaseError as exc:
        raise PreconditionFailed(f"{backup_file} is not a readable SQLite database: {exc}") from exc
    if integrity != "ok":
        raise PreconditionFailed(f"Backup failed integrity check: {integrity}")
    if not has_meta or not (0 < version <= latest_version()):
        raise PreconditionFailed(f"{backup_file} is not an sed database this version can restore (schema v{version}).")
    return {"user_version": version, "meta": meta}


def restore(
    backup_file: Path,
    db_path: Path,
    backups_dir: Path,
    lock_path: Path,
    *,
    expected_data_class: str | None = None,
    expected_salt_fingerprint: str | None = None,
) -> dict[str, Any]:
    """Restore a backup over the live DB.

    Order matters: validate the source read-only, copy it to a temp file, THEN take the safety backup
    (which may prune old backups, possibly including the source) and finally copy into the live DB.
    """
    holder = serve_lock_holder(lock_path)
    if holder is not None:
        raise PreconditionFailed(f"`sed serve` is running (pid {holder}); stop it before restoring.")
    info_ = inspect_backup(backup_file)
    src_meta = info_["meta"]
    if expected_data_class and src_meta.get("data_class") != expected_data_class:
        raise PreconditionFailed(
            f"Backup data_class={src_meta.get('data_class')} does not match this profile ({expected_data_class}).",
        )
    if expected_salt_fingerprint and src_meta.get("salt_fingerprint") not in (None, expected_salt_fingerprint):
        raise PreconditionFailed("Backup was made with a different PII salt than this profile uses.")

    with tempfile.TemporaryDirectory(prefix="sed-restore-") as tmp:
        staged = Path(tmp) / "staged.db"
        shutil.copy2(backup_file, staged)
        safety = None
        if db_path.exists():
            conn = connect(db_path)
            try:
                safety = backup(conn, backups_dir, reason="pre-restore", protect=(backup_file,))
            finally:
                conn.close()
        src = sqlite3.connect(f"file:{staged.as_posix()}?mode=ro", uri=True)
        dest = connect(db_path)
        try:
            src.backup(dest)
            enable_wal(dest)
            version = user_version(dest)
            restored_meta = all_meta(dest)
        finally:
            src.close()
            dest.close()
    return {
        "restored_from": str(backup_file),
        "safety_backup": str(safety) if safety else None,
        "user_version": version,
        "data_class": restored_meta.get("data_class"),
    }


def info(conn: sqlite3.Connection, db_path: Path) -> dict[str, Any]:
    tables = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "AND name NOT LIKE '%_fts%' ORDER BY name"
        )
    ]
    counts = {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}
    return {
        "path": str(db_path),
        "size_bytes": db_path.stat().st_size if db_path.exists() else 0,
        "sqlite_version": sqlite3.sqlite_version,
        "user_version": user_version(conn),
        "latest_version": latest_version(),
        "journal_mode": conn.execute("PRAGMA journal_mode").fetchone()[0],
        "meta": all_meta(conn),
        "row_counts": counts,
    }
