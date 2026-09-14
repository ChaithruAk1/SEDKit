"""Reference resolution: raw names in exports -> canonical ids via the alias table.

Alias kinds: app, vendor, group, ci, jira_project, jira_component, confluence_space.
Origins: auto_exact (seeded from master data names/ids), cmdb_rel (CI -> business app from cmdb_rel_ci),
seed (DATA_DIR\\config\\aliases.yaml / DATA_DIR\\config\\ops\\ci_to_app.yaml),
manual (`sed alias assign`; never overwritten).
Unresolved values are counted in unmapped_value with a rapidfuzz suggestion.
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from collections import Counter, defaultdict
from typing import Any

from rapidfuzz import fuzz, process

from sed import db

ALIAS_KINDS = ("app", "vendor", "group", "ci", "jira_project", "jira_component", "confluence_space")
_LEGAL_SUFFIXES = re.compile(
    r"\b(gmbh|ag|sa|sas|sarl|s a|ltd|limited|inc|llc|plc|bv|nv|spa|srl|oy|ab|as|co|corp|corporation|company)\b"
)


def normalize_alias(value: Any, kind: str = "") -> str:
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    text = re.sub(r"[^0-9a-z]+", " ", text)
    if kind == "vendor":
        text = _LEGAL_SUFFIXES.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


class Resolver:
    """In-memory alias cache for one import run; flushes unmapped counts and new aliases to the DB."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.aliases: dict[tuple[str, str], str] = {}
        self.origins: dict[tuple[str, str], str] = {}
        for row in conn.execute("SELECT kind, alias_norm, target_id, origin FROM alias"):
            self.aliases[(row["kind"], row["alias_norm"])] = row["target_id"]
            self.origins[(row["kind"], row["alias_norm"])] = row["origin"]
        self.group_vendor: dict[str, str | None] = {}
        self.group_app: dict[str, str | None] = {}
        for row in conn.execute("SELECT name, vendor_id, app_id FROM assignment_group WHERE is_deleted = 0"):
            key = normalize_alias(row["name"])
            self.group_vendor[key] = row["vendor_id"]
            self.group_app[key] = row["app_id"]
        self.unmapped: dict[str, Counter[str]] = defaultdict(Counter)
        self._pending_aliases: dict[tuple[str, str], tuple[str, str]] = {}
        # Only ids that exist may be returned (FK safety): a seed/manual alias to an unknown id counts as unmapped.
        self.valid: dict[str, set[str]] = {
            "app": {r[0] for r in conn.execute("SELECT app_id FROM application")},
            "vendor": {r[0] for r in conn.execute("SELECT vendor_id FROM vendor")},
        }

    def _target_kind(self, kind: str) -> str | None:
        if kind in {"app", "ci", "jira_project", "jira_component", "confluence_space"}:
            return "app"
        if kind == "vendor":
            return "vendor"
        return None

    def _valid_target(self, kind: str, target: str | None) -> str | None:
        if target is None:
            return None
        tk = self._target_kind(kind)
        if tk is None or target in self.valid[tk]:
            return target
        return None

    def register_ids(self, kind: str, ids: list[str]) -> None:
        self.valid[kind].update(ids)

    # -- lookups ---------------------------------------------------------------------------------------------

    def resolve(self, kind: str, raw: Any, *, record_unmapped: bool = True) -> str | None:
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            return None
        key = normalize_alias(raw, kind)
        if not key:
            return None
        target = self._valid_target(kind, self.aliases.get((kind, key)))
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

    def resolve_app(self, business_service: Any, ci: Any) -> str | None:
        """Business service name first, then the CI via cmdb_rel / ci_to_app aliases."""
        for kind, raw in (("app", business_service), ("ci", ci), ("app", ci)):
            if raw not in (None, ""):
                hit = self.resolve(kind, raw, record_unmapped=False)
                if hit:
                    return hit
        if business_service not in (None, ""):
            self.unmapped["app"][str(business_service).strip()] += 1
        elif ci not in (None, ""):
            self.unmapped["ci"][str(ci).strip()] += 1
        return None

    def vendor_for_group(self, group: Any) -> str | None:
        if group in (None, ""):
            return None
        return self._valid_target("vendor", self.group_vendor.get(normalize_alias(group)))

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

    def set_group(self, name: str, vendor_id: str | None, app_id: str | None) -> None:
        key = normalize_alias(name)
        self.group_vendor[key] = vendor_id
        self.group_app[key] = app_id

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
    """Display name -> target id for fuzzy suggestions."""
    if kind in {"app", "ci"}:
        rows = conn.execute("SELECT app_id, name FROM application WHERE is_deleted = 0").fetchall()
        return {r["name"]: r["app_id"] for r in rows}
    if kind == "vendor":
        rows = conn.execute("SELECT vendor_id, name FROM vendor WHERE is_deleted = 0").fetchall()
        return {r["name"]: r["vendor_id"] for r in rows}
    if kind == "group":
        rows = conn.execute("SELECT name FROM assignment_group WHERE is_deleted = 0").fetchall()
        return {r["name"]: r["name"] for r in rows}
    return {}


def refresh_suggestions(conn: sqlite3.Connection) -> int:
    """Compute rapidfuzz suggestions for unresolved unmapped values (call inside write_tx)."""
    updated = 0
    for kind in ("app", "vendor", "group", "ci"):
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


def mark_resolved(conn: sqlite3.Connection) -> int:
    """Flag unmapped values that now resolve through the alias table (call inside write_tx)."""
    rows = conn.execute("SELECT kind, raw_value FROM unmapped_value WHERE resolved = 0").fetchall()
    known = {(r["kind"], r["alias_norm"]) for r in conn.execute("SELECT kind, alias_norm FROM alias")}
    count = 0
    for row in rows:
        kinds = ("app", "ci") if row["kind"] in {"app", "ci"} else (row["kind"],)
        if any((k, normalize_alias(row["raw_value"], k)) in known for k in kinds):
            conn.execute(
                "UPDATE unmapped_value SET resolved = 1 WHERE kind = ? AND raw_value = ?",
                (row["kind"], row["raw_value"]),
            )
            count += 1
    return count
