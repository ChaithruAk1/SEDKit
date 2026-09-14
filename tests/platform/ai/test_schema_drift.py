"""Generated AI schemas (skill output_schema.json and workflow schema blocks) match the Pydantic models."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys

import pytest

from sed.ai.codegen import END_MARKER, START_MARKER, export, model_schema, replace_block
from sed.ai.contract import RunPlan
from sed.errors import ValidationFailed
from tests.conftest import REPO


def test_schemas_export_check_exits_0():
    proc = subprocess.run(
        [sys.executable, "-m", "sed", "ai", "schemas", "export", "--check", "--json"],
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    lines = proc.stdout.strip().splitlines()
    assert len(lines) == 1 and json.loads(lines[0]) == {"ok": True, "check": True, "drifted": [], "written": []}


def test_inlined_schemas_have_no_refs():
    schema = model_schema(RunPlan)
    text = json.dumps(schema)
    assert "$ref" not in text and "$defs" not in text
    assert schema["properties"]["inputs"]["items"]["required"] == ["batch", "packet", "aux", "out", "items", "chars"]


def test_replace_block_needs_exactly_one_marker_pair():
    text = f"a\n{START_MARKER}\nold\n{END_MARKER}\nb\n"
    assert replace_block(text, "new\n", label="x") == f"a\n{START_MARKER}\nnew\n{END_MARKER}\nb\n"
    with pytest.raises(ValidationFailed):
        replace_block("no markers\n", "new\n", label="x")
    with pytest.raises(ValidationFailed):
        replace_block(text + text, "new\n", label="x")


def test_export_detects_and_repairs_drift(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(REPO / ".claude" / "skills", repo / ".claude" / "skills")
    shutil.copytree(REPO / ".claude" / "workflows", repo / ".claude" / "workflows")
    schema_file = repo / ".claude" / "skills" / "sed-triage-batch" / "output_schema.json"
    workflow = repo / ".claude" / "workflows" / "sed-analyze.js"
    assert export(repo, check=True) == ([], [])
    schema_file.write_bytes(b"{}\n")
    text = workflow.read_bytes().decode("utf-8")
    start = text.index(START_MARKER)
    workflow.write_bytes((text[: text.index("\n", start) + 1] + text[text.index(END_MARKER) :]).encode("utf-8"))
    drifted, written = export(repo, check=True)
    assert sorted(drifted) == [".claude/skills/sed-triage-batch/output_schema.json", ".claude/workflows/sed-analyze.js"]
    assert written == [] and schema_file.read_bytes() == b"{}\n"
    drifted, written = export(repo, check=False)
    assert drifted == [] and len(written) == 2
    assert export(repo, check=True) == ([], [])
    assert workflow.read_bytes() == (REPO / ".claude" / "workflows" / "sed-analyze.js").read_bytes()
