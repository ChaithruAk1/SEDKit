"""Module registry: declarations are valid, every reference imports, collisions are rejected."""

from __future__ import annotations

import dataclasses

import pytest

from sed import modules
from sed.errors import PreconditionFailed, ValidationFailed
from sed.ingest.resolve import ALIAS_KINDS
from sed.modules.contract import Module, NavItem, ReportDef, SkillDef


def test_builtin_modules_are_valid_and_every_reference_imports():
    assert modules.validate() == []
    for m in modules.installed():
        for ref in modules.declared_refs(m):
            modules.load_ref(ref)


def test_ops_is_enabled_by_default_and_nav_is_namespaced():
    assert [m.key for m in modules.enabled()] == ["ops"]
    for key, item in modules.nav():
        prefix = "/data" if key == "core" else f"/{key}"
        assert item.path == prefix or item.path.startswith(prefix + "/")
    assert [item.id for _, item in modules.nav()][:1] == ["ops.overview"]


def test_alias_kinds_match_resolver_and_schema():
    assert set(modules.alias_kinds()) == set(ALIAS_KINDS)
    assert set(modules.alias_kinds()) <= modules.SCHEMA_CHECKS["alias_kind"]
    for m in modules.installed():
        assert set(m.finding_kinds) <= modules.SCHEMA_CHECKS["finding_kind"]
        assert {r.key for r in m.reports} <= modules.SCHEMA_CHECKS["report_key"]


def test_module_tables_are_disjoint_from_core_tables():
    declared = [t for m in modules.installed() for t in m.tables]
    assert len(declared) == len(set(declared))
    assert not set(declared) & set(modules.CORE_TABLES + modules.PORTFOLIO_TABLES)


def test_report_and_mapping_lookup():
    module, rdef = modules.report("weekly")
    assert module.key == "ops" and rdef.spec == "ops/reports/weekly.yaml"
    index = modules.mapping_index()
    assert index["servicenow_incident"] == ("ops", "ops/mappings")
    with pytest.raises(ValidationFailed):
        modules.report("nope")


def _clone(key: str, **changes) -> Module:
    ops = modules.get("ops")
    base = Module(key=key, title=key.title(), mappings_dir=ops.mappings_dir, ingest_targets=ops.ingest_targets)
    return dataclasses.replace(base, **changes)


def test_duplicate_mapping_names_and_target_keys_are_rejected():
    ops = modules.get("ops")
    twin = dataclasses.replace(_clone("twin"), mappings_dir="ops/mappings")
    with modules.use_modules([ops, twin]):
        with pytest.raises(ValidationFailed, match="declared by modules"):
            modules.mapping_index()
        with pytest.raises(ValidationFailed, match="declared by modules"):
            modules.ingest_targets()


def test_validate_flags_collisions_and_bad_names():
    ops = modules.get("ops")
    bad = Module(
        key="Bad-Key",
        title="bad",
        reports=(ReportDef("weekly", "dup", "bad/reports/weekly.yaml", "x.y:build", ("week",)),),
        skills=(SkillDef("triage", None),),
        nav=(NavItem("other.page", "Page", "/elsewhere"),),
        legacy_cli=("report",),
        tables=("ticket", "meta"),
    )
    problems = "\n".join(modules.validate([ops, bad]))
    for fragment in (
        "module key 'Bad-Key'",
        "duplicate report 'weekly'",
        "must be prefixed 'sed-'",
        "nav id",
        "nav path",
        "reserved by the core",
        "duplicate table 'ticket'",
        "table 'meta' is core-owned",
    ):
        assert fragment in problems, fragment


def test_duplicate_entity_keys_are_rejected():
    from sed.modules.contract import EntityRef

    ops = modules.get("ops")
    other = Module(key="delivery", title="Delivery", entities=(EntityRef("app", "delivery_app", "id", "title"),))
    assert "duplicate entity 'app'" in "\n".join(modules.validate([ops, other]))
    with modules.use_modules([ops, other]), pytest.raises(ValidationFailed, match="declared differently"):
        modules.entities()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("enabled: []\n", frozenset()),
        ("enabled: [ops]\n", frozenset({"ops"})),
        ("title: no enabled key\n", frozenset({"ops"})),
        ("enabled: [\n", ValidationFailed),
        ("enabled: none\n", ValidationFailed),
        ("enabled:\n", ValidationFailed),
        ("enabled:\n  - ops: false\n", ValidationFailed),
    ],
)
def test_modules_yaml_is_validated(data_root, text, expected):
    from sed.paths import get_paths

    paths = get_paths("synthetic")
    paths.config.mkdir(parents=True, exist_ok=True)
    (paths.config / "modules.yaml").write_text(text, encoding="utf-8")
    if expected is ValidationFailed:
        with pytest.raises(ValidationFailed):
            modules.enabled_keys(paths)
    else:
        assert modules.enabled_keys(paths) == expected


def test_disabled_module_is_refused(tmp_path):
    ops = modules.get("ops")
    with modules.use_modules([ops], enabled_keys=set()):
        assert modules.enabled() == ()
        with pytest.raises(PreconditionFailed):
            modules.require_enabled(None, "ops")


def test_extra_modules_env_only_accepts_test_packages(monkeypatch):
    monkeypatch.setenv(modules.EXTRA_ENV, "os.path")
    with pytest.raises(ValidationFailed):
        modules.installed()
