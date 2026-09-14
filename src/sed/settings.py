"""Layered configuration.

Every file under config/ in the repo holds SYNTHETIC defaults. The same relative path under
DATA_DIR\\config overrides it (deep merge). Real values therefore never live in git.

Merge rules:
- dicts merge recursively;
- lists and scalars from the override replace the base;
- a key written as ``name+`` appends its list to the base list ``name`` (a mapping may not contain both);
- a value of ``"~delete"`` removes the key;
- markers are resolved everywhere, including inside subtrees the base does not have;
- ``extends: <name>`` loads the sibling config (fully layered) as the base.

The agent config (command prefix) is MACHINE-level, not per profile, because it drives repo-wide files
(CLAUDE.md and the shared settings.local.json): repo config/agent.yaml overlaid by <data_root>\\agent.yaml.
"""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from sed.errors import ValidationFailed
from sed.paths import Paths, data_root, repo_root

DELETE_MARKER = "~delete"
PII_MODES = ("pseudonymize", "drop", "keep")


def _resolve_markers(value: Any) -> Any:
    """Normalise a subtree that has no base counterpart (so `x+` becomes `x`, `~delete` entries vanish)."""
    if isinstance(value, dict):
        return deep_merge({}, value)
    return copy.deepcopy(value)


def deep_merge(base: Any, override: Any) -> Any:
    if not isinstance(base, dict) or not isinstance(override, dict):
        return _resolve_markers(override)
    for key in override:
        if isinstance(key, str) and key.endswith("+") and key[:-1] in override:
            raise ValidationFailed(f"Config mapping contains both '{key[:-1]}' and '{key}'; use only one.")
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(key, str) and key.endswith("+"):
            target = key[:-1]
            existing = out.get(target, [])
            if existing is None:
                existing = []
            if not isinstance(existing, list) or not isinstance(value, list):
                raise ValidationFailed(f"'{key}' can only append a list to a list")
            out[target] = existing + [copy.deepcopy(v) for v in value if v not in existing]
        elif value == DELETE_MARKER:
            out.pop(key, None)
        elif key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = _resolve_markers(value)
    return out


def read_yaml(path: Path) -> dict[str, Any]:
    try:
        text = path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValidationFailed(f"{path} is not UTF-8 encoded; re-save it as UTF-8.", {"error": str(exc)}) from exc
    except OSError as exc:
        raise ValidationFailed(f"Cannot read {path}: {exc}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValidationFailed(f"Invalid YAML in {path}", {"error": str(exc)}) from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValidationFailed(f"{path} must contain a mapping at the top level")
    return data


def repo_config_dir() -> Path:
    return repo_root() / "config"


def _sibling(rel: str, name: str) -> str:
    parent = Path(rel).parent.as_posix()
    return f"{name}.yaml" if parent in ("", ".") else f"{parent}/{name}.yaml"


def load_layered(rel_path: str, paths: Paths | None = None, *, _seen: tuple[str, ...] = ()) -> dict[str, Any]:
    """Load config/<rel_path> merged with DATA_DIR/config/<rel_path> (when a profile is given).

    A repo file may ``extends:`` a sibling; the sibling is loaded fully layered first.
    A local file with ``extends: <own name>`` (or no extends) overrides the repo layer; a local
    file extending a different sibling uses that sibling's layered result as its base instead.
    """
    rel = rel_path.replace("\\", "/")
    if rel in _seen:
        raise ValidationFailed(f"Circular extends: {' -> '.join((*_seen, rel))}")
    seen = (*_seen, rel)
    base_file = repo_config_dir() / rel
    local_file = paths.config / rel if paths else None
    has_local = bool(local_file and local_file.is_file())
    if not base_file.is_file() and not has_local:
        raise ValidationFailed(f"Config '{rel}' not found in repo or DATA_DIR")

    repo_layer: dict[str, Any] = {}
    if base_file.is_file():
        repo_layer = read_yaml(base_file)
        parent = repo_layer.pop("extends", None)
        if parent:
            repo_layer = deep_merge(load_layered(_sibling(rel, parent), paths, _seen=seen), repo_layer)
        else:
            repo_layer = deep_merge({}, repo_layer)

    if not has_local:
        return repo_layer
    local = read_yaml(local_file)  # type: ignore[arg-type]
    parent = local.pop("extends", None)
    if parent and _sibling(rel, parent) != rel:
        base_for_local = load_layered(_sibling(rel, parent), paths, _seen=seen)
    else:
        base_for_local = repo_layer
    return deep_merge(base_for_local, local)


def config_sha256(data: Any) -> str:
    blob = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _validate(model: type[BaseModel], data: dict[str, Any], label: str) -> Any:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        errors = [{"loc": ".".join(str(p) for p in e["loc"]), "msg": e["msg"]} for e in exc.errors()]
        raise ValidationFailed(f"Invalid {label}", errors) from exc


class StrictModel(BaseModel):
    """Unknown keys are errors: a misspelled override must never silently fall back to synthetic defaults."""

    model_config = ConfigDict(extra="forbid")


class Thresholds(StrictModel):
    """Attention-list thresholds. Risk-rule thresholds live in config/risk_rules.yaml."""

    aged_ticket_days: int = Field(30, ge=1)
    sla_warning_ratio: float = Field(0.8, gt=0, le=1)
    reassignment_pingpong: int = Field(3, ge=1)


class AiSettings(StrictModel):
    triage_batch_size: int = Field(100, ge=1, le=500)
    triage_packet_max_chars: int = Field(40_000, ge=1_000)
    max_items_per_run: int = Field(8_000, ge=1)
    backfill_days: int = Field(90, ge=1)
    claim_lease_hours: int = Field(6, ge=1)


class Settings(StrictModel):
    base_currency: str = Field("EUR", pattern=r"^[A-Z]{3}$")
    fiscal_year_start: int = Field(1, ge=1, le=12)
    reporting_tz: str = "Europe/Paris"
    display_names: bool = False
    pii_mode: str = "pseudonymize"
    as_of: date | None = None
    thresholds: Thresholds = Thresholds()
    ai: AiSettings = AiSettings()

    @field_validator("pii_mode")
    @classmethod
    def _pii_mode(cls, v: str) -> str:
        if v not in PII_MODES:
            raise ValueError(f"pii_mode must be one of {', '.join(PII_MODES)}")
        return v


def load_settings(paths: Paths | None = None) -> Settings:
    return _validate(Settings, load_layered("settings.yaml", paths), "settings.yaml")


class AgentConfig(StrictModel):
    command_prefix: str = "uv run sed"
    fallback_prefix: str = ".venv/Scripts/python -m sed"
    shell_tool: str = "Bash"

    @model_validator(mode="after")
    def _bash_safe(self) -> AgentConfig:
        if self.shell_tool not in {"Bash", "PowerShell"}:
            raise ValueError("shell_tool must be Bash or PowerShell")
        if self.shell_tool == "Bash":
            for name in ("command_prefix", "fallback_prefix"):
                if "\\" in getattr(self, name):
                    raise ValueError(f"{name} must use forward slashes when shell_tool is Bash (Git Bash eats '\\')")
        return self


def machine_agent_override() -> Path:
    return data_root() / "agent.yaml"


def load_agent_config() -> AgentConfig:
    data = read_yaml(repo_config_dir() / "agent.yaml")
    override = machine_agent_override()
    if override.is_file():
        data = deep_merge(data, read_yaml(override))
    return _validate(AgentConfig, data, "agent.yaml")
