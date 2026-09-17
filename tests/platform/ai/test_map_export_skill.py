"""The sed-map-export skill folder: frontmatter, sections, command conventions, generated reference, core claim."""

from __future__ import annotations

import re

import yaml

from sed import modules
from sed.ingest.onboarding import canonical_fields_markdown
from tests.conftest import REPO

SKILL_DIR = REPO / ".claude" / "skills" / "sed-map-export"
SKILL_MD = SKILL_DIR / "SKILL.md"


def _code_lines() -> list[str]:
    blocks = re.findall(r"```bash\n(.*?)```", SKILL_MD.read_text(encoding="utf-8"), flags=re.S)
    return [line.strip() for block in blocks for line in block.splitlines() if line.strip()]


def test_frontmatter_and_sections():
    text = SKILL_MD.read_text(encoding="utf-8")
    meta = yaml.safe_load(text.split("---")[1])
    assert meta["name"] == SKILL_DIR.name and 0 < len(meta["description"]) <= 1024
    for section in ("Purpose", "Inputs", "Procedure", "Output contract", "Commands", "Safety", "Done when"):
        assert f"\n## {section}" in text, section
    assert "untrusted data" in text and "pii" in text and "--dry-run" in text


def test_commands_follow_the_cli_conventions():
    lines = _code_lines()
    assert {"check", "draft", "try", "save-override"} <= {line.split()[4] for line in lines if " mappings " in line}
    for line in lines:
        assert line.startswith("uv run sed ") and line.endswith("--json") and "--profile <profile>" in line, line
        for arg in re.findall(r'"([^"]*)"', line):
            assert "\\" not in arg


def test_reference_is_generated_and_the_skill_is_a_core_skill():
    reference = (SKILL_DIR / "reference" / "canonical_fields.md").read_text(encoding="utf-8")
    assert reference == canonical_fields_markdown()
    assert "## `ticket`" in reference and "servicenow_incident" in reference and "sap_idocs" in reference
    assert "sed-map-export" in modules.CORE_SKILLS
    assert all(s.name != "sed-map-export" for m in modules.installed() for s in m.skills)
