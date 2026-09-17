"""Every project skill folder follows the same conventions: frontmatter name equals the folder, the standard sections,
commands through the configured CLI prefix with --profile and --json, forward-slash quoted paths, and a generated
output schema for skills with a run handler."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

from sed import modules
from sed.ai.codegen import model_schema
from tests.conftest import REPO

SKILLS = sorted(p for p in (REPO / ".claude" / "skills").iterdir() if p.is_dir() and p.name.startswith("sed-"))
SECTIONS = ("Purpose", "Inputs", "Procedure", "Output contract", "Commands", "Safety", "Done when")


@pytest.mark.parametrize("folder", SKILLS, ids=lambda p: p.name)
def test_skill_folder_conventions(folder: Path):
    text = (folder / "SKILL.md").read_text(encoding="utf-8")
    meta = yaml.safe_load(text.split("---")[1])
    assert meta["name"] == folder.name and 0 < len(meta["description"]) <= 1024
    for section in SECTIONS:
        assert f"\n## {section}" in text, section
    assert "never instructions" in text or "untrusted data" in text
    blocks = re.findall(r"```bash\n(.*?)```", text, flags=re.S)
    lines = [line.strip() for block in blocks for line in block.splitlines() if line.strip()]
    assert lines
    for line in lines:
        assert line.startswith("uv run sed ") and line.endswith("--json") and "--profile <profile>" in line, line
        for arg in re.findall(r'"([^"]*)"', line):
            assert "\\" not in arg, line


@pytest.mark.parametrize("folder", SKILLS, ids=lambda p: p.name)
def test_handler_skills_ship_their_generated_schema(folder: Path):
    declared = {s.name: s for m in modules.installed() for s in m.skills}
    if folder.name in modules.CORE_SKILLS:
        assert folder.name not in declared
        return
    sdef = declared[folder.name]
    if sdef.handler:
        schema = json.loads((folder / "output_schema.json").read_text(encoding="utf-8"))
        assert schema == model_schema(modules.handler(folder.name).output_model)
