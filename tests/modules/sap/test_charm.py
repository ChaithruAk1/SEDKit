"""ChaRM configuration (config/sap/charm.yaml): validation, derivations of type, stage, area, landscape and system, and
layered overrides that replace or extend the default lists."""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from sed.errors import ValidationFailed
from sed.modules.sap.charm import OTHER_TYPE, UNKNOWN_STAGE, CharmConfig, load_charm
from sed.modules.sap.scope import UNASSIGNED, UNKNOWN, load_scope
from tests.modules.sap.conftest import write_sap_config


@pytest.fixture
def charm() -> Any:
    return load_charm(None, load_scope(None))


def test_repo_defaults_load(charm):
    assert charm.change_type("SMHF") == "urgent" and charm.change_type("smmj") == "normal"
    assert charm.change_type("ZMHF") == OTHER_TYPE and charm.change_type(None) == OTHER_TYPE
    assert charm.stage("  to be   TESTED ") == "in_test"
    assert charm.stage("Imported into Production") == "in_production"
    assert charm.stage("Something else") == UNKNOWN_STAGE
    assert charm.config.jira.projects == ["SAPS4"]


@pytest.mark.parametrize(
    ("component", "area"),
    [
        ("FI-GL", "fi_co"),
        ("SCM-EWM-WOP", "ewm"),
        ("BC-SEC-USR", "security"),
        ("BC-CCM-BTC", "basis"),
        ("BC-DWB-CEX", "abap"),
        ("BC", "basis"),
        ("BCX-FOO", UNASSIGNED),  # prefixes match at a "-" boundary only
        ("", UNASSIGNED),
        (None, UNASSIGNED),
    ],
)
def test_area_uses_the_longest_prefix(charm, component, area):
    assert charm.area(component) == area


def test_landscape_and_systems(charm):
    assert charm.cycle_landscape("s4  release") == "s4"
    assert charm.cycle_landscape("Unknown cycle") is None
    assert charm.landscape_of_system("ep1") == "ecc" and charm.role_of_system("EP1") == "prod"
    assert charm.landscape_of_system("XX1") == UNKNOWN and charm.role_of_system("XX1") is None
    assert charm.stuck_after("in_test") == 30 and charm.stuck_after("confirmed") is None


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"change_types": [{"type": "SMHF", "change_type": "urgent"}, {"type": "smhf", "change_type": "normal"}]},
         "transaction type 'smhf' repeats 'SMHF'"),
        ({"stages": [{"status": "Open", "stage": "requested"}, {"status": " open", "stage": "approved"}]},
         "status ' open' repeats 'Open'"),
        ({"stages": [{"status": "Open", "stage": "started"}]}, "Input should be"),
        ({"change_types": [{"type": "X", "change_type": "emergency"}]}, "Input should be"),
        ({"jira": {"change_id_pattern": "(8)(\\d+)"}}, "at most one capturing group"),
        ({"jira": {"key_pattern": "(["}}, "invalid regular expression"),
        ({"thresholds": {"stuck_days": {"confirmed": 5}}}, "open stages only"),
        ({"thresholds": {"stuck_days": {"in_test": 0}}}, "open stages only"),
        ({"thresholds": {"failed_return_code": 0}}, "greater than or equal to 1"),
        ({"unexpected": True}, "Extra inputs are not permitted"),
    ],
)  # fmt: skip
def test_invalid_config_is_rejected(data, message):
    with pytest.raises(ValidationError) as info:
        CharmConfig.model_validate(data)
    assert message in str(info.value)


def test_components_and_cycles_must_name_scope_codes(data_root):
    from sed.paths import get_paths

    paths = get_paths("synthetic")
    paths.ensure()
    write_sap_config(
        paths,
        "charm.yaml",
        {
            "components": [{"prefix": "HR", "area": "hcm"}],
            "cycles": [{"cycle": "BW", "landscape": "bw"}],
        },
    )
    with pytest.raises(ValidationFailed) as info:
        load_charm(paths, load_scope(paths))
    details = json.dumps(info.value.details)
    assert "unknown SAP area 'hcm' for prefix 'HR'" in details and "unknown landscape 'bw' for cycle 'BW'" in details


def test_local_lists_replace_or_extend_the_defaults(data_root):
    from sed.paths import get_paths

    paths = get_paths("synthetic")
    paths.ensure()
    write_sap_config(
        paths,
        "charm.yaml",
        {
            "change_types+": [{"type": "ZMHF", "change_type": "urgent"}],
            "components": [{"prefix": "Z-FIN", "area": "fi_co"}],
            "jira": {"projects": ["ERPCHG"]},
        },
    )
    charm = load_charm(paths, load_scope(paths))
    assert charm.change_type("ZMHF") == "urgent" and charm.change_type("SMHF") == "urgent"  # appended
    assert charm.area("Z-FIN-AP") == "fi_co" and charm.area("FI-GL") == UNASSIGNED  # replaced
    assert charm.stage("To Be Tested") == "in_test"  # untouched keys keep the defaults
    assert charm.config.jira.projects == ["ERPCHG"] and charm.config.jira.change_id_pattern == r"\b(8\d{9})\b"
