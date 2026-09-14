from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from amkit import db
from amkit.errors import PreconditionFailed

EXPECTED_TABLES = {
    "meta", "import_batch", "row_reject", "alias", "unmapped_value", "person_key", "person_display", "vendor",
    "application", "assignment_group", "contract", "license", "license_usage", "cost_line", "ticket", "task_sla",
    "ci_rel", "work_item", "doc_page", "ai_run", "ai_batch", "ai_claim", "ai_ticket_label", "symptom_key_alias",
    "finding", "ai_cluster_member", "review_decision", "report_snapshot", "report_artifact", "job",
}  # fmt: skip
EXPECTED_VIEWS = {"v_label_current", "v_ticket", "v_findings_published"}


@pytest.fixture
def conn(tmp_path: Path):
    path = tmp_path / "amkit.db"
    c = db.connect(path)
    db.enable_wal(c)
    db.migrate(c, path, tmp_path / "backups")
    yield c
    c.close()


def test_migrations_are_contiguous():
    versions = [v for v, _, _ in db.migrations()]
    assert versions == list(range(1, len(versions) + 1))


def test_fresh_migrate_creates_schema(conn: sqlite3.Connection):
    assert db.user_version(conn) == db.latest_version()
    names = {r[0]: r[1] for r in conn.execute("SELECT name, type FROM sqlite_master")}
    assert {n for n, t in names.items() if t == "table"} >= EXPECTED_TABLES
    assert {n for n, t in names.items() if t == "view"} >= EXPECTED_VIEWS
    assert "ticket_fts" in names
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_migrate_is_idempotent(conn: sqlite3.Connection, tmp_path: Path):
    result = db.migrate(conn, tmp_path / "amkit.db", tmp_path / "backups")
    assert result["applied"] == []
    assert result["backup"] is None


def test_views_are_queryable(conn: sqlite3.Connection):
    for view in EXPECTED_VIEWS:
        conn.execute(f"SELECT * FROM {view} LIMIT 1").fetchall()


def test_ai_run_seq_autoincrements(conn: sqlite3.Connection):
    with db.write_tx(conn):
        for i in range(2):
            conn.execute(
                "INSERT INTO ai_run (run_id, skill, skill_hash, schema_version, invoked_via, profile, started_at) "
                "VALUES (?, 'am-triage-batch', 'h', 1, 'workflow', 'test', ?)",
                (f"run-{i}", db.utc_now()),
            )
    seqs = [r[0] for r in conn.execute("SELECT run_seq FROM ai_run ORDER BY run_seq")]
    assert seqs == [1, 2]


def test_write_tx_rolls_back_on_error(conn: sqlite3.Connection):
    with pytest.raises(RuntimeError), db.write_tx(conn):
        db.set_meta(conn, "k", "v")
        raise RuntimeError("boom")
    assert db.get_meta(conn, "k") is None


def test_review_decision_is_append_only(conn: sqlite3.Connection):
    with db.write_tx(conn):
        conn.execute(
            "INSERT INTO review_decision (target_type, target_id, decision, reviewer, decided_at) "
            "VALUES ('finding', 'f1', 'approve', 'tester', ?)",
            (db.utc_now(),),
        )
    with pytest.raises(sqlite3.IntegrityError), db.write_tx(conn):
        conn.execute("UPDATE review_decision SET decision = 'reject'")
    with pytest.raises(sqlite3.IntegrityError), db.write_tx(conn):
        conn.execute("DELETE FROM review_decision")


def test_fts_tracks_ticket_changes(conn: sqlite3.Connection):
    with db.write_tx(conn):
        conn.execute(
            "INSERT INTO ticket (ticket_id, kind, number, short_description, description, sys_updated_on) "
            "VALUES ('incident:INC0000001', 'incident', 'INC0000001', 'Interface posting timeout', 'Queue stuck', ?)",
            (db.utc_now(),),
        )
    hits = conn.execute("SELECT rowid FROM ticket_fts WHERE ticket_fts MATCH 'posting'").fetchall()
    assert len(hits) == 1
    with db.write_tx(conn):
        conn.execute("UPDATE ticket SET short_description = 'Login failure' WHERE ticket_id = 'incident:INC0000001'")
    assert conn.execute("SELECT COUNT(*) FROM ticket_fts WHERE ticket_fts MATCH 'posting'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM ticket_fts WHERE ticket_fts MATCH 'login'").fetchone()[0] == 1


def test_split_sql_handles_triggers():
    script = "CREATE TABLE a (x);\n-- c\nCREATE TRIGGER t AFTER INSERT ON a BEGIN\n  SELECT 1;\n  SELECT 2;\nEND;\n"
    stmts = db.split_sql(script)
    assert len(stmts) == 2
    assert stmts[1].startswith("CREATE TRIGGER")


