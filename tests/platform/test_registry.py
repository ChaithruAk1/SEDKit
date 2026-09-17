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


def test_builtin_modules_are_enabled_by_default_and_nav_is_namespaced():
    assert [m.key for m in modules.enabled()] == ["ops", "sap"]
    for key, item in modules.nav():
        if key == "core":
            assert item.path.strip("/").split("/")[0] in modules.CORE_PAGE_KEYS
        else:
            assert item.path == f"/{key}" or item.path.startswith(f"/{key}/")
    assert sorted(modules.CORE_PAGE_KEYS) == ["data", "review", "runs"]
    assert [item.id for _, item in modules.nav()][:1] == ["ops.overview"]


def test_alias_and_finding_kinds_come_from_the_modules():
    assert set(modules.alias_kinds()) == set(ALIAS_KINDS)
    kinds = modules.finding_kinds()
    assert len(kinds) == len(set(kinds)) and {"renewal_risk", "report_section"} <= set(kinds)
    assert modules.get("ops").rule_findings == "sed.analytics:compute_rule_findings"


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
        finding_kinds=("renewal_risk",),
    )
    rules_without_kinds = Module(key="norules", title="No kinds", rule_findings="x.y:compute")
    page_clash = Module(key="runs", title="Runs")
    problems = "\n".join(modules.validate([ops, bad, rules_without_kinds, page_clash]))
    for fragment in (
        "module key 'Bad-Key'",
        "duplicate report 'weekly'",
        "must be prefixed 'sed-'",
        "nav id",
        "nav path",
        "reserved by the core",
        "duplicate table 'ticket'",
        "table 'meta' is core-owned",
        "duplicate finding kind 'renewal_risk'",
        "norules: rule_findings needs the finding kinds",
        "module key 'runs' is reserved by the core page #/runs",
    ):
        assert fragment in problems, fragment


def test_extension_points_are_declared_by_the_owner_and_contributions_follow_dependencies():
    from sed.modules.contract import Extension

    ops = modules.get("ops")
    assert ops.extension_points == ("triage",)
    good = Module(key="good", title="Good", depends_on=("ops",), extensions=(Extension("ops.triage", "x.y:ext"),))
    assert modules.validate([ops, good]) == []
    bad = Module(
        key="bad",
        title="Bad",
        extension_points=("Bad Name", "dup", "dup"),
        extensions=(
            Extension("ops.triage", "x.y:ext"),  # ops is not a dependency
            Extension("bad.nothing", "x.y:ext"),  # not declared by the owner
            Extension("bad.nothing", "x.y:other"),
            Extension("ghost.triage", "not a ref"),
        ),
    )
    problems = "\n".join(modules.validate([ops, bad]))
    for fragment in (
        "bad: invalid extension point name 'Bad Name'",
        "bad: extension point 'dup' is declared twice",
        "bad: extension 'ops.triage' needs 'ops' as this module or a dependency",
        "bad: module 'bad' declares no extension point 'nothing'",
        "bad: extension point 'bad.nothing' is contributed to twice",
        "bad: extension 'ghost.triage' needs 'ghost'",
        "bad: invalid import reference 'not a ref'",
    ):
        assert fragment in problems, fragment


def test_extensions_follow_enablement():
    assert [key for key, _ in modules.extensions("ops.triage")] == ["sap"]
    ops, sap = modules.get("ops"), modules.get("sap")
    with modules.use_modules([ops, sap], {"ops"}):
        assert modules.extensions("ops.triage") == []


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
        ("title: no enabled key\n", frozenset({"ops", "sap"})),
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
