"""Ingest hooks: the module-specific steps around the generic import engine.

A module declares `ingest_hooks = "package.module:HOOKS"`, an object implementing `IngestHooks`:

* `session_start(session)` runs once per `sed import`, after the Resolver is built and before any file is mapped
  (seed aliases, load directories into `session.state`).
* `before_target(session, target, spec)` runs before each file is mapped (apply overrides a target needs).
* `relink(session)` computes `RelinkUpdate`s for `sed import reresolve`: rows re-linked from stored raw values after
  aliases changed. The loader applies every module's updates in one write transaction.

Two optional attributes tune alias bookkeeping for the module's alias kinds:

* `suggestion_kinds`: alias kinds whose unmapped values get fuzzy suggestions.
* `resolved_by`: unmapped kind -> alias kinds whose aliases also resolve it (for resolution chains that fall back
  from one kind to another).

`BaseIngestHooks` provides no-op defaults. `ImportSession.state` is shared with `Ctx.state`, so targets can read what
hooks put there.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Protocol, runtime_checkable

if TYPE_CHECKING:
    from sed.ingest.mapping import MappingSpec
    from sed.ingest.resolve import Resolver
    from sed.ingest.target import Target
    from sed.paths import Paths


@dataclass
class ImportSession:
    """One import run (or one reresolve): shared connection, resolver, options and module state."""

    conn: sqlite3.Connection
    paths: Paths
    resolver: Resolver
    opts: Any = None  # sed.ingest.loader.ImportOptions for `sed import`; None for reresolve
    state: dict[str, Any] = field(default_factory=dict)

    @property
    def dry_run(self) -> bool:
        return bool(getattr(self.opts, "dry_run", False))


@dataclass(frozen=True)
class RelinkUpdate:
    """`conn.executemany(sql, rows)` for one table; `len(rows)` is reported under `table` in the reresolve result."""

    table: str
    sql: str
    rows: list[tuple[Any, ...]]


@runtime_checkable
class IngestHooks(Protocol):
    def session_start(self, session: ImportSession) -> None: ...

    def before_target(self, session: ImportSession, target: Target, spec: MappingSpec) -> None: ...

    def relink(self, session: ImportSession) -> list[RelinkUpdate]: ...


class BaseIngestHooks:
    """No-op hooks; subclass and override what the module needs."""

    suggestion_kinds: ClassVar[tuple[str, ...]] = ()
    resolved_by: ClassVar[Mapping[str, tuple[str, ...]]] = {}

    def session_start(self, session: ImportSession) -> None:
        return None

    def before_target(self, session: ImportSession, target: Target, spec: MappingSpec) -> None:
        return None

    def relink(self, session: ImportSession) -> list[RelinkUpdate]:
        return []


def suggestion_kinds(hooks: Iterable[Any]) -> tuple[str, ...]:
    """Alias kinds that get fuzzy suggestions, in declaration order across modules."""
    out: list[str] = []
    for h in hooks:
        out += [k for k in getattr(h, "suggestion_kinds", ()) or () if k not in out]
    return tuple(out)


def resolved_by(hooks: Iterable[Any]) -> dict[str, tuple[str, ...]]:
    """Unmapped kind -> alias kinds that resolve it (the first module declaring a kind wins)."""
    out: dict[str, tuple[str, ...]] = {}
    for h in hooks:
        for kind, kinds in dict(getattr(h, "resolved_by", {}) or {}).items():
            out.setdefault(kind, tuple(kinds))
    return out


def seed_aliases(resolver: Resolver, mapping: Mapping[str, Any] | None, kind: str | None = None) -> int:
    """Add `seed` aliases from a YAML mapping: `{kind: {raw: target}}`, or `{raw: target}` when `kind` is given."""
    seeded = 0
    items = {kind: mapping} if kind is not None else dict(mapping or {})
    for alias_kind, values in items.items():
        for raw, target_id in (values or {}).items():
            resolver.add_alias(alias_kind, raw, str(target_id), "seed")
            seeded += 1
    return seeded


def read_optional_yaml(path: Path) -> dict[str, Any]:
    """A DATA_DIR-only YAML file, or {} when it does not exist."""
    from sed.settings import read_yaml

    return (read_yaml(path) or {}) if path.is_file() else {}


__all__ = [
    "BaseIngestHooks",
    "ImportSession",
    "IngestHooks",
    "RelinkUpdate",
    "read_optional_yaml",
    "resolved_by",
    "seed_aliases",
    "suggestion_kinds",
]
