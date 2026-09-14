"""`sed data move` and the app-storage check: data leaves a redirected folder intact, the copy is verified, and sed
refuses the old copy afterwards."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sed import claude_setup, db, relocate
from sed.cli import app
from sed.errors import PreconditionFailed
from sed.paths import MOVED_MARKER, app_package_location, get_paths
from sed.salt import fingerprint, read_salt

runner = CliRunner()
windows_only = pytest.mark.skipif(sys.platform != "win32", reason="app package storage and junctions are Windows-only")
BATCH_TEXT = '{"ref": "T001", "short": "Interface posting timeout"}\n'
WORKBOOK_BYTES = b"PK\x03\x04 synthetic workbook bytes"


def run(*args: str) -> tuple[int, dict]:
    result = runner.invoke(app, [*args, "--json"])
    lines = [ln for ln in result.stdout.strip().splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected exactly one JSON line, got: {result.stdout!r}"
    return result.exit_code, json.loads(lines[0])


def junction(link: Path, target: Path) -> None:
    """A directory junction: Windows resolves it the way it resolves a folder an app's storage redirects."""
    target.mkdir(parents=True, exist_ok=True)
    link.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)


def db_info(path: Path) -> dict:
    conn = db.connect(path, readonly=True)
    try:
        return db.info(conn, path)
    finally:
        conn.close()


@pytest.fixture
def two_profiles(data_root: Path) -> Path:
    """synthetic (with an import batch, run files, an output, an empty folder and a stale serve lock) and eval-7."""
    assert run("init", "--profile", "synthetic", "--new-salt")[0] == 0
    assert run("init", "--profile", "eval-7", "--new-salt")[0] == 0
    paths = get_paths("synthetic")
    conn = db.connect(paths.db)
    try:
        with db.write_tx(conn):
            conn.execute(
                "INSERT INTO import_batch (file_name, file_sha256, mapping_name, mapping_sha256, load_mode, status, "
                "rows_read, imported_at) VALUES ('incident_2026-08.csv', 'sha-a', 'servicenow_incident', 'm', "
                "'delta', 'completed', 3, '2026-09-01T00:00:00Z')"
            )
    finally:
        conn.close()
    batch = paths.runs / "run-1" / "in" / "batch_0001.jsonl"
    batch.parent.mkdir(parents=True)
    batch.write_text(BATCH_TEXT, encoding="utf-8")
    workbook = paths.out / "2026-W35" / "weekly_2026-W35_SYNTHETIC.xlsx"
    workbook.parent.mkdir(parents=True)
    workbook.write_bytes(WORKBOOK_BYTES)
    (paths.processed / "2026-09-14").mkdir(parents=True)
    paths.serve_lock.write_text(f"{os.getpid()} 8000 1\n", encoding="utf-8")  # left behind by an earlier process
    return data_root