def test_backup_and_prune(conn: sqlite3.Connection, tmp_path: Path):
    backups = tmp_path / "bk"
    for i in range(4):
        db.backup(conn, backups, reason=f"r{i}", keep=3)
    files = list(backups.glob("amkit-*.db"))
    assert len(files) == 3
    check = sqlite3.connect(str(sorted(files)[-1]))
    assert check.execute("PRAGMA user_version").fetchone()[0] == db.latest_version()
    check.close()


def test_migrate_backs_up_existing_db_before_new_migration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "amkit.db"
    c = db.connect(path)
    db.migrate(c, path, tmp_path / "backups")
    real = db.migrations()
    fake_v2 = (len(real) + 1, f"{len(real) + 1:03d}_extra.sql", "CREATE TABLE extra_thing (id INTEGER PRIMARY KEY);")
    monkeypatch.setattr(db, "migrations", lambda: [*real, fake_v2])
    result = db.migrate(c, path, tmp_path / "backups")
    assert result["applied"] == [fake_v2[1]]
    assert result["backup"] is not None and Path(result["backup"]).exists()
    assert db.user_version(c) == fake_v2[0]
    c.close()


def test_failed_migration_rolls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "amkit.db"
    c = db.connect(path)
    db.migrate(c, path, tmp_path / "backups")
    real = db.migrations()
    broken = (
        len(real) + 1,
        f"{len(real) + 1:03d}_broken.sql",
        "CREATE TABLE ok_table (id INTEGER);\nSELECT * FROM nope;",
    )
    monkeypatch.setattr(db, "migrations", lambda: [*real, broken])
    with pytest.raises(sqlite3.OperationalError):
        db.migrate(c, path, tmp_path / "backups")
    assert db.user_version(c) == len(real)
    assert c.execute("SELECT COUNT(*) FROM sqlite_master WHERE name = 'ok_table'").fetchone()[0] == 0
    c.close()


def test_restore_refused_while_serve_lock_held(conn: sqlite3.Connection, tmp_path: Path):
    backup_file = db.backup(conn, tmp_path / "bk", reason="t")
    lock = tmp_path / "serve.lock"
    lock.write_text(str(os.getpid()), encoding="utf-8")
    with pytest.raises(PreconditionFailed):
        db.restore(backup_file, tmp_path / "restored.db", tmp_path / "bk", lock)


def test_restore_ignores_stale_lock(conn: sqlite3.Connection, tmp_path: Path):
    backup_file = db.backup(conn, tmp_path / "bk", reason="t")
    lock = tmp_path / "serve.lock"
    lock.write_text("999999", encoding="utf-8")
    target = tmp_path / "restored.db"
    result = db.restore(backup_file, target, tmp_path / "bk", lock)
    assert result["user_version"] == db.latest_version()
    assert target.exists()


def test_pid_alive():
    assert db.pid_alive(os.getpid())
    assert not db.pid_alive(999999)


# --- review fixes -------------------------------------------------------------------------------------------


def _seed_meta(conn: sqlite3.Connection, data_class: str = "synthetic", fp: str = "fp1") -> None:
    with db.write_tx(conn):
        db.set_meta(conn, "data_class", data_class)
        db.set_meta(conn, "salt_fingerprint", fp)


def _add_ticket(conn: sqlite3.Connection, number: str, **cols) -> None:
    fields = {"ticket_id": f"incident:{number}", "kind": "incident", "number": number, "sys_updated_on": "2026-09-01"}
    fields.update(cols)
    names = ", ".join(fields)
    marks = ", ".join("?" for _ in fields)
    with db.write_tx(conn):
        conn.execute(f"INSERT INTO ticket ({names}) VALUES ({marks})", tuple(fields.values()))


def test_restore_oldest_of_full_backup_dir_keeps_data(tmp_path: Path):
    live = tmp_path / "amkit.db"
    conn = db.connect(live)
    db.enable_wal(conn)
    db.migrate(conn, live, None)
    _seed_meta(conn)
    _add_ticket(conn, "INC0000001")
    backups = tmp_path / "backups"
    oldest = db.backup(conn, backups, reason="day0", keep=7)
    for i in range(1, 7):
        db.backup(conn, backups, reason=f"day{i}", keep=7)
    _add_ticket(conn, "INC0000002")  # changes after the oldest backup
    conn.close()

    result = db.restore(oldest, live, backups, tmp_path / "serve.lock", expected_data_class="synthetic")
    assert result["user_version"] == db.latest_version()
    check = db.connect(live, readonly=True)
    assert check.execute("SELECT COUNT(*) FROM ticket").fetchone()[0] == 1
    check.close()
    assert oldest.exists() and oldest.stat().st_size > 0  # source protected from pruning
    assert Path(result["safety_backup"]).exists()


