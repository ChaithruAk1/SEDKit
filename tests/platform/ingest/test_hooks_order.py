"""Registry-driven ingest keeps M1 behaviour: file order, hook timing, reresolve result keys, group alias rules."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

import pytest

from sed import db, modules
from sed.errors import ValidationFailed
from sed.ingest import loader
from sed.ingest.hooks import BaseIngestHooks, ImportSession, IngestHooks, RelinkUpdate, resolved_by, suggestion_kinds
from sed.ingest.loader import ImportOptions, reresolve, run_import
from sed.ingest.mapping import MappingSpec, load_all_mappings
from sed.ingest.resolve import Resolver
from sed.modules.ops.ingest_hooks import HOOKS

# Planned order of the seed-42, scale-0.02 synthetic inbox, recorded with the M1 loader (before the registry refactor).
RECORDED_ORDER = [
    "sys_user.csv",
    "Vendor_Master.xlsx",
    "cmdb_ci_business_app.xlsx",
    "cmdb_rel_ci.csv",
    "sys_user_group.csv",
    "Contracts_Register.xlsx",
    "License_Inventory.xlsx",
    *(f"License_Usage_2026-{m:02d}.xlsx" for m in (6, 7, 8)),
    "Budget_FY26.csv",
    "IT_Cost_Actuals_FY25_FY26.xlsx",
    "problem.csv",
    *(
        f"change_request_{y}-{m:02d}.csv"
        for y, m in [(2025, m) for m in range(3, 13)] + [(2026, m) for m in range(1, 9)]
    ),
    *(f"incident_2025-{m:02d}.csv" for m in range(3, 13)),
    "incident_2026-01.csv",
    "incident_2026-02.csv",
    "incident_2026-03_part1.csv",
    "incident_2026-03_part2.csv",
    *(f"incident_2026-{m:02d}.csv" for m in range(4, 8)),
    "incident_2026-08.xlsx",
    *(f"sc_req_item_{y}-{m:02d}.xlsx" for y, m in [(2025, m) for m in range(3, 13)] + [(2026, m) for m in range(1, 9)]),
    "incident_active_2026-09-01.csv",
    *(f"task_sla_{y}-{m:02d}.csv" for y, m in [(2025, m) for m in range(3, 13)] + [(2026, m) for m in range(1, 9)]),
    *(f"jira_export_{key}.csv" for key in ("BI", "CRM", "LDG", "ORN", "PLM", "WMS")),
    *(f"confluence_space_{key}" for key in ("HRCORE", "LEDGER", "ORION")),
]
RERESOLVE_KEYS = ["ticket", "contract", "license", "cost_line", "application", "unmapped_marked_resolved"]
INC_HEADER = ["number", "opened_at", "sys_updated_on", "state", "short_description", "assignment_group"]
CHG_HEADER = ["number", "opened_at", "sys_updated_on", "state", "short_description", "assignment_group"]


def test_planned_order_for_synthetic_inbox_is_unchanged(synthetic_inbox):
    paths = synthetic_inbox
    opts = ImportOptions(inbox=True, dry_run=True)
    conn = db.connect(paths.db, readonly=True)
    try:
        planned, unmatched, skipped = loader.plan_import(
            conn, paths, opts, load_all_mappings(paths), modules.ingest_targets(paths)
        )
    finally:
        conn.close()
    assert unmatched == [] and skipped == []
    assert len(RECORDED_ORDER) == 96
    assert [e.path.name for e in planned] == RECORDED_ORDER


def test_plan_order_uses_target_order_then_sort_key(tmp_path):
    targets = modules.ingest_targets()

    def entry(name: str, target: str, **extra: Any) -> loader.Entry:
        spec = MappingSpec.model_validate(
            {
                "name": name,
                "target": target,
                "load_mode": extra.pop("load_mode", "delta"),
                "match": {"glob": ["*"]},
                "fields": {"x": {"from": ["x"], "pii": "none"}},
                **extra,
            }
        )
        return loader.Entry(tmp_path / f"{name}.csv", "", False, spec=spec)

    entries = [
        entry("a_active", "ticket", load_mode="active_snapshot", constants={"kind": "incident"}),
        entry("b_incident", "ticket", constants={"kind": "incident"}),
        entry("c_problem", "ticket", constants={"kind": "problem"}),
        entry("d_pages", "doc_page"),
        entry("e_vendors", "vendor"),
        loader.Entry(tmp_path / "unmatched.csv", "", False),
    ]
    planned = [e.path.stem for e in loader.plan_order(entries, targets)]
    assert planned == ["e_vendors", "c_problem", "b_incident", "a_active", "d_pages"]
    with pytest.raises(ValidationFailed, match="no enabled module declares"):
        loader.plan_order([entry("z", "no_such_target")], targets)


def test_vendor_group_overrides_apply_before_incident_files(fresh_profile, tmp_path, write_csv, monkeypatch):
    paths = fresh_profile
    (paths.config / "ops").mkdir(parents=True, exist_ok=True)
    (paths.config / "ops" / "vendor_groups.yaml").write_text(
        "groups:\n  IT-FIN-L2: Nordwind Managed Services\n", encoding="utf-8"
    )
    files = [
        write_csv(
            tmp_path / "incident_2026-08.csv",
            INC_HEADER,
            [["INC0000001", "2026-08-20 09:00:00", "2026-08-20 10:00:00", "New", "Printer", "IT-FIN-L2"]],
        ),
        write_csv(
            tmp_path / "change_request_2026-08.csv",
            CHG_HEADER,
            [["CHG0000001", "2026-08-20 09:00:00", "2026-08-20 10:00:00", "Scheduled", "Patch", "IT-FIN-L2"]],
        ),
        write_csv(tmp_path / "sys_user_group.csv", ["name", "u_vendor"], [["IT-FIN-L2", "Keel Support Services"]]),
        write_csv(
            tmp_path / "Vendor_Master.csv",
            ["Vendor ID", "Vendor Name"],
            [["V001", "Nordwind Managed Services"], ["V002", "Keel Support Services"]],
        ),
    ]
    seen: list[tuple[str, str | None, Any]] = []
    original = HOOKS.before_target

    def spy(session: ImportSession, target: Any, spec: MappingSpec) -> None:
        original(session, target, spec)
        directory = session.state.get("ops.groups")
        seen.append((spec.name, spec.constants.get("kind"), directory.group_vendor.get("it fin l2")))

    monkeypatch.setattr(HOOKS, "before_target", spy)
    result = run_import(paths, ImportOptions(files=files, allow_unmanifested=True, move_files=False))
    assert result["summary"]["errors"] == 0, result["files"]
    assert [f["file"] for f in result["files"]] == [
        "Vendor_Master.csv",
        "sys_user_group.csv",
        "change_request_2026-08.csv",
        "incident_2026-08.csv",
    ]
    assert seen == [
        ("vendors_xlsx", None, None),
        ("servicenow_group", None, None),
        ("servicenow_change_request", "change_request", "V002"),  # from the group file, no override yet
        ("servicenow_incident", "incident", "V001"),  # override applied before the incident file is mapped
    ]
    conn = db.connect(paths.db, readonly=True)
    try:
        vendors = dict(conn.execute("SELECT number, vendor_id FROM ticket").fetchall())
    finally:
        conn.close()
    assert vendors == {"CHG0000001": "V002", "INC0000001": "V001"}


def test_reresolve_result_keys_unchanged(ops_profile_rw):
    first = reresolve(ops_profile_rw.paths)
    assert list(first) == RERESOLVE_KEYS
    assert all(isinstance(v, int) and v >= 0 for v in first.values())
    again = reresolve(ops_profile_rw.paths)
    assert list(again) == RERESOLVE_KEYS
    assert all(again[k] == 0 for k in RERESOLVE_KEYS[:-1])


def test_group_alias_to_unknown_group_is_accepted_but_app_ids_are_validated(fresh_profile):
    assert modules.entities()["group"].validate_ids is False
    conn = db.connect(fresh_profile.db)
    try:
        resolver = Resolver(conn)
        assert "group" not in resolver.valid and {"app", "vendor"} <= set(resolver.valid)
        resolver.add_alias("group", "Phantom Team", "No Such Group", "manual")
        resolver.add_alias("app", "Phantom App", "APM0000000", "manual")
        assert resolver.resolve("group", "phantom team") == "No Such Group"
        assert resolver.resolve("app", "Phantom App") is None
        assert dict(resolver.unmapped["app"]) == {"Phantom App": 1}
        resolver.register_ids("app", ["APM0000000"])
        assert resolver.resolve("app", "Phantom App") == "APM0000000"
        resolver.register_ids("group", ["ignored"])  # unvalidated entity: no id set is created
        assert "group" not in resolver.valid
        resolver.unmapped.clear()
        with db.write_tx(conn):
            resolver.flush(None)
        assert Resolver(conn).resolve("group", "PHANTOM TEAM") == "No Such Group"
    finally:
        conn.close()


def test_ops_hooks_satisfy_the_protocol_and_keep_alias_policies():
    assert isinstance(HOOKS, IngestHooks) and isinstance(BaseIngestHooks(), IngestHooks)
    assert modules.ingest_hooks() == [HOOKS]
    assert suggestion_kinds([HOOKS]) == ("app", "vendor", "group", "ci")
    assert resolved_by([HOOKS]) == {"app": ("app", "ci"), "ci": ("app", "ci")}

    class Extra(BaseIngestHooks):
        suggestion_kinds: ClassVar[tuple[str, ...]] = ("ci", "jira_project")
        resolved_by: ClassVar[Mapping[str, tuple[str, ...]]] = {"ci": ("ci",), "jira_project": ("jira_project", "app")}

    assert suggestion_kinds([HOOKS, Extra()]) == ("app", "vendor", "group", "ci", "jira_project")
    assert resolved_by([HOOKS, Extra()]) == {
        "app": ("app", "ci"),
        "ci": ("app", "ci"),
        "jira_project": ("jira_project", "app"),
    }
    assert suggestion_kinds([object()]) == () and resolved_by([object()]) == {}


def test_reresolve_applies_every_modules_relink_updates(fresh_profile, monkeypatch):
    calls: list[str] = []

    class Recorder(BaseIngestHooks):
        def relink(self, session: ImportSession) -> list[RelinkUpdate]:
            calls.append(type(session.resolver).__name__)
            return [RelinkUpdate("meta", "UPDATE meta SET value = value WHERE key = ?", [("data_class",)])]

    monkeypatch.setattr(modules, "ingest_hooks", lambda paths=None: [HOOKS, Recorder()])
    counts = reresolve(fresh_profile)
    assert calls == ["Resolver"]
    assert list(counts) == [*RERESOLVE_KEYS[:-1], "meta", "unmapped_marked_resolved"]
    assert counts["meta"] == 1 and counts["ticket"] == 0
