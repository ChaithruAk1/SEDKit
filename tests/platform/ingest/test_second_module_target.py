"""A second module plugs its own ingest target, mapping and hooks into the same loader with no engine edits.

The `demo` module is defined in this file: a target writing a table the test creates, a DATA_DIR-only mapping in
config/demo/mappings/, and hooks that record their calls and contribute a reresolve re-link.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from sed import db, modules
from sed.errors import ValidationFailed
from sed.ingest.hooks import BaseIngestHooks, ImportSession, RelinkUpdate
from sed.ingest.loader import ImportOptions, reresolve, run_import
from sed.ingest.mapping import load_mapping, mapping_names
from sed.ingest.target import Ctx, Reject, Target
from sed.modules.contract import Module

CALLS: list[tuple[str, str]] = []


def build_widget(rec: dict[str, Any], raw: dict[str, Any], ctx: Ctx) -> dict[str, Any]:
    if not rec.get("widget_id"):
        raise Reject("missing widget_id")
    return {"widget_id": str(rec["widget_id"]), "label": rec.get("label"), "size": rec.get("size"), "is_deleted": 0}


def after_widget(ctx: Ctx, rows: list[dict[str, Any]]) -> None:
    ctx.state.setdefault("demo.loaded", []).extend(r["widget_id"] for r in rows)


class DemoHooks(BaseIngestHooks):
    def session_start(self, session: ImportSession) -> None:
        CALLS.append(("session_start", ""))
        session.state["demo.session"] = True

    def before_target(self, session: ImportSession, target: Target, spec: Any) -> None:
        CALLS.append(("before_target", target.name))

    def relink(self, session: ImportSession) -> list[RelinkUpdate]:
        rows = [(r[0],) for r in session.conn.execute("SELECT widget_id FROM demo_widget WHERE label IS NULL")]
        return [RelinkUpdate("demo_widget", "UPDATE demo_widget SET label = 'unlabelled' WHERE widget_id = ?", rows)]


DEMO_TARGETS = {
    "demo_widget": Target(
        "demo_widget",
        "demo_widget",
        ("widget_id",),
        ("widget_id", "label", "size", "is_deleted"),
        build_widget,
        soft_delete=True,
        after_load=after_widget,
        order=45,  # between two ops targets
    )
}
DEMO_HOOKS = DemoHooks()
VENDOR_TWIN = {"vendor": dataclasses.replace(DEMO_TARGETS["demo_widget"], name="vendor")}
DEMO = Module(
    key="demo",
    title="Demo (test module)",
    mappings_dir="demo/mappings",
    ingest_targets=f"{__name__}:DEMO_TARGETS",
    ingest_hooks=f"{__name__}:DEMO_HOOKS",
    tables=("demo_widget",),
)
MAPPING = """\
name: demo_widgets
target: demo_widget
load_mode: full_snapshot
match:
  glob: ["demo_widgets*.csv"]
fields:
  widget_id: {from: [Widget ID, id], pii: none, required: true}
  label:     {from: [Label], pii: none}
  size:      {from: [Size], transform: float, pii: none}
