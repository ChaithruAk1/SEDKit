"""IDoc configuration (config/sap/idoc.yaml): status groups and the SAP area of each message type.

The database keeps the exported status codes; the group (ok, in_process, error, closed) and the area are derived on
read, so a configuration change applies to all history at once.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cached_property
from typing import Literal

from pydantic import Field, ValidationError, model_validator

from sed.errors import ValidationFailed
from sed.modules.sap.scope import UNASSIGNED, Scope
from sed.paths import Paths
from sed.settings import StrictModel, load_layered

IDOC_FILE = "sap/idoc.yaml"
StatusGroup = Literal["ok", "in_process", "error", "closed"]
UNKNOWN_GROUP = "unknown"
_NUMBERS = re.compile(r"\b\d{4,}\b")
_SPACES = re.compile(r"\s+")


def normalise_code(code: str | int | None) -> str:
    """Status codes as two digits ("51", "03"); exports sometimes drop the leading zero."""
    text = str(code or "").strip()
    return text.zfill(2) if text.isdigit() else text


def normalise_text(text: str | None) -> str:
    """An error text with document numbers (4 or more digits) stripped, so the same error on different documents groups
    together."""
    return _SPACES.sub(" ", _NUMBERS.sub("#", text or "")).strip()


class StatusDef(StrictModel):
    code: str = Field(pattern=r"^\d{1,2}$")
    group: StatusGroup


class MessageTypeDef(StrictModel):
    type: str = Field(pattern=r"^[A-Z0-9_/]{1,30}$")
    area: str


class IdocThresholds(StrictModel):
    reprocess_grace_hours: int = Field(24, ge=1, le=720)
    aged_error_hours: int = Field(48, ge=1, le=8760)
    spike_window_hours: int = Field(48, ge=1, le=720)
    spike_min_lift: int = Field(10, ge=1)
    history_days: int = Field(120, ge=7, le=730)


class IdocConfig(StrictModel):
    version: Literal[1] = 1
    statuses: list[StatusDef] = Field(default_factory=list)
    message_types: list[MessageTypeDef] = Field(default_factory=list)
    thresholds: IdocThresholds = Field(default_factory=IdocThresholds)

    @model_validator(mode="after")
    def _unique(self) -> IdocConfig:
        codes = [normalise_code(s.code) for s in self.statuses]
        types = [m.type for m in self.message_types]
        problems = [f"status code '{c}' is listed twice" for c in sorted({c for c in codes if codes.count(c) > 1})]
        problems += [f"message type '{t}' is listed twice" for t in sorted({t for t in types if types.count(t) > 1})]
        if problems:
            raise ValueError("; ".join(problems))
        return self


@dataclass(frozen=True)
class Idoc:
    config: IdocConfig
    scope: Scope

    @cached_property
    def _groups(self) -> dict[str, str]:
        return {normalise_code(s.code): s.group for s in self.config.statuses}

    @cached_property
    def _areas(self) -> dict[str, str]:
        return {m.type: m.area for m in self.config.message_types}

    def group(self, code: str | None) -> str:
        return self._groups.get(normalise_code(code), UNKNOWN_GROUP)

    def codes(self, group: str) -> list[str]:
        return sorted(c for c, g in self._groups.items() if g == group)

    def area(self, message_type: str | None) -> str:
        return self._areas.get(str(message_type or "").strip().upper(), UNASSIGNED)


def load_idoc(paths: Paths | None, scope: Scope) -> Idoc:
    try:
        config = IdocConfig.model_validate(load_layered(IDOC_FILE, paths))
    except ValidationError as exc:
        errors = [{"loc": ".".join(str(p) for p in e["loc"]), "msg": e["msg"]} for e in exc.errors()]
        raise ValidationFailed(f"Invalid {IDOC_FILE}", errors) from exc
    areas = {a.code for a in scope.config.areas}
    errors = [
        {"loc": f"message_types.{i}", "msg": f"unknown SAP area '{m.area}' for '{m.type}' (config/sap/scope.yaml)"}
        for i, m in enumerate(config.message_types)
        if m.area not in areas
    ]
    if errors:
        raise ValidationFailed(f"Invalid {IDOC_FILE}", errors)
    return Idoc(config, scope)
