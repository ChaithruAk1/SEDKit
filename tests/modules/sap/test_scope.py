"""SAP scope: config validation, layered overrides and the ticket predicate (groups, categories, custom fields, areas,
landscapes)."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from typing import Any

import pytest
from pydantic import ValidationError

from sed.errors import ValidationFailed
from sed.modules.sap.scope import UNASSIGNED, UNKNOWN, Scope, ScopeConfig, load_scope
from tests.modules.sap.conftest import write_sap_config

CONFIG: dict[str, Any] = {
    "areas": [{"code": "fi_co", "label": "FI/CO"}, {"code": "ewm", "label": "EWM"}, {"code": "sd", "label": "SD"}],
    "groups": [{"name": "G-FICO", "area": "fi_co"}, {"name": "G-EWM", "area": "ewm"}],
    "categories": ["SAP"],
    "custom_fields": [{"field": "u_sap_component"}],
    "landscapes": [
        {"code": "ecc", "label": "ECC", "apps": ["APP-ECC"]},
        {"code": "s4", "label": "S/4", "apps": ["APP-S4"]},
    ],
}

# ticket_id, assignment_group, category, raw_keep_json, app_id
ROWS = [
    ("fico-ecc", "G-FICO", "Software", None, "APP-ECC"),
    ("ewm-s4", "G-EWM", "Software", None, "APP-S4"),
    ("ewm-other-app", "G-EWM", "Software", None, "APP-OTHER"),
    ("desk-category", "DESK", "SAP", None, "APP-S4"),
    ("desk-field", "DESK", "Software", json.dumps({"u_sap_component": "SD-BIL"}), None),
    ("desk-empty-field", "DESK", "Software", json.dumps({"u_sap_component": ""}), "APP-ECC"),
    ("no-group-category", None, "SAP", None, "APP-ECC"),
    ("not-sap", "DESK", "Software", json.dumps({"u_other": "x"}), "APP-ECC"),
]
SAP = {"fico-ecc", "ewm-s4", "ewm-other-app", "desk-category", "desk-field", "no-group-category"}


def _scope(**changes: Any) -> Scope:
    return Scope(ScopeConfig.model_validate({**CONFIG, **changes}))


def _select(scope: Scope, **kw: Any) -> set[str]:
    sql, params = scope.ticket_sql(**kw)
    with closing(sqlite3.connect(":memory:")) as conn:
        conn.execute(
            "CREATE TABLE ticket (ticket_id TEXT, assignment_group TEXT, category TEXT, raw_keep_json TEXT, "
            "app_id TEXT)"
        )
        conn.executemany("INSERT INTO ticket VALUES (?, ?, ?, ?, ?)", ROWS)
        return {r[0] for r in conn.execute(f"SELECT ticket_id FROM ticket t WHERE {sql.format(t='t')}", params)}


def test_scope_selects_groups_categories_and_non_empty_custom_fields():
    assert _select(_scope()) == SAP


def test_each_criterion_alone():
    assert _select(_scope(categories=[], custom_fields=[])) == {"fico-ecc", "ewm-s4", "ewm-other-app"}
    assert _select(_scope(groups=[], custom_fields=[])) == {"desk-category", "no-group-category"}
    assert _select(_scope(groups=[], categories=[])) == {"desk-field"}
    assert (
        _select(_scope(groups=[], categories=[], custom_fields=[{"field": "u_sap_component", "values": ["MM"]}]))
        == set()
    )
    assert _select(
        _scope(groups=[], categories=[], custom_fields=[{"field": "u_sap_component", "values": ["SD-BIL", "MM"]}])
    ) == {"desk-field"}


def test_areas_partition_the_scope():
    scope = _scope()
    assert _select(scope, area="fi_co") == {"fico-ecc"}
    assert _select(scope, area="ewm") == {"ewm-s4", "ewm-other-app"}
    assert _select(scope, area="sd") == set()  # an area without a group holds no tickets
    assert _select(scope, area=UNASSIGNED) == {"desk-category", "desk-field", "no-group-category"}
    parts = [_select(scope, area=code) for code in [*(a["code"] for a in CONFIG["areas"]), UNASSIGNED]]
    assert set().union(*parts) == SAP and sum(len(p) for p in parts) == len(SAP)


def test_landscapes_partition_the_scope():
    scope = _scope()
    assert _select(scope, landscape="ecc") == {"fico-ecc", "no-group-category"}
    assert _select(scope, landscape="s4") == {"ewm-s4", "desk-category"}
    assert _select(scope, landscape=UNKNOWN) == {"ewm-other-app", "desk-field"}
    assert _select(scope, area="ewm", landscape=UNKNOWN) == {"ewm-other-app"}
    parts = [_select(scope, landscape=code) for code in ("ecc", "s4", UNKNOWN)]
    assert set().union(*parts) == SAP and sum(len(p) for p in parts) == len(SAP)


def test_without_groups_every_scope_ticket_is_unassigned():
    scope = _scope(groups=[])
    assert _select(scope, area=UNASSIGNED) == _select(scope) == {"desk-category", "desk-field", "no-group-category"}
    assert _select(scope, area="ewm") == set()


def test_without_landscapes_every_ticket_is_unknown():
    scope = _scope(landscapes=[])
    assert _select(scope, landscape=UNKNOWN) == SAP


def test_unconfigured_scope_selects_nothing():
    scope = Scope(ScopeConfig())
    assert not scope.configured
    assert _select(scope) == set()
    assert scope.area_labels == {UNASSIGNED: "Unassigned"}


def test_unknown_area_or_landscape_is_a_validation_error():
    scope = _scope()
    with pytest.raises(ValidationFailed, match="Unknown SAP area 'mm'"):
        scope.ticket_sql(area="mm")
    with pytest.raises(ValidationFailed, match="Unknown SAP landscape 'bw'"):
        scope.ticket_sql(landscape="bw")


def test_area_of_uses_exact_group_names():
    scope = _scope()
    assert scope.area_of("G-EWM") == "ewm"
    assert scope.area_of("g-ewm") == UNASSIGNED
    assert scope.area_of(None) == UNASSIGNED


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"areas": [*CONFIG["areas"], {"code": "ewm", "label": "EWM again"}]}, "duplicate area 'ewm'"),
        ({"areas": [*CONFIG["areas"], {"code": "unassigned", "label": "x"}]}, "area code 'unassigned' is reserved"),
        ({"landscapes": [{"code": "unknown", "label": "x"}]}, "landscape code 'unknown' is reserved"),
        ({"groups": [{"name": "G-MM", "area": "mm"}]}, "group 'G-MM' has unknown area 'mm'"),
        ({"groups": [{"name": "G", "area": "sd"}, {"name": "G", "area": "ewm"}]}, "group 'G' is listed twice"),
        (
            {"landscapes": [{"code": "ecc", "label": "E", "apps": ["A"]}, {"code": "s4", "label": "S", "apps": ["A"]}]},
            "app 'A' is in two landscapes",
        ),
        ({"areas": [{"code": "FI CO", "label": "x"}]}, "String should match pattern"),
        ({"custom_fields": [{"field": 'u_x"]'}]}, "String should match pattern"),
        ({"unexpected": 1}, "Extra inputs are not permitted"),
    ],
)
def test_invalid_scope_config_is_rejected(changes: dict[str, Any], message: str):
    with pytest.raises(ValidationError, match=message.replace("(", r"\(").replace("[", r"\[")):
        ScopeConfig.model_validate({**CONFIG, **changes})


def test_repo_default_scope_is_synthetic_and_configured():
    scope = load_scope(None)
    assert scope.configured
    assert [a.code for a in scope.config.areas] == [
        "fi_co", "sd", "mm", "pp_qm", "ewm", "basis", "security", "integration", "abap",
    ]  # fmt: skip
    assert sorted(set(scope.group_area.values())) == sorted(a.code for a in scope.config.areas)
    assert all(g.name.startswith("SAP-") and g.name.endswith("-L3") for g in scope.config.groups)
    assert [x.code for x in scope.config.landscapes] == ["ecc", "s4"]


def test_data_dir_override_replaces_lists(data_root):
    from sed.paths import get_paths

    paths = get_paths("synthetic")
    paths.ensure()
    write_sap_config(paths, "scope.yaml", {"groups": [{"name": "LOCAL-EWM", "area": "ewm"}], "categories": []})
    scope = load_scope(paths)
    assert scope.group_area == {"LOCAL-EWM": "ewm"}
    assert scope.config.categories == []
    assert scope.config.areas[0].code == "fi_co"  # untouched keys keep the repo defaults


def test_invalid_override_reports_errors(data_root):
    from sed.paths import get_paths

    paths = get_paths("synthetic")
    paths.ensure()
    write_sap_config(paths, "scope.yaml", {"groups": [{"name": "LOCAL", "area": "nope"}]})
    with pytest.raises(ValidationFailed) as info:
        load_scope(paths)
    assert info.value.message == "Invalid sap/scope.yaml"
    assert "unknown area 'nope'" in json.dumps(info.value.details)
