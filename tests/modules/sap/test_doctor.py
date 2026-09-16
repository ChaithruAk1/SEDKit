"""SAP doctor checks (sap.*): all ok on the SAP profile, and a clear warning or failure for each misconfiguration."""

from __future__ import annotations

from typing import Any

from sed import modules
from sed.modules.sap.doctor import checks
from tests.modules.sap.conftest import write_sap_config

SCOPE = {
    "areas": [{"code": "ewm", "label": "EWM"}],
    "groups": [{"name": "SAP-EWM-L3", "area": "ewm"}],
    "categories": [],
    "custom_fields": [],
    "landscapes": [{"code": "s4", "label": "S/4", "apps": ["APM0990002"]}],
    "systems": [{"sid": "HP1", "landscape": "s4", "role": "prod"}],
}
IDOC = {"message_types": [{"type": "DESADV", "area": "ewm"}]}  # matches the reduced SCOPE
CHARM = {"components": [{"prefix": "SCM-EWM", "area": "ewm"}], "cycles": [{"cycle": "S4 Release", "landscape": "s4"}]}


def _by_name(paths: Any) -> dict[str, tuple[str, str]]:
    return {c.name: (c.status, c.detail) for c in checks(paths)}


def test_all_ok_on_the_sap_profile(sap_profile):
    result = _by_name(sap_profile.paths)
    assert list(result) == [
        "sap.config_valid",
        "sap.scope_groups_seen",
        "sap.landscape_apps_known",
        "sap.transport_systems_known",
        "sap.charm_values_mapped",
        "sap.idoc_values_known",
    ]
    assert {status for status, _ in result.values()} == {"ok"}
    assert result["sap.scope_groups_seen"][1] == "every configured SAP group appears on tickets"
    names = [c.name for c in modules.doctor_checks(sap_profile.paths)]
    assert "sap.config_valid" in names  # appended to `sed doctor` while the module is enabled


def test_unseen_group_and_unknown_landscape_app_warn(sap_profile_rw):
    paths = sap_profile_rw.paths
    write_sap_config(
        paths,
        "scope.yaml",
        {
            **SCOPE,
            "groups": [*SCOPE["groups"], {"name": "SAP-EWM-L3 ", "area": "ewm"}],
            "landscapes": [{"code": "s4", "label": "S/4", "apps": ["APM0990002", "APM0999999"]}],
        },
    )
    write_sap_config(paths, "charm.yaml", CHARM)
    write_sap_config(paths, "idoc.yaml", IDOC)
    result = _by_name(paths)
    assert result["sap.scope_groups_seen"] == (
        "warn",
        "SAP groups never seen on a ticket (check exact names): SAP-EWM-L3 ",
    )
    assert result["sap.landscape_apps_known"] == ("warn", "landscape applications not in the portfolio: APM0999999")


def test_invalid_config_fails(sap_profile_rw):
    paths = sap_profile_rw.paths
    write_sap_config(paths, "scope.yaml", {**SCOPE, "groups": [{"name": "SAP-X", "area": "nope"}]})
    result = _by_name(paths)
    assert list(result) == ["sap.config_valid"]
    status, detail = result["sap.config_valid"]
    assert status == "fail" and detail.startswith("Invalid sap/scope.yaml") and "unknown area 'nope'" in detail


def test_invalid_rules_fail(sap_profile_rw):
    paths = sap_profile_rw.paths
    write_sap_config(paths, "risk_rules.yaml", {"rules": {"area_aged_backlog": {"min_tickets": 0}}})
    status, detail = _by_name(paths)["sap.config_valid"]
    assert status == "fail" and detail.startswith("Invalid sap/risk_rules.yaml")


def test_unconfigured_scope_warns(sap_profile_rw):
    paths = sap_profile_rw.paths
    write_sap_config(paths, "scope.yaml", {"groups": [], "categories": [], "custom_fields": []})
    result = _by_name(paths)
    assert result["sap.scope_configured"][0] == "warn"
    assert "sap.scope_groups_seen" not in result


def test_empty_profile_reports_no_data_yet(data_root):
    from sed import bootstrap
    from sed.paths import get_paths

    paths = get_paths("synthetic")
    bootstrap.init_profile(paths, write_claude_settings=False, new_salt=True)
    result = _by_name(paths)
    assert result["sap.scope_groups_seen"] == ("ok", "no tickets imported yet")
    assert result["sap.landscape_apps_known"] == ("ok", "no applications imported yet")


def test_disabled_module_adds_no_checks(sap_profile_rw):
    config = sap_profile_rw.paths.config
    config.mkdir(parents=True, exist_ok=True)
    (config / "modules.yaml").write_text("enabled: [ops]\n", encoding="utf-8")
    assert not [c for c in modules.doctor_checks(sap_profile_rw.paths) if c.name.startswith("sap.")]


def test_charm_config_must_match_the_scope(sap_profile_rw):
    paths = sap_profile_rw.paths
    write_sap_config(paths, "scope.yaml", SCOPE)  # fewer areas and landscapes than the default charm.yaml uses
    status, detail = _by_name(paths)["sap.config_valid"]
    assert status == "fail" and detail.startswith("Invalid sap/charm.yaml")
    assert "unknown SAP area 'fi_co'" in detail and "unknown landscape 'ecc'" in detail


def test_unknown_transport_systems_and_unmapped_charm_values_warn(sap_profile_rw):
    paths = sap_profile_rw.paths
    write_sap_config(
        paths,
        "scope.yaml",
        {
            "systems": [
                {"sid": "HD1", "landscape": "s4", "role": "dev"},
                {"sid": "HP1", "landscape": "s4", "role": "prod"},
            ]
        },
    )
    write_sap_config(
        paths,
        "charm.yaml",
        {
            "change_types": [{"type": "SMMJ", "change_type": "normal"}],
            "components": [{"prefix": "FI", "area": "fi_co"}],
        },
    )
    result = _by_name(paths)
    status, detail = result["sap.transport_systems_known"]
    assert status == "warn" and "EP1" in detail and "EQ1" in detail and "HP1" not in detail
    status, detail = result["sap.charm_values_mapped"]
    assert status == "warn"
    assert "transaction types: " in detail and "SMHF" in detail and "SMMJ" not in detail
    assert "components: " in detail and "SD-" in detail and "FI-" not in detail


def test_unknown_idoc_values_warn(sap_profile_rw):
    paths = sap_profile_rw.paths
    write_sap_config(
        paths,
        "idoc.yaml",
        {
            "statuses": [{"code": "53", "group": "ok"}, {"code": "03", "group": "ok"}],
            "message_types": [{"type": "ORDERS", "area": "sd"}],
        },
    )
    write_sap_config(paths, "scope.yaml", {"systems": [{"sid": "HP1", "landscape": "s4", "role": "prod"}]})
    status, detail = _by_name(paths)["sap.idoc_values_known"]
    assert status == "warn"
    assert "status codes: " in detail and "51" in detail and "message types: " in detail and "INVOIC" in detail
    assert "systems: EP1" in detail
