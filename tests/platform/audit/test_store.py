"""The audit store: an append-only chain that refuses changes, and shows tampering done behind SED's back."""

from __future__ import annotations

import sqlite3
import threading

import pytest

from sed.audit import store
from sed.errors import PreconditionFailed


def _row(n: int, **extra) -> dict:
    return {
        "entry_id": f"entry-{n}",
        "at": f"2026-09-25T10:00:{n:02d}Z",
        "actor": "owner@example.com",
        "actor_name": "Owner",
        "method": "google",
        "verified": 1,
        "channel": "dashboard",
        "action": "download",
        "outcome": "done",
        "summary": f"Downloaded {n} tickets as a workbook.",
        "profile": "synthetic",
        "sed_version": "0.1.0",
        **extra,
    }


def _tamper(path, sql: str, params=()) -> None:
    """What someone with the Windows login could do with any SQLite tool: drop the guards, then edit."""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("DROP TRIGGER audit_entry_never_changed")
        conn.execute("DROP TRIGGER audit_entry_never_deleted")
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def test_entries_chain_and_verify(tmp_path):
    path = tmp_path / "audit" / "audit.db"
    assert store.verify(path) == {"entries": 0, "intact": True, "first_break": None, "first_at": None, "last_at": None}
    first = store.append(path, _row(1))
    second = store.append(path, _row(2))
    assert first["prev_hash"] == "" and second["prev_hash"] == first["entry_hash"] and second["seq"] == 2
    assert second["entry_hash"] == store.entry_hash(first["entry_hash"], _row(2))
    result = store.verify(path)
    assert result == {
        "entries": 2,
        "intact": True,
        "first_break": None,
        "first_at": "2026-09-25T10:00:01Z",
        "last_at": "2026-09-25T10:00:02Z",
    }


def test_sqlite_itself_refuses_to_change_or_delete_an_entry(tmp_path):
    path = tmp_path / "audit.db"
    store.append(path, _row(1))
    conn = store.connect(path)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="never changed"):
            conn.execute("UPDATE audit_entry SET actor = 'someone-else@example.com'")
        with pytest.raises(sqlite3.IntegrityError, match="never deleted"):
            conn.execute("DELETE FROM audit_entry")
    finally:
        conn.close()
    assert store.verify(path)["intact"]


@pytest.mark.parametrize(
    ("sql", "first_break"),
    [
        ("UPDATE audit_entry SET actor = 'someone-else@example.com' WHERE seq = 2", 2),
        ("DELETE FROM audit_entry WHERE seq = 2", 2),
        ("DELETE FROM audit_entry WHERE seq = 3", 3),  # the last one: SQLite remembers it was handed out
    ],
)
def test_tampering_outside_sed_shows(tmp_path, sql, first_break):
    path = tmp_path / "audit.db"
    for n in (1, 2, 3):
        store.append(path, _row(n))
    _tamper(path, sql)
    result = store.verify(path)
    assert result["intact"] is False and result["first_break"] == first_break


def test_a_reader_never_creates_the_file(tmp_path):
    path = tmp_path / "audit" / "audit.db"
    with pytest.raises(PreconditionFailed):
        store.connect(path, readonly=True)
    assert not path.exists()


def test_concurrent_writers_keep_one_unbroken_chain(tmp_path):
    path = tmp_path / "audit.db"
    store.append(path, _row(0))  # create the file and schema once
    errors: list[BaseException] = []

    def write(worker: int) -> None:
        try:
            for n in range(10):
                store.append(path, _row(n, entry_id=f"entry-{worker}-{n}"))
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=write, args=(w,)) for w in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert store.verify(path) | {"first_at": None, "last_at": None} == {
        "entries": 41,
        "intact": True,
        "first_break": None,
        "first_at": None,
        "last_at": None,
    }


def test_a_writer_waits_for_another_process_creating_the_trail(tmp_path):
    # The other process holds the lock on the new file. SQLite answers the WAL switch "locked" at once instead of
    # waiting, so without a retry this action would be refused (two commands started at the same moment).
    path = tmp_path / "audit.db"
    other = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
    other.execute("BEGIN IMMEDIATE")
    release = threading.Timer(0.3, other.execute, args=("COMMIT",))
    release.start()
    try:
        stored = store.append(path, _row(1))
    finally:
        release.join()
        other.close()
    assert stored["seq"] == 1 and store.verify(path)["intact"]


def test_a_trail_locked_longer_than_the_busy_timeout_still_refuses(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "BUSY_TIMEOUT_MS", 200)
    path = tmp_path / "audit.db"
    other = sqlite3.connect(str(path), isolation_level=None)
    other.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            store.append(path, _row(1))
    finally:
        other.execute("ROLLBACK")
        other.close()


def test_verify_reads_one_snapshot_while_others_write(tmp_path, monkeypatch):
    path = tmp_path / "audit.db"
    for n in (1, 2, 3):
        store.append(path, _row(n))
    real_connect = store.connect

    class Racing:
        """A reader during whose walk another process appends an entry (just before the tail check)."""

        def __init__(self, conn):
            self.conn = conn

        def execute(self, sql, *args):
            if sql.startswith("SELECT seq FROM sqlite_sequence"):
                store.append(path, _row(4, entry_id="written-meanwhile"))
            return self.conn.execute(sql, *args)

        def close(self):
            self.conn.close()

    monkeypatch.setattr(
        store,
        "connect",
        lambda p, readonly=False: Racing(real_connect(p, readonly=True)) if readonly else real_connect(p),
    )
    result = store.verify(path)
    assert result["intact"] and result["entries"] == 3  # the snapshot it started with, not a false alarm


def test_a_trail_written_by_a_newer_sed_is_refused(tmp_path):
    path = tmp_path / "audit.db"
    store.append(path, _row(1))
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("PRAGMA user_version=2")
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(PreconditionFailed, match="newer SED"):
        store.append(path, _row(2))


def test_an_entry_follows_a_moved_data_folder(tmp_path):
    old, new = tmp_path / "old" / "audit.db", tmp_path / "new" / "audit.db"
    store.append(old, _row(1))
    stored = store.append(old, _row(2), moved=lambda: new)
    assert stored["seq"] == 1 and store.verify(new)["entries"] == 1 and store.verify(old)["entries"] == 1