def test_restore_refuses_other_data_class_and_non_db(conn: sqlite3.Connection, tmp_path: Path):
    _seed_meta(conn, data_class="real")
    bk = db.backup(conn, tmp_path / "bk", reason="t")
    with pytest.raises(PreconditionFailed, match="data_class"):
        db.restore(bk, tmp_path / "x.db", tmp_path / "bk", tmp_path / "lock", expected_data_class="synthetic")
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"this is not sqlite")
    with pytest.raises(PreconditionFailed):
        db.restore(junk, tmp_path / "x.db", tmp_path / "bk", tmp_path / "lock")
    missing = tmp_path / "missing.db"
    with pytest.raises(PreconditionFailed):
        db.restore(missing, tmp_path / "x.db", tmp_path / "bk", tmp_path / "lock")
    assert not missing.exists()  # read-only open never creates the file


def test_restore_refuses_different_salt(conn: sqlite3.Connection, tmp_path: Path):
    _seed_meta(conn, fp="other")
    bk = db.backup(conn, tmp_path / "bk", reason="t")
    with pytest.raises(PreconditionFailed, match="salt"):
        db.restore(bk, tmp_path / "x.db", tmp_path / "bk", tmp_path / "lock", expected_salt_fingerprint="mine")


def test_backup_keep_must_be_positive(conn: sqlite3.Connection, tmp_path: Path):
    from amkit.errors import ValidationFailed

    with pytest.raises(ValidationFailed):
        db.backup(conn, tmp_path / "bk", keep=0)


def _seed_import_batch_with_reject(c: sqlite3.Connection) -> None:
    with db.write_tx(c):
        c.execute(
            "INSERT INTO import_batch (batch_id, file_name, file_sha256, mapping_name, mapping_sha256, load_mode,"
            " imported_at) VALUES (1, 'f.csv', 'sha', 'm', 'msha', 'delta', ?)",
            (db.utc_now(),),
        )
        c.execute("INSERT INTO row_reject (batch_id, row_num, reason) VALUES (1, 7, 'bad date')")


