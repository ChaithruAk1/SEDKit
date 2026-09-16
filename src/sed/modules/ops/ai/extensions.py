"""Triage extensions: other modules add subcategories and packet context for their own tickets to sed-triage-batch.

A module contributes to the `ops.triage` extension point (`Module.extensions`) with an import reference to a factory
`(Paths) -> TriageExtension`. For every enabled extension the triage handler:
* adds `payload[key]` (the extension's context object) to the packet lines of the tickets the extension claims;
* lists the extension's subcategories under their portfolio categories in in/context.md, with its field
  descriptions and guide, when the run holds at least one such ticket;
* accepts those subcategories only on lines that carry `payload[key]`;
* hashes `config` into the skill hash, so a change to the extension's configuration is visible on every run.
A subcategory whose category is not in the effective ops taxonomy (a DATA_DIR override removed it) is left out; the
contributing module's doctor checks report it (`unknown_categories`).

Ops never imports the contributing modules; it reaches them only through `sed.modules.extensions`.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

from sed.errors import ValidationFailed

POINT = "ops.triage"
KEY_RE = re.compile(r"^[a-z][a-z0-9]{1,15}$")
# Packet keys the ops handler writes itself; an extension key must not shadow them.
RESERVED_KEYS = frozenset(
    {"ref", "stage", "app", "kind", "prio", "sn_cat", "group", "short", "desc", "close_code", "close"}
)


@dataclass(frozen=True)
class Subcategory:
    code: str  # starts with "<extension key>_"
    category: str  # a category of the effective ops taxonomy
    description: str


@dataclass(frozen=True)
class TriageExtension:
    key: str  # packet field and subcategory code prefix, e.g. "sap"
    title: str  # human name, e.g. "SAP"
    subcategories: tuple[Subcategory, ...]
    fields: tuple[tuple[str, str], ...]  # (field, description) of the packet object
    guide: str  # markdown appended to the extension's section of in/context.md
    # (conn, ticket ids) -> {ticket id: context object} for the tickets the extension claims; values must be JSON-safe
    context: Callable[[sqlite3.Connection, list[str]], dict[str, dict[str, Any]]]
    config: dict[str, Any] = field(default_factory=dict)  # canonical configuration hashed into the skill hash

    def by_category(self) -> dict[str, tuple[str, ...]]:
        out: dict[str, list[str]] = {}
        for sub in self.subcategories:
            out.setdefault(sub.category, []).append(sub.code)
        return {k: tuple(v) for k, v in out.items()}

    def as_config(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "subcategories": [[s.code, s.category, s.description] for s in self.subcategories],
            "fields": [list(f) for f in self.fields],
            "guide": self.guide,
            "config": self.config,
        }


def unknown_categories(ext: TriageExtension, taxonomy: Any) -> list[Subcategory]:
    """The extension's subcategories whose category is not in the effective taxonomy."""
    return [s for s in ext.subcategories if s.category not in taxonomy.categories]


def load_extensions(paths: Any, taxonomy: Any) -> list[TriageExtension]:
    """The enabled modules' triage extensions checked against the effective taxonomy (exit 2 on a conflict), without
    subcategories of categories the taxonomy does not have."""
    from sed import modules

    out: list[TriageExtension] = []
    for module_key, factory in modules.extensions(POINT, paths):
        ext = factory(paths)
        if not isinstance(ext, TriageExtension):
            raise ValidationFailed(f"Module '{module_key}': the {POINT} extension did not return a TriageExtension")
        problems = _problems(ext, taxonomy, out)
        if problems:
            raise ValidationFailed(f"Module '{module_key}': invalid triage extension '{ext.key}'", problems)
        dropped = unknown_categories(ext, taxonomy)
        if dropped:
            ext = replace(ext, subcategories=tuple(s for s in ext.subcategories if s not in dropped))
        out.append(ext)
    return out


def _problems(ext: TriageExtension, taxonomy: Any, earlier: list[TriageExtension]) -> list[str]:
    problems: list[str] = []
    if not KEY_RE.match(ext.key):
        problems.append(f"key '{ext.key}' must match {KEY_RE.pattern}")
    if ext.key in RESERVED_KEYS:
        problems.append(f"key '{ext.key}' is a packet field of the ops handler")
    if any(e.key == ext.key for e in earlier):
        problems.append(f"key '{ext.key}' is used by another triage extension")
    codes = [s.code for s in ext.subcategories]
    problems += [f"subcategory '{c}' is listed twice" for c in sorted({c for c in codes if codes.count(c) > 1})]
    for sub in ext.subcategories:
        category = taxonomy.categories.get(sub.category)
        if not sub.code.startswith(f"{ext.key}_"):
            problems.append(f"subcategory '{sub.code}' must start with '{ext.key}_'")
        if category is not None and sub.code in category.subcategories:
            problems.append(f"subcategory '{sub.code}' is already a subcategory of '{sub.category}'")
    return problems
