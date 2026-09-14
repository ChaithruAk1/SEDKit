"""Generic ingest target types shared by every module: Target (where rows go), Ctx (per-file build context), Reject.

A module declares `ingest_targets` as a dict name -> Target. The loader orders files by `Target.order` (then the
target name, active-snapshot files last, `Target.sort_key(spec)`, file name), writes `Target.columns` plus
`Target.batch_column`, and applies the load-mode post-steps described by the Target (soft delete, append-snapshot
scope, active-snapshot scope). Nothing here knows about any business domain.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sed.ingest.mapping import MappingSpec
    from sed.ingest.pii import PiiProcessor
    from sed.ingest.resolve import Resolver

DEFAULT_ORDER = 1000


class Reject(Exception):
    """Row cannot be loaded; reason is recorded in row_reject."""


@dataclass
class Ctx:
    """Context handed to `Target.build` and `Target.after_load` for one file.

    `state` is the import session state shared with the module's ingest hooks (for example a directory loaded at
    session start); `cache` holds per-file lookups built lazily by `cached`.
    """

    conn: sqlite3.Connection
    resolver: Resolver
    pii: PiiProcessor
    salt: bytes
    batch_id: int | None
    as_of: str | None
    base_currency: str
    fx_rates: dict[str, float]
    constants: dict[str, Any]
    warnings: dict[str, int] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)
    cache: dict[str, Any] = field(default_factory=dict)

    def warn(self, message: str) -> None:
        self.warnings[message] = self.warnings.get(message, 0) + 1

    def to_base(self, amount: float | None, currency: str | None) -> float | None:
        if amount is None:
            return None
        cur = (currency or self.base_currency).upper()
        rate = self.fx_rates.get(cur)
        if rate is None:
            self.warn(f"no FX rate for currency {cur}")
            return None
        return round(amount * rate, 2)

    def cached(self, key: str, load: Callable[[], Any]) -> Any:
        """Value for `key`, computed once per file by `load()`."""
        if key not in self.cache:
            self.cache[key] = load()
        return self.cache[key]


@dataclass
class Target:
    """One canonical destination table (or a registry-only target when `table` is empty)."""

    name: str
    table: str
    key: tuple[str, ...]
    columns: tuple[str, ...]
    build: Callable[[dict[str, Any], dict[str, Any], Ctx], dict[str, Any]]
    updated_field: str | None = None
    soft_delete: bool = False
    snapshot_field: str | None = None  # append_snapshot: rows with this value are replaced
    active_scope: tuple[str, str] | None = None  # active_snapshot: (column, value) scope, e.g. ("kind", "incident")
    after_load: Callable[[Ctx, list[dict[str, Any]]], None] | None = None
    order: int = DEFAULT_ORDER  # dependency rank for multi-file imports (lower first)
    sort_key: Callable[[MappingSpec], Any] | None = None  # tie-break between mappings of the same target
    batch_column: str = "last_batch_id"  # column that records the import batch that last wrote the row
    # append_snapshot: extra equality filters (column -> value) from the mapping constants, ANDed with snapshot_field
    snapshot_scope: Callable[[dict[str, Any]], dict[str, Any]] | None = None

    def snapshot_filters(self, as_of: str | None, constants: dict[str, Any]) -> dict[str, Any]:
        """Column -> value filters selecting the rows an append_snapshot file replaces."""
        filters: dict[str, Any] = {}
        if self.snapshot_field:
            filters[self.snapshot_field] = as_of
        if self.snapshot_scope:
            filters.update(self.snapshot_scope(dict(constants)))
        return filters


__all__ = ["DEFAULT_ORDER", "Ctx", "Reject", "Target"]