"""


@pytest.fixture
def demo_profile(fresh_profile):
    CALLS.clear()
    conn = db.connect(fresh_profile.db)
    try:
        with db.write_tx(conn):
            conn.execute(
                "CREATE TABLE demo_widget (widget_id TEXT PRIMARY KEY, label TEXT, size REAL, is_deleted INTEGER "
                "NOT NULL DEFAULT 0, last_batch_id INTEGER REFERENCES import_batch (batch_id))"
            )
    finally:
        conn.close()
    folder = fresh_profile.config / "demo" / "mappings"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "demo_widgets.yaml").write_text(MAPPING, encoding="utf-8")
    with modules.use_modules((modules.get("ops"), DEMO)):
        yield fresh_profile


def _rows(paths, sql: str) -> list[tuple[Any, ...]]:
    conn = db.connect(paths.db, readonly=True)
    try:
        return [tuple(r) for r in conn.execute(sql).fetchall()]
    finally:
        conn.close()


def test_demo_module_is_valid_and_its_data_dir_mapping_is_discovered(demo_profile):
    assert modules.validate() == []
    assert "demo_widgets" in mapping_names(demo_profile)
    assert "demo_widgets" not in mapping_names(None)  # DATA_DIR-only
    assert "servicenow_incident" in mapping_names(demo_profile)
    assert modules.mapping_index(demo_profile)["demo_widgets"] == ("demo", "demo/mappings")
    assert load_mapping("demo_widgets", demo_profile).target == "demo_widget"
    assert set(modules.ingest_targets(demo_profile)) >= {"demo_widget", "ticket", "vendor"}
    assert modules.ingest_hooks(demo_profile)[-1] is DEMO_HOOKS


def test_demo_csv_imports_through_the_same_loader(demo_profile, tmp_path, write_csv):
    widgets = write_csv(
        tmp_path / "demo_widgets_2026-09.csv",
        ["Widget ID", "Label", "Size"],
        [["W-1", "Alpha", "1.5"], ["W-2", "", "2"], ["", "no id", "3"]],
    )
    vendors = write_csv(tmp_path / "Vendor_Master.csv", ["Vendor ID", "Vendor Name"], [["V001", "Nordwind"]])
    groups = write_csv(tmp_path / "sys_user_group.csv", ["name", "u_vendor"], [["IT-FIN-L2", "Nordwind"]])
    result = run_import(
        demo_profile, ImportOptions(files=[groups, widgets, vendors], allow_unmanifested=True, move_files=False)
    )
    assert result["summary"]["errors"] == 0, result["files"]
    # Target.order 45 sits between ops ci_rel (40) and assignment_group (50).
    assert [f["file"] for f in result["files"]] == [
        "Vendor_Master.csv",
        "demo_widgets_2026-09.csv",
        "sys_user_group.csv",
    ]
    demo = next(f for f in result["files"] if f["mapping"] == "demo_widgets")
    assert (demo["status"], demo["target"], demo["inserted"], demo["rows_rejected"]) == (
        "completed",
        "demo_widget",
        2,
        1,
    )
    assert CALLS == [
        ("session_start", ""),
        ("before_target", "vendor"),
        ("before_target", "demo_widget"),
        ("before_target", "assignment_group"),
    ]
    batch = _rows(
        demo_profile,
        "SELECT batch_id, mapping_name, status, rows_read FROM import_batch WHERE mapping_name = 'demo_widgets'",
    )
    assert len(batch) == 1 and batch[0][1:] == ("demo_widgets", "completed", 3)
    assert _rows(demo_profile, "SELECT widget_id, label, size, last_batch_id FROM demo_widget ORDER BY widget_id") == [
        ("W-1", "Alpha", 1.5, batch[0][0]),
        ("W-2", None, 2.0, batch[0][0]),
    ]
    rejects = _rows(demo_profile, f"SELECT reason FROM row_reject WHERE batch_id = {batch[0][0]}")
    assert rejects == [("missing widget_id",)]

    # full_snapshot soft delete and the reresolve dispatcher work for the module's own table too.
    smaller = write_csv(tmp_path / "demo_widgets_2026-10.csv", ["Widget ID", "Label"], [["W-2", ""]])
    result = run_import(
        demo_profile, ImportOptions(files=[smaller], allow_unmanifested=True, move_files=False, force=True)
    )
    assert result["files"][0]["dq"]["soft_deleted"] == 1
    assert _rows(demo_profile, "SELECT widget_id, is_deleted FROM demo_widget ORDER BY 1") == [("W-1", 1), ("W-2", 0)]
    counts = reresolve(demo_profile)
    assert list(counts) == [
        "ticket",
        "contract",
        "license",
        "cost_line",
        "application",
        "demo_widget",
        "unmapped_marked_resolved",
    ]
    assert counts["demo_widget"] == 1
    assert _rows(demo_profile, "SELECT label FROM demo_widget WHERE widget_id = 'W-2'") == [("unlabelled",)]


def test_duplicate_mapping_name_across_modules_is_rejected(demo_profile, tmp_path, write_csv):
    clash = demo_profile.config / "demo" / "mappings" / "servicenow_incident.yaml"
    clash.write_text(MAPPING.replace("name: demo_widgets", "name: servicenow_incident"), encoding="utf-8")
    with pytest.raises(ValidationFailed, match="declared by modules"):
        mapping_names(demo_profile)
    widgets = write_csv(tmp_path / "demo_widgets_x.csv", ["Widget ID"], [["W-9"]])
    with pytest.raises(ValidationFailed, match="declared by modules"):
        run_import(demo_profile, ImportOptions(files=[widgets], allow_unmanifested=True, move_files=False))
    assert _rows(demo_profile, "SELECT COUNT(*) FROM import_batch") == [(0,)]


def test_duplicate_target_key_across_modules_is_rejected(fresh_profile, tmp_path, write_csv):
    twin = dataclasses.replace(DEMO, key="twin", mappings_dir=None, ingest_targets=f"{__name__}:VENDOR_TWIN", tables=())
    vendors = write_csv(tmp_path / "Vendor_Master.csv", ["Vendor ID", "Vendor Name"], [["V001", "Nordwind"]])
    with modules.use_modules((modules.get("ops"), twin)):
        with pytest.raises(ValidationFailed, match="declared by modules"):
            modules.ingest_targets(fresh_profile)
        with pytest.raises(ValidationFailed, match="declared by modules"):
            run_import(fresh_profile, ImportOptions(files=[vendors], allow_unmanifested=True, move_files=False))
    assert _rows(fresh_profile, "SELECT COUNT(*) FROM vendor") == [(0,)]


def test_disabled_module_targets_and_mappings_are_not_used(demo_profile, tmp_path, write_csv):
    widgets = write_csv(tmp_path / "demo_widgets_2026-09.csv", ["Widget ID"], [["W-1"]])
    with modules.use_modules((modules.get("ops"), DEMO), enabled_keys={"ops"}):
        assert "demo_widgets" not in mapping_names(demo_profile)
        assert "demo_widget" not in modules.ingest_targets(demo_profile)
        result = run_import(demo_profile, ImportOptions(files=[widgets], allow_unmanifested=True, move_files=False))
    assert result["files"][0]["status"] == "error" and "No mapping matches" in result["files"][0]["error"]["message"]
    assert CALLS == []
