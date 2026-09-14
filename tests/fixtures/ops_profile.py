"""Deterministic ops profile shared by platform and module tests.

`build_ops_profile` generates scale-0.02 synthetic exports with a pinned salt, imports them and refreshes rule findings
as of 2026-09-01, so pseudonyms, hashes and findings are identical on every machine and run.

Fixtures:
* `ops_profile` (session): read-only use. Teardown fails if the database state changed during the session.
* `ops_profile_rw` (function): a private copy (SQLite backup API) for tests that write (snapshots, AI runs, aliases).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import pytest

PINNED_SALT = "5a" * 32
AS_OF = date(2026, 9, 1)
PERIODS = {"week": "2026-W35", "month": "2026-08", "quarter": "2026-Q3"}
REPO = Path(__file__).resolve().parents[2]

EXCLUDED_TABLES = {"meta", "import_batch", "row_reject", "sqlite_sequence", "ticket_fts"}
EXCLUDED_COLUMN_SUFFIXES = ("_at", "batch_id")
EXCLUDED_COLUMNS = {("finding", "finding_id")}


@dataclass
class OpsProfile:
    paths: Any
    ground_truth: Path
    periods: dict[str, str] = field(default_factory=lambda: dict(PERIODS))
    ids: dict[str, str] = field(default_factory=dict)


def build_ops_profile(root: Path, *, scale: float = 0.02, seed: int = 42) -> OpsProfile:
    from sed import bootstrap, db
    from sed.analytics import refresh_rule_findings
    from sed.ingest.loader import ImportOptions, run_import
    from sed.paths import get_paths
    from sed.synth.generate import SynthOptions, generate

    root.mkdir(parents=True, exist_ok=True)
    claude_md = root / "CLAUDE.md"
    shutil.copyfile(REPO / "CLAUDE.md", claude_md)
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("SED_DATA_ROOT", str(root / "sed-data"))
        mp.setenv("SED_CLAUDE_SETTINGS_LOCAL", str(root / "settings.local.json"))
        mp.setenv("SED_CLAUDE_MD", str(claude_md))
        mp.delenv("SED_PROFILE", raising=False)
        paths = get_paths("synthetic")
        paths.salt_file.parent.mkdir(parents=True, exist_ok=True)
        paths.salt_file.write_text(PINNED_SALT + "\n", encoding="utf-8")
        bootstrap.init_profile(paths, write_claude_settings=False)
        generate(paths, SynthOptions(seed=seed, as_of=AS_OF, scale=scale))
        result = run_import(paths, ImportOptions(inbox=True))
        if result["summary"]["errors"]:
            raise RuntimeError(f"ops_profile import failed: {result['summary']}")
        conn = db.connect(paths.db)
        try:
            refresh_rule_findings(conn, paths, AS_OF)
        finally:
            conn.close()
        paths.ensure()
    truth = json.loads((paths.ground_truth / "patterns.json").read_text(encoding="utf-8"))
    conn = db.connect(paths.db, readonly=True)
    try:
        row = conn.execute("SELECT vendor_id FROM vendor WHERE name = ?", (truth["P2"]["vendor"],)).fetchone()
    finally:
        conn.close()
    return OpsProfile(paths=paths, ground_truth=paths.ground_truth, ids={"vendor_p2": row[0] if row else ""})


def _tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name").fetchall()
    return [r[0] for r in rows if not r[0].startswith("ticket_fts_") and r[0] not in {"sqlite_sequence"}]


def _autoincrement_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone()
    if not sql or "AUTOINCREMENT" not in (sql[0] or "").upper():
        return set()
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})") if r[5] == 1}


def _digest_rows(conn: sqlite3.Connection, table: str, columns: list[str]) -> tuple[int, str]:
    quoted = ", ".join(f'"{c}"' for c in columns)
    rows = [
        json.dumps(list(r), ensure_ascii=False, sort_keys=True, default=str)
        for r in conn.execute(f'SELECT {quoted} FROM "{table}"')
    ]
    rows.sort()
    h = hashlib.sha256()
    for line in rows:
        h.update(line.encode("utf-8"))
        h.update(b"\n")
    return len(rows), h.hexdigest()


def baseline_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    auto = _autoincrement_columns(conn, table)
    cols = []
    for r in conn.execute(f'PRAGMA table_info("{table}")'):
        name = r[1]
        if name.endswith(EXCLUDED_COLUMN_SUFFIXES) or name in auto or (table, name) in EXCLUDED_COLUMNS:
            continue
        cols.append(name)
    return cols


def baseline_digest(conn: sqlite3.Connection, tables_spec: dict[str, Any] | None = None) -> dict[str, Any]:
    """Order-independent digest of imported content per table and column.

    With `tables_spec` (a previously written baseline), only its tables and columns are digested, so tables and
    columns added later do not change the result.
    """
    out: dict[str, Any] = {"version": 1, "tables": {}}
    existing = set(_tables(conn))
    if tables_spec is None:
        names = [t for t in _tables(conn) if t not in EXCLUDED_TABLES]
        spec = {t: baseline_columns(conn, t) for t in names}
    else:
        spec = {t: v["columns"] for t, v in tables_spec["tables"].items() if t in existing}
    for table, columns in spec.items():
        rows, digest = _digest_rows(conn, table, columns)
        out["tables"][table] = {"columns": columns, "rows": rows, "digest": digest}
    return out


def state_digest(conn: sqlite3.Connection) -> str:
    """Digest of every table (except timestamps) used to prove a session fixture stayed read-only."""
    h = hashlib.sha256()
    for table in _tables(conn):
        cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")') if not r[1].endswith("_at")]
        rows, digest = _digest_rows(conn, table, cols)
        h.update(f"{table}:{rows}:{digest}\n".encode())
    return h.hexdigest()


def _state(paths: Any) -> str:
    from sed import db

    conn = db.connect(paths.db, readonly=True)
    try:
        return state_digest(conn)
    finally:
        conn.close()


@pytest.fixture(scope="session")
def ops_profile(tmp_path_factory: pytest.TempPathFactory) -> Iterator[OpsProfile]:
    profile = build_ops_profile(tmp_path_factory.mktemp("ops_profile"))
    before = _state(profile.paths)
    yield profile
    after = _state(profile.paths)
    if before != after:
        pytest.fail("A test wrote to the shared session ops_profile; use ops_profile_rw for writes.")


@pytest.fixture
def ops_profile_rw(ops_profile: OpsProfile, tmp_path: Path) -> OpsProfile:
    from sed import db
    from sed.paths import Paths

    src = ops_profile.paths
    dst = Paths(profile=src.profile, data_dir=tmp_path / "rw" / src.profile)
    dst.ensure()
    source = sqlite3.connect(str(src.db))
    target = sqlite3.connect(str(dst.db))
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    conn = db.connect(dst.db)
    try:
        db.enable_wal(conn)
    finally:
        conn.close()
    shutil.copyfile(src.salt_file, dst.salt_file)
    if src.config.is_dir():
        shutil.copytree(src.config, dst.config, dirs_exist_ok=True)
    shutil.copytree(src.ground_truth, dst.ground_truth, dirs_exist_ok=True)
    return OpsProfile(
        paths=dst, ground_truth=dst.ground_truth, periods=dict(ops_profile.periods), ids=dict(ops_profile.ids)
    )