def test_move_copies_every_profile_verifies_it_and_marks_the_old_root(
    two_profiles: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    old_root = two_profiles
    settings_file = tmp_path / "settings.local.json"
    local = json.loads(settings_file.read_text(encoding="utf-8"))
    local["permissions"]["allow"].insert(0, "Bash(git status)")
    settings_file.write_text(json.dumps(local), encoding="utf-8")
    custom = tmp_path / "elsewhere" / "eval-9"
    assert run("init", "--profile", "eval-9", "--data-dir", str(custom), "--new-salt")[0] == 0
    before = {p: db_info(old_root / p / "sed.db") for p in ("synthetic", "eval-7")}
    new_root = tmp_path / "SEDData"

    code, out = run("data", "move", "--to", str(new_root))

    assert code == 0, out
    assert out["profiles"] == ["eval-7", "synthetic"]
    assert [v["profile"] for v in out["verified"]] == ["eval-7", "synthetic"]
    assert out["warnings"] == []
    for profile, info in before.items():
        copied = db_info(new_root / profile / "sed.db")
        assert (copied["row_counts"], copied["meta"]) == (info["row_counts"], info["meta"])
        assert copied["journal_mode"] == "wal"
        salt = read_salt(new_root / profile / "secret" / "pii_salt.txt")
        assert fingerprint(salt) == copied["meta"]["salt_fingerprint"]
    moved = new_root / "synthetic"
    assert (moved / "runs" / "run-1" / "in" / "batch_0001.jsonl").read_text(encoding="utf-8") == BATCH_TEXT
    assert (moved / "out" / "2026-W35" / "weekly_2026-W35_SYNTHETIC.xlsx").read_bytes() == WORKBOOK_BYTES
    assert (moved / "inbox" / "processed" / "2026-09-14").is_dir()
    assert not (moved / "serve.lock").exists()
    assert json.loads((old_root / MOVED_MARKER).read_text(encoding="utf-8"))["moved_to"] == str(new_root)
    assert (old_root / "synthetic" / "sed.db").is_file(), "nothing is deleted"

    code, refused = run("db", "info", "--profile", "synthetic")
    assert code == 4
    assert str(new_root) in refused["error"]["message"] and "SED_DATA_ROOT" in refused["error"]["message"]
    monkeypatch.setenv("SED_DATA_ROOT", str(new_root))
    code, info = run("db", "info", "--profile", "synthetic")
    assert code == 0 and info["row_counts"]["import_batch"] == 1

    perms = json.loads(settings_file.read_text(encoding="utf-8"))["permissions"]
    assert perms["allow"][0] == "Bash(git status)"
    assert {str(new_root / "synthetic" / "runs"), str(custom / "runs")} <= set(perms["additionalDirectories"])
    assert not [d for d in perms["additionalDirectories"] if Path(d).is_relative_to(old_root)]
    old_rule_prefix = claude_setup.posix_rule_path(old_root) + "/"
    assert not [rule for rule in perms["allow"] + perms["deny"] if old_rule_prefix in rule]
    registry = json.loads((new_root / "profiles.json").read_text(encoding="utf-8"))["data_dirs"]
    assert set(registry) == {str(new_root / "synthetic"), str(new_root / "eval-7"), str(custom)}


def test_dry_run_counts_the_files_and_changes_nothing(two_profiles: Path, tmp_path: Path):
    new_root = tmp_path / "SEDData"
    code, out = run("data", "move", "--to", str(new_root), "--dry-run")
    assert code == 0, out
    assert out["dry_run"] is True and out["profiles"] == ["eval-7", "synthetic"] and out["verified"] == []
    assert out["files"] >= 6 and out["bytes"] > 0
    assert not new_root.exists() and not (two_profiles / MOVED_MARKER).exists()
    assert run("db", "info", "--profile", "synthetic")[0] == 0


@pytest.mark.parametrize(
    ("target", "code", "message"),
    [
        ("inside", 2, "outside the current data root"),
        ("same", 2, "outside the current data root"),
        ("not_empty", 4, "new or empty folder"),
        ("git_tree", 4, "git working tree"),
    ],
)
def test_move_refuses_unsafe_targets(two_profiles: Path, tmp_path: Path, target: str, code: int, message: str):
    folder = {
        "inside": two_profiles / "copy",
        "same": two_profiles,
        "not_empty": tmp_path / "busy",
        "git_tree": tmp_path / "project" / "data",
    }[target]
    (tmp_path / "busy").mkdir()
    (tmp_path / "busy" / "keep.txt").write_text("x", encoding="utf-8")
    (tmp_path / "project" / ".git").mkdir(parents=True)
    got, out = run("data", "move", "--to", str(folder))
    assert (got, out["ok"]) == (code, False)
    assert message in out["error"]["message"]
    assert not (two_profiles / MOVED_MARKER).exists()
    assert not (tmp_path / "project" / "data").exists() and not (two_profiles / "copy").exists()


def test_move_refuses_while_serve_runs_and_after_a_move(two_profiles: Path, tmp_path: Path):
    lock = two_profiles / "synthetic" / "serve.lock"
    lock.write_text(f"{os.getpid()} 8000 {db.process_start_token(os.getpid())}\n", encoding="utf-8")
    code, out = run("data", "move", "--to", str(tmp_path / "first"))
    assert code == 4 and "`sed serve` is running" in out["error"]["message"]
    assert not (tmp_path / "first").exists()
    lock.unlink()
    assert run("data", "move", "--to", str(tmp_path / "first"))[0] == 0
    code, out = run("data", "move", "--to", str(tmp_path / "second"))
    assert code == 4 and "already moved" in out["error"]["message"]
    assert not (tmp_path / "second").exists()


def test_a_failed_copy_is_removed_and_the_old_root_stays_usable(
    two_profiles: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    def mismatch(*_args, **_kwargs):
        raise PreconditionFailed("simulated mismatch")

    monkeypatch.setattr(relocate, "_copy_database", mismatch)
    code, out = run("data", "move", "--to", str(tmp_path / "SEDData"))
    assert code == 4 and "simulated mismatch" in out["error"]["message"]
    assert not (tmp_path / "SEDData").exists() and not (two_profiles / MOVED_MARKER).exists()
    conn = db.connect(get_paths("synthetic").db)
    try:
        with db.write_tx(conn):  # the move released its write lock
            db.set_meta(conn, "probe", "1")
    finally:
        conn.close()


@windows_only
def test_folders_redirected_into_app_storage_are_detected(
    data_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    package_local = tmp_path / "AppData" / "Local" / "Packages" / "Claude_test" / "LocalCache" / "Local"
    root_link = tmp_path / "home" / "AppData" / "Local" / "sed"
    junction(root_link, package_local / "sed")
    physical = (package_local / "sed").resolve()
    assert app_package_location(root_link) == physical
    assert (
        app_package_location(root_link / "synthetic" / "not-created-yet") == physical / "synthetic" / "not-created-yet"
    )
    assert app_package_location(tmp_path / "home") is None
    assert app_package_location(data_root) is None

    monkeypatch.setenv("SED_DATA_ROOT", str(root_link))
    for profile, severity in (("synthetic", "warn"), ("real", "fail")):
        assert run("init", "--profile", profile, "--new-salt")[0] == 0
        _, out = run("doctor", "--profile", profile)
        check = next(c for c in out["checks"] if c["name"] == "data_dir_not_in_app_storage")
        assert check["status"] == severity and "sed data move" in check["detail"], check

    redirected_target = tmp_path / "home" / "AppData" / "Local" / "sed-new"
    junction(redirected_target, package_local / "sed-new")
    code, out = run("data", "move", "--to", str(redirected_target))
    assert code == 4 and "private storage" in out["error"]["message"]

    code, out = run("data", "move", "--to", str(tmp_path / "home" / "SEDData"))
    assert code == 0, out
    assert out["physical_from"] == str(physical)
    monkeypatch.setenv("SED_DATA_ROOT", str(tmp_path / "home" / "SEDData"))
    _, out = run("doctor", "--profile", "synthetic")
    assert next(c for c in out["checks"] if c["name"] == "data_dir_not_in_app_storage")["status"] == "ok"
