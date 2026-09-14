"""Reference resolution: raw names in exports -> canonical ids via the alias table.

Alias kinds and the entities they point at come from the module registry (`AliasKind`, `EntityRef`); ops declares
app, vendor, group, ci, jira_project, jira_component and confluence_space.
Origins: auto_exact (seeded from master data names/ids), cmdb_rel (CI -> business app from cmdb_rel_ci),
seed (DATA_DIR config files read by module ingest hooks), manual (`sed alias assign`; never overwritten).
Unresolved values are counted in unmapped_value with a rapidfuzz suggestion.
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Sequence
from typing import Any, overload

from rapidfuzz import fuzz, process

from sed import db
from sed import modules as registry
from sed.ingest.hooks import resolved_by, suggestion_kinds

_LEGAL_SUFFIXES = re.compile(
    r"\b(gmbh|ag|sa|sas|sarl|s a|ltd|limited|inc|llc|plc|bv|nv|spa|srl|oy|ab|as|co|corp|corporation|company)\b"
)


class _AliasKinds(Sequence[str]):
    """Alias kinds declared by the installed modules, read from the registry on every access."""

    @staticmethod
    def _kinds() -> tuple[str, ...]:
        return registry.alias_kinds()

    @overload
    def __getitem__(self, index: int) -> str: ...

    @overload
    def __getitem__(self, index: slice) -> tuple[str, ...]: ...

    def __getitem__(self, index: int | slice) -> str | tuple[str, ...]:
        return self._kinds()[index]

    def __len__(self) -> int:
        return len(self._kinds())

    def __iter__(self) -> Iterator[str]:
        return iter(self._kinds())

    def __contains__(self, kind: object) -> bool:
        return kind in self._kinds()

    def __eq__(self, other: object) -> bool:
        return tuple(self) == (tuple(other) if isinstance(other, (tuple, list, _AliasKinds)) else other)

    def __hash__(self) -> int:
        return hash(tuple(self))

    def __repr__(self) -> str:
        return repr(self._kinds())


ALIAS_KINDS: Sequence[str] = _AliasKinds()


def normalize_alias(value: Any, kind: str = "") -> str:
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    text = re.sub(r"[^0-9a-z]+", " ", text)
    if kind == "vendor":
        text = _LEGAL_SUFFIXES.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def _alias_entities() -> dict[str, str]:
    """Alias kind -> entity key, from every installed module."""
    return {a.key: a.entity for m in registry.installed() for a in m.alias_kinds}


class Resolver:
    """In-memory alias cache for one import run; flushes unmapped counts and new aliases to the DB."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.aliases: dict[tuple[str, str], str] = {}
        self.origins: dict[tuple[str, str], str] = {}
        for row in conn.execute("SELECT kind, alias_norm, target_id, origin FROM alias"):
            self.aliases[(row["kind"], row["alias_norm"])] = row["target_id"]
            self.origins[(row["kind"], row["alias_norm"])] = row["origin"]
        self.unmapped: dict[str, Counter[str]] = defaultdict(Counter)
        self._pending_aliases: dict[tuple[str, str], tuple[str, str]] = {}
        self.entities = registry.entities()
        self.kind_entity = _alias_entities()
        # Only ids that exist may be returned (FK safety): a seed/manual alias to an unknown id counts as unmapped.
        # Entities declared with validate_ids=False (free-text ids such as group names) are not checked.
        self.valid: dict[str, set[str]] = {
            e.key: {r[0] for r in conn.execute(f"SELECT {e.id_col} FROM {e.table}")}
            for e in self.entities.values()
            if e.validate_ids
        }

    def _target_kind(self, kind: str) -> str | None:
        """Entity key whose ids an alias of `kind` must match, or None when ids are not validated."""
        entity = self.kind_entity.get(kind)
        return entity if entity in self.valid else None

    def valid_target(self, kind: str, target: str | None) -> str | None:
        """`target` when it is an existing id of the entity behind `kind` (or the entity is not validated)."""
        if target is None:
            return None
        tk = self._target_kind(kind)
        if tk is None or target in self.valid[tk]:
            return target
        return None

    def register_ids(self, kind: str, ids: Iterable[str]) -> None:
        """Mark ids as existing for an entity key (or the entity behind an alias kind) during an import."""
        entity = kind if kind in self.entities else self.kind_entity.get(kind, kind)
        if entity in self.valid:
            self.valid[entity].update(ids)

    @property
    def pending(self) -> int:
        """Number of aliases added in memory and not flushed yet."""
        return len(self._pending_aliases)

    # -- lookups ---------------------------------------------------------------------------------------------

    def resolve(self, kind: str, raw: Any, *, record_unmapped: bool = True) -> str | None:
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            return None
        key = normalize_alias(raw, kind)
        if not key:
            return None
        target = self.valid_target(kind, self.aliases.get((kind, key)))
        if target is None and record_unmapped:
            self.unmapped[kind][str(raw).strip()] += 1
        return target

    def resolve_first(self, kind: str, *raws: Any) -> str | None:
        candidates = [r for r in raws if r not in (None, "")]
        for raw in candidates:
            hit = self.resolve(kind, raw, record_unmapped=False)
            if hit:
                return hit
        if candidates:
            self.unmapped[kind][str(candidates[0]).strip()] += 1
        return None

    # -- writes ----------------------------------------------------------------------------------------------

    def add_alias(self, kind: str, raw: Any, target_id: str, origin: str) -> None:
        """Register an alias (in memory now, persisted by flush). Manual/seed aliases are never overwritten."""
        if raw in (None, "") or not target_id:
            return
        key = normalize_alias(raw, kind)
        if not key:
            return
        existing_origin = self.origins.get((kind, key))
        if existing_origin in {"manual", "seed"} and origin not in {"manual", "seed"}:
            return
        if existing_origin == "manual" and origin == "seed":
            return
        self.aliases[(kind, key)] = target_id
        self.origins[(kind, key)] = origin
        self._pending_aliases[(kind, key)] = (target_id, origin)

    def flush(self, batch_id: int | None) -> dict[str, Any]:
        """Persist pending aliases and unmapped counts (call inside write_tx)."""
        now = db.utc_now()
        for (kind, key), (target, origin) in self._pending_aliases.items():
            self.conn.execute(
                "INSERT INTO alias (kind, alias_norm, target_id, origin, created_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT (kind, alias_norm) DO UPDATE SET target_id = excluded.target_id, origin = excluded.origin "
                "WHERE alias.origin NOT IN ('manual') AND NOT (alias.origin = 'seed' AND excluded.origin <> 'seed')",
                (kind, key, target, origin, now),
            )
        self._pending_aliases.clear()
        summary: dict[str, Any] = {}
        for kind, counter in self.unmapped.items():
            summary[kind] = dict(counter.most_common(20))
            for raw, count in counter.items():
                self.conn.execute(
                    "INSERT INTO unmapped_value (kind, raw_value, occurrences, first_batch_id, last_batch_id, "
                    "resolved) "
                    "VALUES (?, ?, ?, ?, ?, 0) ON CONFLICT (kind, raw_value) DO UPDATE SET "
                    "occurrences = unmapped_value.occurrences + excluded.occurrences, "
                    "last_batch_id = excluded.last_batch_id, resolved = 0",
                    (kind, raw, count, batch_id, batch_id),
                )
        self.unmapped.clear()
        return summary


