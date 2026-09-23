"""Clearing imported data by source (`sed.dataclear`).

The promise is narrow and worth pinning: the rows one source imported go, everything else stays — including the
saved layouts, which live in the same store and are setup rather than data.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sed import db
from sed.dataclear import SOURCES, clear, counts
from sed.errors import ValidationFailed
from sed.layouts import list_layouts, save_layout


@pytest.fixture
def store(tmp_path: Path):
    path = tmp_path / "sed.db"
    conn = db.connect(path)
    db.migrate(conn, path, tmp_path / "backups")
    with db.write_tx(conn):
        conn.execute("INSERT INTO vendor (vendor_id, name) VALUES ('V1', 'A vendor')")
        conn.execute("INSERT INTO application (app_id, name, primary_vendor_id) VALUES ('A1', 'An app', 'V1')")
        for number in ("INC1", "INC2"):
            conn.execute(
                "INSERT INTO ticket (ticket_id, number, kind, app_id, vendor_id, sys_updated_on) "
                "VALUES (?, ?, 'incident', 'A1', 'V1', '2026-01-01T00:00:00Z')",
                (number, number),
            )
        conn.execute("INSERT INTO work_item (issue_key, project_key, app_id) VALUES ('PRJ-1', 'PRJ', 'A1')")
        conn.execute(
            "INSERT INTO contract (contract_id, vendor_id, app_id) VALUES ('C1', 'V1', 'A1')",
        )
        # Setup, not data: it must survive every clear.
        save_layout(conn, "ops.tickets", "Weekly review", ["number", "priority"])
    yield conn
    conn.close()


def test_counts_lists_every_source(store):
    rows = {s["key"]: s for s in counts(store)}
    assert set(rows) == {s.key for s in SOURCES}
    assert rows["servicenow"]["tables"]["ticket"] == 2
    assert rows["jira"]["rows"] == 1


def test_clearing_one_source_leaves_the_others(store):
    with db.write_tx(store):
        result = clear(store, "jira")
    assert result["deleted"]["work_item"] == 1
    assert store.execute("SELECT COUNT(*) FROM work_item").fetchone()[0] == 0
    assert store.execute("SELECT COUNT(*) FROM ticket").fetchone()[0] == 2


def test_clearing_servicenow_removes_tickets_and_what_hangs_off_them(store):
    with db.write_tx(store):
        store.execute(
            "INSERT INTO ai_run (run_id, skill, skill_hash, schema_version, invoked_via, profile, status, started_at) "
            "VALUES ('R1', 'sed-triage-batch', 'h', 1, 'manual', 'test', 'approved', '2026-01-01T00:00:00Z')"
        )
        store.execute(
            "INSERT INTO ai_ticket_label (ticket_id, run_id, stage, input_hash, am_category, created_at) "
            "VALUES ('INC1', 'R1', 'resolved', 'h', 'access', '2026-01-01T00:00:00Z')"
        )
    with db.write_tx(store):
        clear(store, "servicenow")
    assert store.execute("SELECT COUNT(*) FROM ticket").fetchone()[0] == 0
    # The label cascaded with its ticket rather than being left pointing at nothing.
    assert store.execute("SELECT COUNT(*) FROM ai_ticket_label").fetchone()[0] == 0


def test_clearing_applications_unlinks_rather_than_failing(store):
    """Tickets and contracts point at applications and vendors. Clearing the portfolio must not be blocked by them,
    and must not take them with it — the next import resolves the links again."""
    with db.write_tx(store):
        result = clear(store, "portfolio")
    assert result["deleted"] == {"application": 1, "vendor": 1}
    assert store.execute("SELECT COUNT(*) FROM ticket").fetchone()[0] == 2
    assert store.execute("SELECT COUNT(*) FROM contract").fetchone()[0] == 1
    assert store.execute("SELECT app_id, vendor_id FROM ticket WHERE ticket_id = 'INC1'").fetchone()[0] is None


def test_layouts_survive_every_clear(store):
    for source in SOURCES:
        with db.write_tx(store):
            clear(store, source.key)
    assert [row["name"] for row in list_layouts(store, "ops.tickets")] == ["Weekly review"]


def test_an_unknown_source_is_refused(store):
    with pytest.raises(ValidationFailed), db.write_tx(store):
        clear(store, "nonesuch")
