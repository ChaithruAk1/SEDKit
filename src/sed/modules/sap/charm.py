"""ChaRM configuration (config/sap/charm.yaml): how exported change documents and transports are read.

The database keeps the raw export values (transaction type, user status, component, change cycle). Change type, stage,
SAP area and landscape are derived on read from this file and from the systems and areas in config/sap/scope.yaml, so a
configuration change applies to all history at once.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from pydantic import Field, ValidationError, field_validator, model_validator

from sed.errors import ValidationFailed
from sed.modules.sap.scope import UNASSIGNED, UNKNOWN, Scope, SystemDef
from sed.paths import Paths
from sed.settings import StrictModel, load_layered

CHARM_FILE = "sap/charm.yaml"
ChangeType = Literal["request", "normal", "urgent", "standard", "defect_correction", "general"]
Stage = Literal[
    "requested",
    "approved",
    "in_development",
    "in_test",
    "ready_for_production",
    "in_production",
    "confirmed",
    "withdrawn",
]
CHANGE_TYPES: tuple[str, ...] = ChangeType.__args__  # type: ignore[attr-defined]
STAGES: tuple[str, ...] = Stage.__args__  # type: ignore[attr-defined]
CLOSED_STAGES = ("confirmed", "withdrawn")
OTHER_TYPE = "other"
UNKNOWN_STAGE = "unknown"
# Change types that are expected to trace back to a Jira story (requests and standard changes are not).
JIRA_EXPECTED_TYPES = ("normal", "urgent", "defect_correction")
TYPE_LABELS = {
    "request": "Request for change",
    "normal": "Normal",
    "urgent": "Urgent",
    "standard": "Standard",
    "defect_correction": "Defect correction",
    "general": "General",
    OTHER_TYPE: "Other",
}
STAGE_LABELS = {
    "requested": "Requested",
    "approved": "Approved",
    "in_development": "In development",
    "in_test": "In test",
    "ready_for_production": "Ready for production",
    "in_production": "In production",
    "confirmed": "Confirmed",
    "withdrawn": "Withdrawn",
    UNKNOWN_STAGE: "Unknown status",
}


def _norm(value: str | None) -> str:
    return " ".join(str(value or "").split()).casefold()


def _one_group(pattern: str) -> str:
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"invalid regular expression: {exc}") from exc
    if compiled.groups > 1:
        raise ValueError("use at most one capturing group")
    return pattern


class JiraLinks(StrictModel):
    projects: list[str] = Field(default_factory=list)
    key_pattern: str = r"\b([A-Z][A-Z0-9]{1,9}-\d+)\b"
    change_id_pattern: str = r"\b(8\d{9})\b"

    @field_validator("key_pattern", "change_id_pattern")
    @classmethod
    def _pattern(cls, value: str) -> str:
        return _one_group(value)


class CharmThresholds(StrictModel):
    stuck_days: dict[Stage, int] = Field(default_factory=dict)
    waiting_for_production_days: int = Field(14, ge=1)
    failed_return_code: int = Field(8, ge=1)
    incident_window_hours: int = Field(72, ge=1, le=720)

    @field_validator("stuck_days")
    @classmethod
    def _positive(cls, value: dict[str, int]) -> dict[str, int]:
        bad = sorted(stage for stage, days in value.items() if days < 1 or stage in CLOSED_STAGES)
        if bad:
            raise ValueError(f"stuck_days needs at least 1 day for open stages only: {', '.join(bad)}")
        return value


class ChangeTypeDef(StrictModel):
    type: str = Field(min_length=1)
    change_type: ChangeType


class StageDef(StrictModel):
    status: str = Field(min_length=1)
    stage: Stage


class ComponentDef(StrictModel):
    prefix: str = Field(min_length=1)
    area: str


class CycleDef(StrictModel):
    cycle: str = Field(min_length=1)
    landscape: str


class CharmConfig(StrictModel):
    version: Literal[1] = 1
    change_types: list[ChangeTypeDef] = Field(default_factory=list)
    stages: list[StageDef] = Field(default_factory=list)
    components: list[ComponentDef] = Field(default_factory=list)
    cycles: list[CycleDef] = Field(default_factory=list)
    jira: JiraLinks = Field(default_factory=JiraLinks)
    thresholds: CharmThresholds = Field(default_factory=CharmThresholds)

    @model_validator(mode="after")
    def _unique_after_normalising(self) -> CharmConfig:
        problems: list[str] = []
        lists = (
            ("transaction type", [t.type for t in self.change_types]),
            ("status", [s.status for s in self.stages]),
            ("component prefix", [c.prefix for c in self.components]),
            ("cycle", [c.cycle for c in self.cycles]),
        )
        for what, keys in lists:
            seen: dict[str, str] = {}
            for key in keys:
                if _norm(key) in seen:
                    problems.append(f"{what} '{key}' repeats '{seen[_norm(key)]}'")
                seen[_norm(key)] = key
        if problems:
            raise ValueError("; ".join(problems))
        return self


@dataclass(frozen=True)
class Charm:
    config: CharmConfig
    scope: Scope

    def change_type(self, transaction_type: str | None) -> str:
        types = {_norm(t.type): t.change_type for t in self.config.change_types}
        return types.get(_norm(transaction_type), OTHER_TYPE)

    def stage(self, status: str | None) -> str:
        stages = {_norm(s.status): s.stage for s in self.config.stages}
        return stages.get(_norm(status), UNKNOWN_STAGE)

    def area(self, component: str | None) -> str:
        """Area of the longest configured component prefix ("SCM-EWM" before "SCM"), matched at a "-" boundary."""
        value = str(component or "").strip().upper()
        best: ComponentDef | None = None
        for c in self.config.components:
            p = c.prefix.strip().upper()
            if (value == p or value.startswith(p + "-")) and (best is None or len(p) > len(best.prefix.strip())):
                best = c
        return best.area if best else UNASSIGNED

    def cycle_landscape(self, cycle: str | None) -> str | None:
        cycles = {_norm(c.cycle): c.landscape for c in self.config.cycles}
        return cycles.get(_norm(cycle))

    def system(self, system_id: str | None) -> SystemDef | None:
        sid = str(system_id or "").strip().upper()
        return next((s for s in self.scope.config.systems if s.sid == sid), None)

    def landscape_of_system(self, system_id: str | None) -> str:
        system = self.system(system_id)
        return system.landscape if system else UNKNOWN

    def role_of_system(self, system_id: str | None) -> str | None:
        system = self.system(system_id)
        return system.role if system else None

    def stuck_after(self, stage: str) -> int | None:
        return self.config.thresholds.stuck_days.get(stage)  # type: ignore[call-overload]


def load_charm(paths: Paths | None, scope: Scope) -> Charm:
    try:
        config = CharmConfig.model_validate(load_layered(CHARM_FILE, paths))
    except ValidationError as exc:
        errors = [{"loc": ".".join(str(p) for p in e["loc"]), "msg": e["msg"]} for e in exc.errors()]
        raise ValidationFailed(f"Invalid {CHARM_FILE}", errors) from exc
    areas = {a.code for a in scope.config.areas}
    landscapes = {x.code for x in scope.config.landscapes}
    errors = [
        {
            "loc": f"components.{i}",
            "msg": f"unknown SAP area '{c.area}' for prefix '{c.prefix}' (config/sap/scope.yaml)",
        }
        for i, c in enumerate(config.components)
        if c.area not in areas
    ] + [
        {
            "loc": f"cycles.{i}",
            "msg": f"unknown landscape '{c.landscape}' for cycle '{c.cycle}' (config/sap/scope.yaml)",
        }
        for i, c in enumerate(config.cycles)
        if c.landscape not in landscapes
    ]
    if errors:
        raise ValidationFailed(f"Invalid {CHARM_FILE}", errors)
    return Charm(config, scope)