def candidate_names(conn: sqlite3.Connection, kind: str) -> dict[str, str]:
    """Display name -> target id for fuzzy suggestions (active rows of the entity behind the alias kind)."""
    entity_key = _alias_entities().get(kind)
    entity = registry.entities().get(entity_key or "")
    if entity is None:
        return {}
    rows = conn.execute(f"SELECT {entity.id_col}, {entity.name_col} FROM {entity.table} WHERE is_deleted = 0")
    return {r[1]: r[0] for r in rows.fetchall()}


def _hooks(hooks: Iterable[Any] | None) -> list[Any]:
    return list(registry.ingest_hooks() if hooks is None else hooks)


def refresh_suggestions(conn: sqlite3.Connection, hooks: Iterable[Any] | None = None) -> int:
    """Compute rapidfuzz suggestions for unresolved unmapped values (call inside write_tx).

    Only the alias kinds the modules' ingest hooks list in `suggestion_kinds` get suggestions. `hooks` defaults to the
    hooks of the enabled modules.
    """
    updated = 0
    for kind in suggestion_kinds(_hooks(hooks)):
        names = candidate_names(conn, kind)
        if not names:
            continue
        choices = {normalize_alias(n, kind): n for n in names}
        rows = conn.execute("SELECT raw_value FROM unmapped_value WHERE kind = ? AND resolved = 0", (kind,)).fetchall()
        compact = {c.replace(" ", ""): c for c in choices}
        for row in rows:
            raw_norm = normalize_alias(row["raw_value"], kind)
            best = process.extractOne(raw_norm, list(choices), scorer=fuzz.token_set_ratio)
            squeezed = process.extractOne(raw_norm.replace(" ", ""), list(compact), scorer=fuzz.ratio)
            if squeezed and (not best or squeezed[1] > best[1]):
                best = (compact[squeezed[0]], squeezed[1], None)
            if best:
                choice, score, _ = best
                conn.execute(
                    "UPDATE unmapped_value SET suggestion = ?, score = ? WHERE kind = ? AND raw_value = ?",
                    (choices[choice], float(score), kind, row["raw_value"]),
                )
                updated += 1
    return updated


def mark_resolved(conn: sqlite3.Connection, hooks: Iterable[Any] | None = None) -> int:
    """Flag unmapped values that now resolve through the alias table (call inside write_tx).

    An unmapped value of kind K counts as resolved when an alias exists for any kind the hooks list in
    `resolved_by[K]` (default: K itself). `hooks` defaults to the hooks of the enabled modules.
    """
    fallbacks = resolved_by(_hooks(hooks))
    rows = conn.execute("SELECT kind, raw_value FROM unmapped_value WHERE resolved = 0").fetchall()
    known = {(r["kind"], r["alias_norm"]) for r in conn.execute("SELECT kind, alias_norm FROM alias")}
    count = 0
    for row in rows:
        kinds = fallbacks.get(row["kind"], (row["kind"],))
        if any((k, normalize_alias(row["raw_value"], k)) in known for k in kinds):
            conn.execute(
                "UPDATE unmapped_value SET resolved = 1 WHERE kind = ? AND raw_value = ?",
                (row["kind"], row["raw_value"]),
            )
            count += 1
    return count
