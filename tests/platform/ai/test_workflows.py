"""Workflow scripts: `node --check` plus the offline harness scenarios (skipped when node is not installed)."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from tests.conftest import REPO

NODE = shutil.which("node")
WORKFLOWS = sorted((REPO / ".claude" / "workflows").glob("*.js"))
HARNESS = REPO / "tests" / "workflow_harness.mjs"

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _node(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([NODE, *args], cwd=REPO, capture_output=True, text=True, encoding="utf-8", check=False)


def test_sed_analyze_exists():
    assert (REPO / ".claude" / "workflows" / "sed-analyze.js") in WORKFLOWS


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: p.name)
def test_node_check(workflow):
    proc = _node("--check", str(workflow))
    assert proc.returncode == 0, proc.stderr


def test_harness_scenarios_pass():
    proc = _node(str(HARNESS), "--check", *map(str, WORKFLOWS))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    report = json.loads(proc.stdout)
    cases = [c for w in report["workflows"] for c in w["cases"]]
    assert report["ok"] and len(cases) >= 5 and all(c["ok"] for c in cases)


@pytest.mark.parametrize(
    ("find", "replace", "expected"),
    [
        ("phase('Finish run')", "phase('Finish run'); const stamp = Date.now()", "Date.now()"),
        ("const SKILL = 'sed-triage-batch'", "const SKILL: string = 'sed-triage-batch'", "does not compile"),
        ("  name: 'sed-analyze',", "  name: NAME,", "meta must not reference variables"),
        ("log(`Failed batches: ", "log(`Failures: ", "Failed batches: batch_0002"),
        ("  label: `batch ${input.batch}`,", "  label: 'batch',", "batch agents"),
    ],
)
def test_harness_catches_broken_workflows(tmp_path, find, replace, expected):
    source = (REPO / ".claude" / "workflows" / "sed-analyze.js").read_text(encoding="utf-8")
    assert find in source
    broken = tmp_path / "sed-analyze.js"
    broken.write_bytes(source.replace(find, replace, 1).encode("utf-8"))
    proc = _node(str(HARNESS), "--check", str(broken))
    assert proc.returncode == 1, proc.stdout
    assert expected in proc.stdout