def test_rebuild_migration_preserves_cascade_children(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """SQLite's documented table-rebuild procedure must not cascade-delete children (row_reject ON DELETE CASCADE)."""
    path = tmp_path / "amkit.db"
    c = db.connect(path)
    db.migrate(c, path, tmp_path / "backups")
    _seed_import_batch_with_reject(c)
    ddl = c.execute("SELECT sql FROM sqlite_master WHERE name = 'import_batch'").fetchone()[0]
    new_ddl = ddl.replace("CREATE TABLE import_batch", "CREATE TABLE import_batch_new", 1)
    rebuild = f"{new_ddl};\nINSERT INTO import_batch_new SELECT * FROM import_batch;\nDROP TABLE import_batch;\n"
    rebuild += "ALTER TABLE import_batch_new RENAME TO import_batch;\n"
    real = db.migrations()
    monkeypatch.setattr(db, "migrations", lambda: [*real, (len(real) + 1, f"{len(real) + 1:03d}_rebuild.sql", rebuild)])
    result = db.migrate(c, path, tmp_path / "backups")
    assert result["applied"]
    assert c.execute("SELECT COUNT(*) FROM row_reject").fetchone()[0] == 1
    assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    c.close()


def test_migration_with_fk_violation_rolls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "amkit.db"
    c = db.connect(path)
    db.migrate(c, path, tmp_path / "backups")
    real = db.migrations()
    orphan = "INSERT INTO row_reject (batch_id, row_num, reason) VALUES (999, 1, 'orphan');"
    monkeypatch.setattr(db, "migrations", lambda: [*real, (len(real) + 1, f"{len(real) + 1:03d}_orphan.sql", orphan)])
    with pytest.raises(PreconditionFailed, match="foreign-key"):
        db.migrate(c, path, tmp_path / "backups")
    assert c.execute("SELECT COUNT(*) FROM row_reject").fetchone()[0] == 0
    assert db.user_version(c) == len(real)
    assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    c.close()


def test_migrate_refuses_newer_schema(tmp_path: Path):
    path = tmp_path / "amkit.db"
    c = db.connect(path)
    db.migrate(c, path, None)
    c.execute(f"PRAGMA user_version={db.latest_version() + 5}")
    with pytest.raises(PreconditionFailed, match="newer"):
        db.migrate(c, path, None)
    c.close()


def test_migrate_skips_version_applied_by_another_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "amkit.db"
    a = db.connect(path)
    db.migrate(a, path, None)
    real = db.migrations()
    extra = (len(real) + 1, f"{len(real) + 1:03d}_extra.sql", "CREATE TABLE extra_once (id INTEGER);")
    monkeypatch.setattr(db, "migrations", lambda: [*real, extra])
    b = db.connect(path)
    db.migrate(b, path, None)  # "other process" applies it first
    # Process A read the version BEFORE B committed: simulate that stale first read.
    original = db.user_version
    calls = {"n": 0}

    def stale_then_real(conn: sqlite3.Connection) -> int:
        calls["n"] += 1
        return len(real) if calls["n"] == 1 else original(conn)

    monkeypatch.setattr(db, "user_version", stale_then_real)
    result = db.migrate(a, path, None)  # must be a no-op, not a 'table already exists' error
    assert result["applied"] == []
    assert db.get_meta(a, "schema_version") == str(extra[0])
    a.close()
    b.close()


def test_v_ticket_takes_whole_label_row(conn: sqlite3.Connection):
    _add_ticket(conn, "INC0000009", open_hash="oh", resolved_hash="rh")
    with db.write_tx(conn):
        for run_id in ("r_open", "r_res"):
            conn.execute(
                "INSERT INTO ai_run (run_id, skill, skill_hash, schema_version, invoked_via, profile, status,"
                " started_at)"
                " VALUES (?, 'am-triage-batch', 'h', 1, 'workflow', 't', 'approved', ?)",
                (run_id, db.utc_now()),
            )
        conn.execute(
            "INSERT INTO ai_ticket_label (ticket_id, stage, run_id, input_hash, am_category, am_subcategory,"
            " symptom_key, misfiled_as, created_at) VALUES ('incident:INC0000009', 'open', 'r_open', 'oh', 'how_to',"
            " 'usage_question', 'how-to-export', 'request', ?)",
            (db.utc_now(),),
        )
        conn.execute(
            "INSERT INTO ai_ticket_label (ticket_id, stage, run_id, input_hash, am_category, created_at)"
            " VALUES ('incident:INC0000009', 'resolved', 'r_res', 'rh', 'integration', ?)",
            (db.utc_now(),),
        )
    row = conn.execute("SELECT * FROM v_ticket WHERE ticket_id = 'incident:INC0000009'").fetchone()
    assert row["am_category"] == "integration"
    assert row["am_subcategory"] is None
    assert row["symptom_key"] is None
    assert row["misfiled_as"] is None
    assert row["label_run_id"] == "r_res"
    current = {r["stage"]: r["run_id"] for r in conn.execute("SELECT * FROM v_label_current")}
    assert current == {"open": "r_open", "resolved": "r_res"}


def test_task_sla_without_start_time_is_unique(conn: sqlite3.Connection):
    _add_ticket(conn, "INC0000010")
    upsert = (
        "INSERT INTO task_sla (ticket_id, sla_name, has_breached) VALUES ('incident:INC0000010', 'P3 res', 0) "
        "ON CONFLICT (ticket_id, sla_name, start_time) DO UPDATE SET has_breached = excluded.has_breached"
    )
    with db.write_tx(conn):
        conn.execute(upsert)
        conn.execute(upsert)
    assert conn.execute("SELECT COUNT(*) FROM task_sla").fetchone()[0] == 1


def test_finding_unique_keys(conn: sqlite3.Connection):
    insert = (
        "INSERT INTO finding (finding_id, run_id, origin, stable_key, kind, title, status, created_at) "
        "VALUES (?, ?, ?, ?, 'renewal_risk', 't', ?, ?)"
    )
    with db.write_tx(conn):
        conn.execute(insert, ("f1", None, "rule", "renewal:C1", "active", db.utc_now()))
        conn.execute(insert, ("f2", None, "rule", "renewal:C1", "superseded", db.utc_now()))
    with pytest.raises(sqlite3.IntegrityError), db.write_tx(conn):
        conn.execute(insert, ("f3", None, "rule", "renewal:C1", "active", db.utc_now()))


def test_write_tx_surfaces_real_error_when_sqlite_already_rolled_back(tmp_path: Path):
    path = tmp_path / "tiny.db"
    c = db.connect(path)
    c.execute("CREATE TABLE blob_t (b BLOB)")
    page_count = c.execute("PRAGMA page_count").fetchone()[0]
    c.execute(f"PRAGMA max_page_count={page_count + 2}")
    with pytest.raises(sqlite3.Error, match="full"), db.write_tx(c):
        c.execute("INSERT INTO blob_t VALUES (zeroblob(1000000))")
    assert not c.in_transaction
    c.close()
