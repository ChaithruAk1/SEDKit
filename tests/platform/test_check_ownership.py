"""Self-test of scripts/check_ownership.py in a throwaway git repository."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tests.conftest import load_script

check = load_script("check_ownership")

CONFIG = """
base: main
integration_branch: main
frozen: [src/core.py, docs/m2/ownership.yaml]
generated: [contracts/**]
workstreams:
  ws-a:
    owns: [src/a/**, tests/a/test_*.py]
    may_regenerate: []
"""


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    write(root, "docs/m2/ownership.yaml", CONFIG)
    write(root, "src/core.py", "x = 1\n")
    write(root, "src/a/impl.py", 'from errors import NotImplementedByWorkstream\nWS = "ws-a"\n')
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "base")
    git(root, "checkout", "-q", "-b", "m2/ws-a")
    return root


def violations(repo: Path, capsys, *args: str) -> list[dict]:
    check.main([*args, "--repo", str(repo)])
    return json.loads(capsys.readouterr().out)["violations"]


def test_owned_change_is_clean(repo, capsys):
    write(repo, "src/a/impl.py", "def f():\n    return 1\n")
    git(repo, "commit", "-qam", "implement")
    assert violations(repo, capsys, "ws-a") == []


def test_rebased_branch_ignores_integrator_commits(repo, capsys):
    write(repo, "src/a/impl.py", "def f():\n    return 1\n")
    git(repo, "commit", "-qam", "implement")
    git(repo, "checkout", "-q", "main")
    write(repo, "src/core.py", "x = 2\n")
    git(repo, "commit", "-qam", "contract: core change")
    git(repo, "checkout", "-q", "m2/ws-a")
    git(repo, "rebase", "-q", "main")
    assert violations(repo, capsys, "ws-a") == []


def test_frozen_edit_is_a_violation(repo, capsys):
    write(repo, "src/core.py", "x = 3\n")
    git(repo, "commit", "-qam", "touch core")
    found = violations(repo, capsys, "ws-a")
    assert [v["path"] for v in found] == ["src/core.py"] and "frozen" in found[0]["reason"]


def test_leftover_stub_is_a_violation(repo, capsys):
    write(repo, "src/a/impl.py", 'WS = "ws-a"\n\ndef f():\n    raise NotImplementedByWorkstream(WS)\n')
    git(repo, "commit", "-qam", "still a stub")
    found = violations(repo, capsys, "ws-a")
    assert any("stub" in v["reason"] for v in found)


def test_uncommitted_unowned_file_is_a_violation(repo, capsys):
    write(repo, "src/b/other.py", "y = 1\n")
    found = violations(repo, capsys, "ws-a")
    assert [v["path"] for v in found] == ["src/b/other.py"]


def test_generated_file_needs_may_regenerate(repo, capsys):
    write(repo, "contracts/openapi.json", "{}\n")
    found = violations(repo, capsys, "ws-a")
    assert found and "generated" in found[0]["reason"]


def test_glob_semantics():
    assert check.matches("src/a/deep/x.py", ["src/a/**"])
    assert not check.matches("src/ab/x.py", ["src/a/**"])
    assert check.matches("tests/a/test_x.py", ["tests/a/test_*.py"])
    assert not check.matches("tests/a/sub/test_x.py", ["tests/a/test_*.py"])
    assert check.matches("tests/x/y/__init__.py", ["tests/**/__init__.py"])
