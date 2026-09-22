from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from sed import db
from sed.ingest.aliases import alias_targets, resolve_target_id
from sed.ingest.freshness import data_as_of, import_freshness
from sed.settings import Settings, load_settings


def test_data_as_of_is_the_export_moment(ops_profile):
    conn = db.connect(ops_profile.paths.db, readonly=True)
    try:
        assert data_as_of(conn, load_settings(ops_profile.paths)) == date(2026, 9, 1)
        rows = import_freshness(conn)
        assert rows and all(r["files"] >= 1 for r in rows)
    finally:
        conn.close()


@pytest.fixture
def delta_only_store(tmp_path: Path):
    """A store loaded from delta exports alone: import batches carry no as-of, only the tickets know their dates."""
    path = tmp_path / "sed.db"
    conn = db.connect(path)
    db.migrate(conn, path, tmp_path / "backups")
    with db.write_tx(conn):
        conn.execute(
            "INSERT INTO import_batch "
            "(file_name, file_sha256, mapping_name, mapping_sha256, load_mode, status, as_of, imported_at) "
            "VALUES ('incident_export.xlsx', 'file-hash', 'servicenow_incident', 'map-hash', 'delta', 'completed', "
            "NULL, '2025-02-01T08:00:00Z')"
        )
        for number, updated in (("INC1", "2024-11-30T09:00:00Z"), ("INC2", "2025-01-27T17:20:00Z")):
            conn.execute(
                "INSERT INTO ticket (ticket_id, number, kind, sys_updated_on) VALUES (?, ?, 'incident', ?)",
                (number, number, updated),
            )
    yield conn
    conn.close()


def test_data_as_of_falls_back_to_the_newest_ticket(delta_only_store):
    """Delta exports carry no as-of of their own. Without this fallback the dashboard saw no date at all and used
    today's, so a store whose data is not from this week showed empty pages while the tickets sat in it."""
    assert data_as_of(delta_only_store, Settings()) == date(2025, 1, 27)


def test_settings_as_of_still_wins(delta_only_store):
    assert data_as_of(delta_only_store, Settings(as_of=date(2024, 12, 1))) == date(2024, 12, 1)


def test_alias_target_helpers(ops_profile):
    conn = db.connect(ops_profile.paths.db, readonly=True)
    try:
        vendor = ops_profile.ids["vendor_p2"]
        assert resolve_target_id(conn, "vendor", vendor) == vendor
        name = conn.execute("SELECT name FROM vendor WHERE vendor_id = ?", (vendor,)).fetchone()[0]
        assert resolve_target_id(conn, "vendor", name.upper()) == vendor
        hits = alias_targets(conn, "vendor", name[:5])
        assert {"id": vendor, "name": name} in hits
    finally:
        conn.close()
