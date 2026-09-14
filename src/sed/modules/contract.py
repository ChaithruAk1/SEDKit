"""Module contract: what a SED module declares. Standard library only, so any code can import it cheaply.

A module is a package `sed.modules.<key>` (or, for tests, a package named in SED_EXTRA_MODULES) exposing
`MODULE: Module`. Everything is declared with lazy `ImportRef` strings ("package.module:attr"), so the core
never imports module code until a surface is actually used. See docs/modules.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Literal

ImportRef = str
MODULE_KEY_RE = re.compile(r"^[a-z][a-z0-9]{1,15}$")
IMPORT_REF_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*:[A-Za-z_][A-Za-z0-9_]*$")
PeriodKind = Literal["week", "month", "quarter"]
ReportFormat = Literal["xlsx", "md", "pptx"]


@dataclass(frozen=True)
class CliMount:
    """`sed <name> ...` backed by a Typer app."""

    name: str
    app: ImportRef


@dataclass(frozen=True)
class ApiMount:
    """FastAPI APIRouter mounted at /api/<module key> with tags=[key]."""

    router: ImportRef


@dataclass(frozen=True)
class NavItem:
    id: str  # "<module>.<page>"
    label: str
    path: str  # "/<module>" or "/<module>/..."
    order: int = 100
    icon: str | None = None


@dataclass(frozen=True)
class ReportDef:
    key: str
    title: str
    spec: str  # config-relative path, e.g. "ops/reports/weekly.yaml"
    builder: ImportRef  # (SnapshotRequest) -> SnapshotParts
    period_kinds: tuple[PeriodKind, ...]
    needs_vendor: bool = False
    formats: tuple[ReportFormat, ...] = ("xlsx", "pptx")
    markdown: ImportRef | None = None  # (Snapshot, ReportSpec, *, ai_mode) -> str; required iff "md" in formats


@dataclass(frozen=True)
class SkillDef:
    """A Claude Code skill folder `.claude/skills/<name>/` and its run handler (SkillHandler)."""

    name: str
    handler: ImportRef | None = None
    workflows: tuple[str, ...] = ()


@dataclass(frozen=True)
class SynthRequest:
    seed: int = 42
    as_of: date = date(2026, 9, 1)
    anchor: date | None = None
    months: int = 18
    scale: float = 1.0
    clean: bool = True


@dataclass(frozen=True)
class SynthDef:
    generate: ImportRef  # (Paths, SynthRequest) -> dict


@dataclass(frozen=True)
class EntityRef:
    """A canonical entity aliases can point at. `validate_ids=False`: ids are free text (not checked on resolve)."""

    key: str
    table: str
    id_col: str
    name_col: str
    validate_ids: bool = True


@dataclass(frozen=True)
class AliasKind:
    key: str
    entity: str  # EntityRef.key


@dataclass(frozen=True)
class Module:
    key: str
    title: str
    description: str = ""
    version: str = "1"
    depends_on: tuple[str, ...] = ()
    cli: tuple[CliMount, ...] = ()
    legacy_cli: tuple[str, ...] = ()  # top-level command names the module owns but the core CLI still defines
    api: ApiMount | None = None
    nav: tuple[NavItem, ...] = ()
    reports: tuple[ReportDef, ...] = ()
    skills: tuple[SkillDef, ...] = ()
    mappings_dir: str | None = None  # config-relative, e.g. "ops/mappings"
    ingest_targets: ImportRef | None = None  # dict[str, Target]
    ingest_hooks: ImportRef | None = None
    entities: tuple[EntityRef, ...] = ()
    alias_kinds: tuple[AliasKind, ...] = ()
    synth: SynthDef | None = None
    metric_definitions: ImportRef | None = None  # dict[str, tuple[unit, definition]]
    finding_kinds: tuple[str, ...] = ()
    doctor_checks: ImportRef | None = None  # (Paths) -> list[Check]
    config_files: tuple[str, ...] = ()
    data_subdirs: tuple[str, ...] = ()
    tables: tuple[str, ...] = ()
    owned_paths: tuple[str, ...] = ()
