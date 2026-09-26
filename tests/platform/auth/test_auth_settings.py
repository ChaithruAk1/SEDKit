"""auth.yaml: validation, the mode per profile, the allowlist decision, and `sed auth ...` writing the DATA_DIR file."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from sed.auth.identity import Identity, decide
from sed.auth.settings import AuthSettings, effective_data_class, load_auth_settings, local_file, resolve_mode
from sed.cli import app
from sed.errors import ValidationFailed
from sed.paths import Paths, get_paths
from tests.fixtures.auth import OTHER_TENANT, OWNER, TENANT, auth_settings

runner = CliRunner()


def run(*args: str) -> tuple[int, dict]:
    result = runner.invoke(app, [*args, "--json"])
    lines = [ln for ln in result.stdout.strip().splitlines() if ln.strip()]
    assert len(lines) == 1, result.stdout
    return result.exit_code, json.loads(lines[0])


def test_repo_defaults_switch_everything_off(tmp_path):
    settings = load_auth_settings(Paths("synthetic", tmp_path))
    assert settings.enabled() == [] and settings.allow.emails == [] and settings.mode == "auto"
    assert settings.problems() == ["no sign-in provider is switched on", "nobody is on the list of people allowed in"]


def test_mode_per_profile(tmp_path):
    auto = AuthSettings()
    assert resolve_mode(auto, Paths("synthetic", tmp_path)) == "developer"
    assert resolve_mode(auto, Paths("test-auth", tmp_path)) == "developer"
    assert resolve_mode(auto, Paths("real", tmp_path)) == "sign_in"
    assert resolve_mode(AuthSettings(mode="sign_in"), Paths("synthetic", tmp_path)) == "sign_in"
    assert resolve_mode(auto, Paths("real", tmp_path), developer_mode=True) == "developer"


def test_developer_mode_is_never_a_config_default_for_the_real_profile(tmp_path):
    with pytest.raises(ValidationFailed, match="--developer-mode"):
        resolve_mode(AuthSettings(mode="developer"), Paths("real", tmp_path))
    assert resolve_mode(AuthSettings(mode="developer"), Paths("synthetic", tmp_path)) == "developer"


@pytest.mark.parametrize(
    "data",
    [
        {"providers": {"google": {"enabled": True}}},  # no client id
        {"providers": {"github": {"enabled": True, "client_id": "has space"}}},
        {"providers": {"microsoft": {"enabled": True, "client_id": "abcd1234", "tenant_id": "common"}}},
        {"allow": {"emails": ["not-an-address"]}},
        {"allow": {"microsoft_tenants": ["contoso.example"]}},
        {"session_hours": 0},
        {"unknown_key": True},
    ],
)
def test_invalid_settings_are_refused(tmp_path, data):
    paths = Paths("synthetic", tmp_path)
    paths.config.mkdir(parents=True)
    local_file(paths).write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValidationFailed):
        load_auth_settings(paths)


def test_addresses_and_tenants_are_normalised():
    settings = AuthSettings.model_validate(
        {"allow": {"emails": [" Owner@Example.com ", "owner@example.com"], "microsoft_tenants": [TENANT.upper()]}}
    )
    assert settings.allow.emails == [OWNER] and settings.allow.microsoft_tenants == [TENANT]


def test_decide_by_address_and_by_organisation():
    settings = auth_settings(emails=[OWNER], microsoft_tenants=[])
    google = Identity("google", "s", emails=(OWNER,))
    assert decide(google, settings).allowed and decide(google, settings).identity.email == OWNER
    assert not decide(Identity("google", "s", emails=("x@example.org",)), settings).allowed
    assert not decide(Identity("github", "1", emails=(), login="octo"), settings).allowed

    colleague = Identity("microsoft", "o", emails=("colleague@example.com",), tenant=TENANT)
    assert not decide(colleague, settings).allowed
    assert decide(colleague, auth_settings(emails=[], microsoft_tenants=[TENANT])).allowed
    stranger = Identity("microsoft", "o", emails=(OWNER,), tenant=OTHER_TENANT)
    refused = decide(stranger, auth_settings(emails=[OWNER], microsoft_tenants=[OTHER_TENANT]))
    assert not refused.allowed and "different organisation" in refused.reason


def test_cli_switches_providers_and_edits_the_list(data_root):
    code, out = run("auth", "show", "--profile", "synthetic")
    assert code == 0 and out["mode"] == "developer" and out["allowed_emails"] == 0 and "allow" not in out

    code, out = run("auth", "provider", "google", "--client-id", "test-google-client.apps.example")
    assert code == 0 and [p["enabled"] for p in out["providers"]] == [False, True, False]
    code, out = run("auth", "allow", "Owner@Example.com")
    assert code == 0 and out["allowed_emails"] == 1
    code, out = run("auth", "allow", TENANT)
    assert code == 0 and out["allowed_organisations"] == 1
    code, out = run("auth", "show", "--list")
    assert out["allow"] == {"emails": [OWNER], "microsoft_tenants": [TENANT]}

    paths = get_paths("synthetic")
    before = local_file(paths).read_bytes()
    code, out = run("auth", "provider", "microsoft", "--client-id", "abcd1234")  # no tenant: invalid, file kept
    assert code == 2 and local_file(paths).read_bytes() == before
    code, _ = run("auth", "disallow", "nobody@example.com")
    assert code == 2
    code, out = run("auth", "disallow", OWNER)
    assert code == 0 and out["allowed_emails"] == 0
    code, out = run("auth", "provider", "google", "--off")
    assert code == 0 and not any(p["enabled"] for p in out["providers"])
    code, _ = run("auth", "provider", "google", "--tenant-id", TENANT)
    assert code == 2
    code, _ = run("auth", "allow", "neither")
    assert code == 2


def test_cli_mode(data_root):
    code, out = run("auth", "mode", "sign-in")
    assert code == 0 and out["mode"] == "sign_in"
    code, out = run("auth", "mode", "auto")
    assert code == 0 and out["mode"] == "developer"
    code, _ = run("auth", "mode", "bogus")
    assert code == 2
    code, out = run("auth", "mode", "developer", "--profile", "real")
    assert code == 2 and "--developer-mode" in out["error"]["message"]
    assert not local_file(get_paths("real")).exists()


def test_a_synthetic_profile_name_on_real_data_still_requires_sign_in(data_root):
    from sed import bootstrap

    real = get_paths("real")
    bootstrap.init_profile(real, write_claude_settings=False)
    disguised = Paths("synthetic", real.data_dir)  # `--profile synthetic --data-dir <real data folder>`
    assert effective_data_class(disguised) == "real"
    assert resolve_mode(AuthSettings(), disguised) == "sign_in"
    with pytest.raises(ValidationFailed):
        resolve_mode(AuthSettings(mode="developer"), disguised)

    synthetic = get_paths("synthetic")
    bootstrap.init_profile(synthetic, write_claude_settings=False)
    assert effective_data_class(synthetic) == "synthetic" and resolve_mode(AuthSettings(), synthetic) == "developer"


def test_unproven_people_are_recorded_under_a_windows_prefix(monkeypatch):
    from sed.auth.actor import command_line_actor, developer_actor
    from sed.bootstrap import reviewer_name

    monkeypatch.setenv("USERNAME", OWNER)  # anyone can name their Windows account like an address
    assert developer_actor().reviewer == f"windows:{OWNER}" and reviewer_name() == f"windows:{OWNER}"
    assert command_line_actor().verified is False
    signed_in = Identity("google", "s", emails=(OWNER,), email=OWNER).actor()
    assert signed_in.reviewer == OWNER and signed_in.verified


def test_the_public_api_prefixes_are_reserved_module_keys():
    from sed import modules
    from sed.auth.middleware import PUBLIC_API
    from sed.modules.contract import Module

    assert {p.strip("/").split("/")[1] for p in PUBLIC_API} == set(modules.CORE_API_KEYS)
    assert "auth" in modules.CORE_CLI_NAMES
    problems = "\n".join(modules.validate([Module(key=key, title=key) for key in sorted(modules.CORE_API_KEYS)]))
    for key in modules.CORE_API_KEYS:
        assert f"module key '{key}' is reserved by the core API /api/{key}" in problems


def test_cli_reports_why_nobody_can_sign_in_on_real(data_root):
    code, out = run("auth", "show", "--profile", "real")
    assert code == 0 and out["mode"] == "sign_in"
    assert out["problems"] == ["no sign-in provider is switched on", "nobody is on the list of people allowed in"]
