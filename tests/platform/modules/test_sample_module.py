"""A second module plugs into the CLI and navigation through SED_EXTRA_MODULES, without core edits."""

from __future__ import annotations

import json
import os
import subprocess
import sys

from sed import modules
from tests.conftest import REPO

EXTRA = "tests.platform.modules.sample_module"


def _run(*args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, modules.EXTRA_ENV: EXTRA, "PYTHONPATH": os.pathsep.join([str(REPO / "src"), str(REPO)])}
    return subprocess.run(
        [sys.executable, "-m", "sed", *args], cwd=REPO, env=env, capture_output=True, text=True, encoding="utf-8"
    )


def test_cli_mount_via_extra_modules():
    proc = _run("hello", "ping", "--json")
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout.strip().splitlines()[-1]) == {"ok": True, "module": "hello", "reply": "pong"}
    listed = json.loads(_run("modules", "list", "--json").stdout.strip().splitlines()[-1])
    assert {m["key"] for m in listed["modules"]} == {"ops", "sap", "delivery", "hello"}


def test_nav_includes_extra_module(monkeypatch):
    monkeypatch.setenv(modules.EXTRA_ENV, EXTRA)
    assert modules.validate() == []
    ids = [item.id for _, item in modules.nav()]
    assert "hello.home" in ids and ids.index("hello.home") < ids.index("core.data")


def test_without_env_the_module_is_absent():
    proc = subprocess.run(
        [sys.executable, "-m", "sed", "hello", "ping"],
        cwd=REPO,
        env={k: v for k, v in os.environ.items() if k != modules.EXTRA_ENV},
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert proc.returncode != 0
