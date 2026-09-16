"""The sed-triage-batch skill folder: frontmatter, CLI prefix, quoted ingest path, examples and doctor claims."""

from __future__ import annotations

import json
import re

import pytest
import yaml

from sed.ai.codegen import model_schema
from sed.ai.contract import SkillHandler
from sed.doctor import run_checks
from sed.modules import handler as load_handler
from sed.paths import get_paths
from sed.settings import load_agent_config
from tests.conftest import REPO

SKILL_DIR = REPO / ".claude" / "skills" / "sed-triage-batch"
SKILL_MD = SKILL_DIR / "SKILL.md"


def _frontmatter() -> dict:
    parts = SKILL_MD.read_text(encoding="utf-8").split("---")
    return yaml.safe_load(parts[1])


def _code_lines() -> list[str]:
    text = SKILL_MD.read_text(encoding="utf-8")
    blocks = re.findall(r"```bash\n(.*?)```", text, flags=re.S)
    return [line.strip() for block in blocks for line in block.splitlines() if line.strip()]


def test_frontmatter_name_matches_folder():
    meta = _frontmatter()
    assert meta["name"] == SKILL_DIR.name == "sed-triage-batch"
    assert meta["description"] and len(meta["description"]) <= 1024


def test_skill_sections_present():
    text = SKILL_MD.read_text(encoding="utf-8")
    for section in ("Purpose", "Inputs", "Procedure", "Output contract", "Commands", "Safety", "Done when"):
        assert f"\n## {section}" in text, section
    assert "untrusted data" in text and "300" in text and "offset" in text


def test_commands_use_the_configured_prefix(monkeypatch, tmp_path):
    monkeypatch.setenv("SED_DATA_ROOT", str(tmp_path / "no-machine-override"))
    prefix = load_agent_config().command_prefix
    lines = _code_lines()
    assert lines and prefix == "uv run sed"
    for line in lines:
        assert line.startswith(f"{prefix} "), line
        assert line.endswith("--json") and "--profile <profile>" in line, line


def test_ingest_command_double_quotes_a_forward_slash_path():
    ingest = [line for line in _code_lines() if " ai ingest " in line]
    assert ingest == ['uv run sed ai ingest <run_id> "<out>" --profile <profile> --json']
    for line in _code_lines():
        for arg in re.findall(r'"([^"]*)"', line):
            assert "\\" not in arg
    assert re.search(r'ai ingest \S+ "[^"\\]+" --profile', ingest[0])


def test_output_schema_file_is_generated_from_the_model():
    handler = load_handler("sed-triage-batch")
    generated = json.loads((SKILL_DIR / "output_schema.json").read_text(encoding="utf-8"))
    assert generated == model_schema(handler.output_model)


def test_examples_validate_against_the_output_model_and_taxonomy(ops_profile):
    from sed.modules.ops.ai.extensions import load_extensions
    from sed.modules.ops.ai.taxonomy import load_taxonomy

    handler = load_handler("sed-triage-batch")
    taxonomy = load_taxonomy(ops_profile.paths)
    extensions = {ext.key: ext.by_category() for ext in load_extensions(ops_profile.paths, taxonomy)}
    lines = (SKILL_DIR / "examples.synthetic.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) >= 5
    labels, fields = [], {}
    for line in lines:
        example = json.loads(line)
        assert set(example) == {"input", "label"} and example["input"]["ref"] == example["label"]["ref"]
        assert not {"ticket_id", "number", "sys_id"} & set(example["input"])
        labels.append(example["label"])
        fields[example["input"]["ref"]] = set(example["input"]) & set(extensions)
    output = handler.output_model.model_validate({"meta": {"model": "example"}, "items": labels})
    assert any(fields.values()), "at least one example shows a module field"
    for item in output.items:
        category = taxonomy.categories[item.am_category]
        allowed = set(category.subcategories)
        allowed |= {c for key in fields[item.ref] for c in extensions[key].get(item.am_category, ())}
        assert item.am_subcategory is None or item.am_subcategory in allowed
        assert len(item.rationale.split()) <= 25
    assert len({i.ref for i in output.items}) == len(output.items)


def test_reference_guide_exists():
    guide = (SKILL_DIR / "reference" / "taxonomy_guide.md").read_text(encoding="utf-8")
    assert "misfiled_as" in guide and "symptom_key" in guide and "confidence" in guide


def test_handler_conforms_to_the_protocol():
    handler = load_handler("sed-triage-batch")
    assert isinstance(handler, SkillHandler)
    assert handler.skill == "sed-triage-batch" and handler.schema_version >= 1


@pytest.mark.parametrize("name", ["skills_claimed", "skill_names_valid"])
def test_doctor_skill_checks_ok(tmp_path, name):
    checks = {c.name: c for c in run_checks(get_paths("synthetic", tmp_path / "doctor"))}
    assert checks[name].status == "ok", checks[name].detail
