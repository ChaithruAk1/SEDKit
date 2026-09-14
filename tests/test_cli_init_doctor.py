from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sed import claude_setup, db
from sed.cli import app
from sed.paths import get_paths
from sed.salt import fingerprint, read_salt

runner = CliRunner()

# Checks that depend on the machine/clone rather than on sed behaviour.
ENVIRONMENT_CHECKS = {"git_hooks_installed", "git_local_user_email", "guard_denylist_present", "data_dir_not_onedrive"}


def run(*args: str) -> tuple[int, dict]:
    result = runner.invoke(app, [*args, "--json"])
    lines = [ln for ln in result.stdout.strip().splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected exactly one JSON line, got: {result.stdout!r}"
    return result.exit_code, json.loads(lines[0])


def test_init_creates_profile(data_root: Path, tmp_path: Path):
    code, out = run("init", "--profile", "synthetic", "--new-salt")
    assert code == 0, out
    paths = get_paths("synthetic")
    assert paths.db.exists()
    for sub in ("inbox", "runs", "out", "backups", "config", "secret", "ground_truth"):
        assert (paths.data_dir / sub).is_dir()
    assert out["salt_created"] is True
    conn = db.connect(paths.db, readonly=True)
    meta = db.all_meta(conn)
    conn.close()
    assert meta["data_class"] == "synthetic"
    assert meta["pii_mode"] == "pseudonymize"
    assert meta["schema_version"] == str(db.latest_version())
    assert meta["salt_fingerprint"] == fingerprint(read_salt(paths.salt_file))

    local = json.loads((tmp_path / "settings.local.json").read_text(encoding="utf-8"))
    perms = local["permissions"]
    assert str(paths.runs) in perms["additionalDirectories"]
    runs_rule = claude_setup.posix_rule_path(paths.runs)
    assert f"Read({runs_rule}/**)" in perms["allow"]
    assert f"Edit({runs_rule}/**)" in perms["allow"]
    assert any("ground_truth" in d for d in perms["deny"])


def test_init_is_rerunnable_and_refuses_new_salt(data_root: Path):
    assert run("init", "--profile", "synthetic", "--new-salt")[0] == 0
    assert run("init", "--profile", "synthetic")[0] == 0
    code, out = run("init", "--profile", "synthetic", "--new-salt")
    assert code == 4
    assert out["ok"] is False and out["error"]["kind"] == "precondition"


@pytest.mark.parametrize("mode", ["pseudonymise", "Keep", "KEEP", ""])
def test_init_rejects_invalid_pii_mode_before_writing(data_root: Path, mode: str):
    code, out = run("init", "--profile", "real", "--new-salt", "--pii-mode", mode)
    assert code == 2, out
    assert not get_paths("real").db.exists()


def test_init_real_rejects_keep_pii_mode(data_root: Path):
    code, _ = run("init", "--profile", "real", "--new-salt", "--pii-mode", "keep")
    assert code == 4


def test_init_rejects_blank_ai_note(data_root: Path):
    code, _ = run("init", "--profile", "real", "--new-salt", "--ai-approval-note", "   ")
    assert code == 2


def test_init_refuses_data_class_mismatch(data_root: Path):
    assert run("init", "--profile", "synthetic", "--new-salt")[0] == 0
    paths = get_paths("synthetic")
    conn = db.connect(paths.db)
    with db.write_tx(conn):
        db.set_meta(conn, "data_class", "real")
    conn.close()
    code, out = run("init", "--profile", "synthetic")
    assert code == 4 and "data_class" in out["error"]["message"]


def test_salt_mismatch_detected(data_root: Path):
    assert run("init", "--profile", "synthetic", "--new-salt")[0] == 0
    paths = get_paths("synthetic")
    paths.salt_file.write_text("f" * 64, encoding="utf-8")
    code, out = run("init", "--profile", "synthetic")
    assert code == 4
    assert "fingerprint" in out["error"]["message"]


def test_salt_with_bom_is_accepted(data_root: Path):
    assert run("init", "--profile", "synthetic", "--new-salt")[0] == 0
    paths = get_paths("synthetic")
    value = paths.salt_file.read_text(encoding="utf-8").strip()
    paths.salt_file.write_bytes(b"\xef\xbb\xbf" + value.encode("ascii") + b"\r\n")
    assert run("init", "--profile", "synthetic")[0] == 0
    paths.salt_file.write_text(value, encoding="utf-16")
    assert run("init", "--profile", "synthetic")[0] == 0


def test_missing_salt_with_fingerprint_says_restore(data_root: Path):
    assert run("init", "--profile", "synthetic", "--new-salt")[0] == 0
    get_paths("synthetic").salt_file.unlink()
    code, out = run("init", "--profile", "synthetic")
    assert code == 0
    assert any("restore the original salt" in w for w in out["warnings"])
    assert not any("--new-salt" in w for w in out["warnings"])


def test_ai_approval_note_recorded(data_root: Path):
    code, out = run("init", "--profile", "real", "--new-salt", "--ai-approval-note", "approved by test")
    assert code == 0
    assert out["meta"]["ai_real_data_approved"] == "true"
    assert "ai_approval_note" not in out["meta"]


def test_settings_local_preserves_user_entries(data_root: Path, tmp_path: Path):
    target = tmp_path / "settings.local.json"
    user_rules = {
        "permissions": {
            "allow": ["Bash(git status)", "Read(//c/tools/sed/docs/**)"],
            "deny": ["Bash(uv run sed db restore:*)"],
        },
        "model": "x",
    }
    target.write_text(json.dumps(user_rules), encoding="utf-8")
    assert run("init", "--profile", "synthetic", "--new-salt")[0] == 0
    assert run("init", "--profile", "synthetic")[0] == 0
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["model"] == "x"
    assert data["permissions"]["allow"][:2] == user_rules["permissions"]["allow"]
    assert "Bash(uv run sed db restore:*)" in data["permissions"]["deny"]
    assert len([a for a in data["permissions"]["allow"] if "/runs/**" in a and a.startswith("Read(")]) == 1


def test_settings_local_malformed_json_is_not_overwritten(data_root: Path, tmp_path: Path):
    target = tmp_path / "settings.local.json"
    target.write_text('{"permissions": {"allow": ["Bash(git status)"],}}', encoding="utf-8")
    before = target.read_bytes()
    code, out = run("init", "--profile", "synthetic", "--new-salt")
    assert code == 4 and "settings.local.json" in out["error"]["message"]
    assert target.read_bytes() == before


def test_custom_data_dir_is_wired(data_root: Path, tmp_path: Path):
    custom = tmp_path / "elsewhere" / "synthetic"
    assert run("init", "--profile", "synthetic", "--data-dir", str(custom), "--new-salt")[0] == 0
    data = json.loads((tmp_path / "settings.local.json").read_text(encoding="utf-8"))
    assert str(custom / "runs") in data["permissions"]["additionalDirectories"]
    # Initialising another profile must keep the custom dir wired.
    assert run("init", "--profile", "eval-7", "--new-salt")[0] == 0
    data = json.loads((tmp_path / "settings.local.json").read_text(encoding="utf-8"))
    assert str(custom / "runs") in data["permissions"]["additionalDirectories"]


def test_machine_prefix_override_survives_other_profiles(data_root: Path, tmp_path: Path):
    data_root.mkdir(parents=True, exist_ok=True)
    (data_root / "agent.yaml").write_text("command_prefix: .venv/Scripts/python -m sed\n", encoding="utf-8")
    assert run("init", "--profile", "real", "--new-salt")[0] == 0
    assert run("init", "--profile", "eval-7", "--new-salt")[0] == 0
    data = json.loads((tmp_path / "settings.local.json").read_text(encoding="utf-8"))
    assert "Bash(.venv/Scripts/python -m sed:*)" in data["permissions"]["allow"]
    assert "PowerShell(.venv/Scripts/python -m sed:*)" in data["permissions"]["allow"]
    assert "`.venv/Scripts/python -m sed`" in Path(os.environ["SED_CLAUDE_MD"]).read_text(encoding="utf-8")


def test_doctor_passes_on_fresh_profile(data_root: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("UV_NO_SYNC", "1")
    monkeypatch.setenv("PYTHONUTF8", "1")
    assert run("init", "--profile", "synthetic", "--new-salt")[0] == 0
    code, out = run("doctor", "--profile", "synthetic")
    problems = [c for c in out["checks"] if c["status"] != "ok" and c["name"] not in ENVIRONMENT_CHECKS]
    assert problems == [], problems
    names = {c["name"] for c in out["checks"]}
    for required in ("data_dir_not_in_app_storage", "db_exists", "schema_current", "wal_mode", "fts5_available",
                     "data_class_matches_profile", "pii_mode_valid", "settings_valid", "tzdata", "salt_present",
                     "salt_fingerprint_matches", "claude_settings_local", "prefix_allow_rule",
                     "skill_names_valid"):  # fmt: skip
        assert required in names
    env_fails = {c["name"] for c in out["checks"] if c["status"] == "fail"}
    assert (code == 0) == (not env_fails)
    assert out["ok"] == (code == 0)


def test_doctor_fails_without_db(data_root: Path):
    code, out = run("doctor", "--profile", "synthetic")
    assert code == 4
    assert out["ok"] is False
    assert {c["name"]: c["status"] for c in out["checks"]}["db_exists"] == "fail"


def test_doctor_reports_bad_settings_and_corrupt_db_as_checks(data_root: Path):
    assert run("init", "--profile", "synthetic", "--new-salt")[0] == 0
    paths = get_paths("synthetic")
    (paths.config / "settings.yaml").write_text("fiscal_year_star: 4\n", encoding="utf-8")
    code, out = run("doctor", "--profile", "synthetic")
    by_name = {c["name"]: c["status"] for c in out["checks"]}
    assert code == 4 and by_name["settings_valid"] == "fail"
    (paths.config / "settings.yaml").unlink()
    for suffix in ("", "-wal", "-shm"):
        Path(str(paths.db) + suffix).unlink(missing_ok=True)
    paths.db.write_bytes(b"garbage" * 100)
    code, out = run("doctor", "--profile", "synthetic")
    assert code == 4
    assert {c["name"]: c["status"] for c in out["checks"]}["db_readable"] == "fail"


def test_db_info_backup_migrate_restore_cli(data_root: Path):
    assert run("init", "--profile", "synthetic", "--new-salt")[0] == 0
    code, info = run("db", "info", "--profile", "synthetic")
    assert code == 0
    assert info["user_version"] == db.latest_version()
    assert info["meta"]["data_class"] == "synthetic"
    code, out = run("db", "migrate", "--profile", "synthetic")
    assert code == 0 and out["applied"] == []
    code, out = run("db", "backup", "--profile", "synthetic")
    assert code == 0 and Path(out["backup"]).exists()
    code, restored = run("db", "restore", out["backup"], "--profile", "synthetic")
    assert code == 0 and restored["data_class"] == "synthetic"


def test_db_restore_cli_refuses_cross_profile(data_root: Path):
    assert run("init", "--profile", "real", "--new-salt")[0] == 0
    _, real_backup = run("db", "backup", "--profile", "real")
    assert run("init", "--profile", "synthetic", "--new-salt")[0] == 0
    code, out = run("db", "restore", real_backup["backup"], "--profile", "synthetic")
    assert code == 4 and "data_class" in out["error"]["message"]


def test_db_backup_keep_zero_is_rejected(data_root: Path):
    assert run("init", "--profile", "synthetic", "--new-salt")[0] == 0
    result = runner.invoke(app, ["db", "backup", "--profile", "synthetic", "--keep", "0", "--json"])
    assert result.exit_code == 2


def test_unexpected_errors_keep_json_contract(data_root: Path, monkeypatch: pytest.MonkeyPatch):
    import sed.bootstrap as bootstrap

    def boom(*_a, **_k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(bootstrap, "init_profile", boom)
    code, out = run("init", "--profile", "synthetic")
    assert code == 1
    assert out["ok"] is False and out["error"]["kind"] == "internal"


def test_posix_rule_path():
    assert claude_setup.posix_rule_path(Path(r"C:\Users\me\AppData\Local\sed\real\runs")) == (
        "//c/Users/me/AppData/Local/sed/real/runs"
    )
