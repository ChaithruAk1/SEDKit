from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from amkit import paths as p
from amkit.errors import PreconditionFailed, ValidationFailed
from amkit.settings import deep_merge, load_agent_config, load_layered, load_settings, read_yaml


def test_profile_resolution_order(monkeypatch: pytest.MonkeyPatch):
    assert p.resolve_profile() == "synthetic"
    monkeypatch.setenv("AMKIT_PROFILE", "eval-1337")
    assert p.resolve_profile() == "eval-1337"
    assert p.resolve_profile("real") == "real"
    with pytest.raises(PreconditionFailed):
        p.resolve_profile("Prod DB")


def test_data_class():
    assert p.data_class_for("real") == "real"
    assert p.data_class_for("synthetic") == "synthetic"
    assert p.data_class_for("eval-42") == "synthetic"


def test_get_paths_uses_data_root(data_root: Path):
    paths = p.get_paths("synthetic")
    assert paths.data_dir == data_root / "synthetic"
    assert paths.db == data_root / "synthetic" / "amkit.db"


def test_location_checks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    assert p.is_unc(Path(r"\\server\share\amkit"))
    assert not p.is_unc(tmp_path)
    monkeypatch.setenv("OneDrive", str(tmp_path / "OneDrive - Contoso"))
    assert p.is_under_onedrive(tmp_path / "OneDrive - Contoso" / "amkit")
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    assert p.enclosing_git_tree(tmp_path / "repo" / "data") == (tmp_path / "repo").resolve()


def test_deep_merge_rules():
    base = {"a": {"x": 1, "y": [1, 2]}, "b": [1], "c": 3}
    over = {"a": {"x": 9, "y+": [2, 3]}, "b": [5], "c": "~delete"}
    assert deep_merge(base, over) == {"a": {"x": 9, "y": [1, 2, 3]}, "b": [5]}


def test_deep_merge_resolves_markers_in_new_subtrees():
    base = {"fields": {"number": {"from": ["number"]}}, "regex": {"employee_id": None}}
    over = {
        "fields": {"u_team": {"from+": ["u_team", "Team"], "legacy": "~delete"}},
        "regex": {"employee_id": {"pattern+": ["[A-Z]{3}\\d{6}"]}},
    }
    merged = deep_merge(base, over)
    assert merged["fields"]["u_team"] == {"from": ["u_team", "Team"]}
    assert merged["regex"]["employee_id"] == {"pattern": ["[A-Z]{3}\\d{6}"]}


def test_deep_merge_rejects_plain_and_append_together():
    with pytest.raises(ValidationFailed):
        deep_merge({"a": [1]}, {"a": [2], "a+": [3]})


def test_repo_defaults_load(data_root: Path):
    settings = load_settings(None)
    assert settings.base_currency == "EUR"
    assert settings.fiscal_year_start == 1
    agent = load_agent_config()
    assert agent.command_prefix == "uv run amkit"
    assert "\\" not in agent.fallback_prefix


def test_local_override_merges(data_root: Path):
    paths = p.get_paths("real")
    paths.ensure()
    (paths.config / "settings.yaml").write_text(
        "base_currency: USD\nas_of: 2026-09-01\nthresholds:\n  aged_ticket_days: 14\n", "utf-8"
    )
    s = load_settings(paths)
    assert s.base_currency == "USD"
    assert s.as_of == date(2026, 9, 1)
    assert s.thresholds.aged_ticket_days == 14
    assert s.thresholds.sla_warning_ratio == 0.8  # untouched default


def test_misspelled_override_key_is_an_error(data_root: Path):
    paths = p.get_paths("real")
    paths.ensure()
    (paths.config / "settings.yaml").write_text("base_curency: USD\n", "utf-8")
    with pytest.raises(ValidationFailed) as exc:
        load_settings(paths)
    assert any("base_curency" in e["loc"] for e in exc.value.details)


def test_read_yaml_encodings(tmp_path: Path):
    bom = tmp_path / "bom.yaml"
    bom.write_bytes(b"\xef\xbb\xbfkey: value\n")
    assert read_yaml(bom) == {"key": "value"}
    ansi = tmp_path / "ansi.yaml"
    ansi.write_bytes("note: Bien à vous\n".encode("cp1252"))
    with pytest.raises(ValidationFailed, match="UTF-8"):
        read_yaml(ansi)


def test_agent_config_is_machine_level_and_bash_safe(data_root: Path):
    data_root.mkdir(parents=True, exist_ok=True)
    (data_root / "agent.yaml").write_text("command_prefix: .venv/Scripts/python -m amkit\n", "utf-8")
    assert load_agent_config().command_prefix == ".venv/Scripts/python -m amkit"
    (data_root / "agent.yaml").write_text("command_prefix: .venv\\Scripts\\python -m amkit\n", "utf-8")
    with pytest.raises(ValidationFailed):
        load_agent_config()


def test_mapping_extends_chain(data_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = tmp_path / "fake_repo"
    (repo / "config" / "mappings").mkdir(parents=True)
    monkeypatch.setenv("AMKIT_REPO_ROOT", str(repo))
    (repo / "config" / "mappings" / "base.yaml").write_text(
        "name: base\nfields:\n  number: {from: [number, Number]}\n", "utf-8"
    )
    (repo / "config" / "mappings" / "child.yaml").write_text(
        "extends: base\nname: child\nfields:\n  state: {from: [state]}\n", "utf-8"
    )
    paths = p.get_paths("real")
    paths.ensure()
    (paths.config / "mappings").mkdir(parents=True, exist_ok=True)
    (paths.config / "mappings" / "child.yaml").write_text(
        "extends: child\nfields:\n  number:\n    from+: [Nummer]\n", "utf-8"
    )
    merged = load_layered("mappings/child.yaml", paths)
    assert merged["name"] == "child"
    assert merged["fields"]["number"]["from"] == ["number", "Number", "Nummer"]
    assert merged["fields"]["state"]["from"] == ["state"]
    assert "extends" not in merged


def test_circular_extends(data_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = tmp_path / "fake_repo"
    (repo / "config" / "mappings").mkdir(parents=True)
    monkeypatch.setenv("AMKIT_REPO_ROOT", str(repo))
    (repo / "config" / "mappings" / "a.yaml").write_text("extends: b\n", "utf-8")
    (repo / "config" / "mappings" / "b.yaml").write_text("extends: a\n", "utf-8")
    with pytest.raises(ValidationFailed):
        load_layered("mappings/a.yaml", None)
