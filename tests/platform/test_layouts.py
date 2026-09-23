"""Named column layouts (schema 011, `sed.layouts`).

A layout holds column keys in display order and nothing else: no ticket data ever reaches this table.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sed import db
from sed.errors import ValidationFailed
from sed.layouts import delete_layout, list_layouts, save_layout, set_default

TABLE = "ops.tickets"


@pytest.fixture
def conn(tmp_path: Path):
    path = tmp_path / "sed.db"
    c = db.connect(path)
    db.migrate(c, path, tmp_path / "backups")
    yield c
    c.close()


def test_saving_then_listing_a_layout(conn):
    with db.write_tx(conn):
        saved = save_layout(conn, TABLE, "Weekly review", ["number", "priority", "opened_at"])
    assert saved["columns"] == ["number", "priority", "opened_at"]
    assert saved["is_default"] is False
    assert [r["name"] for r in list_layouts(conn, TABLE)] == ["Weekly review"]


def test_saving_the_same_name_replaces_its_columns(conn):
    """A layout is identified by its name on its table: saving again is an edit, not a duplicate."""
    with db.write_tx(conn):
        save_layout(conn, TABLE, "Weekly review", ["number", "priority"])
        save_layout(conn, TABLE, "Weekly review", ["number", "app_name", "resolved_at"])
    rows = list_layouts(conn, TABLE)
    assert len(rows) == 1
    assert rows[0]["columns"] == ["number", "app_name", "resolved_at"]


def test_column_order_is_kept_and_repeats_dropped(conn):
    with db.write_tx(conn):
        saved = save_layout(conn, TABLE, "Odd", ["c", "a", "c", "b", "a"])
    assert saved["columns"] == ["c", "a", "b"]


def test_only_one_default_per_table(conn):
    with db.write_tx(conn):
        first = save_layout(conn, TABLE, "One", ["number"], make_default=True)
        second = save_layout(conn, TABLE, "Two", ["number", "priority"])
        set_default(conn, second["layout_id"])
    rows = {r["name"]: r["is_default"] for r in list_layouts(conn, TABLE)}
    assert rows == {"One": False, "Two": True}
    assert first["is_default"] is True  # it was, at the moment it was saved


def test_the_default_is_listed_first(conn):
    with db.write_tx(conn):
        save_layout(conn, TABLE, "Alpha", ["number"])
        save_layout(conn, TABLE, "Zulu", ["number"], make_default=True)
    assert [r["name"] for r in list_layouts(conn, TABLE)] == ["Zulu", "Alpha"]


def test_layouts_of_other_tables_are_not_listed(conn):
    with db.write_tx(conn):
        save_layout(conn, TABLE, "Mine", ["number"])
        save_layout(conn, "sap.changes", "Theirs", ["change_id"])
    assert [r["name"] for r in list_layouts(conn, TABLE)] == ["Mine"]


def test_deleting_a_layout(conn):
    with db.write_tx(conn):
        saved = save_layout(conn, TABLE, "Temporary", ["number"])
        delete_layout(conn, saved["layout_id"])
    assert list_layouts(conn, TABLE) == []


@pytest.mark.parametrize(
    ("name", "columns"),
    [("", ["number"]), ("   ", ["number"]), ("ok", []), ("x" * 61, ["number"]), ("ok", [""])],
)
def test_a_layout_that_cannot_be_used_is_refused(conn, name: str, columns: list[str]):
    with pytest.raises(ValidationFailed), db.write_tx(conn):
        save_layout(conn, TABLE, name, columns)


def test_default_and_delete_refuse_an_unknown_id(conn):
    for action in (set_default, delete_layout):
        with pytest.raises(ValidationFailed), db.write_tx(conn):
            action(conn, 9999)
