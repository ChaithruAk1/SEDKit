"""Request dependencies: per-request database connections and common dashboard filters."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date

from fastapi import Query, Request

from sed import db
from sed.auth.actor import Actor
from sed.errors import PreconditionFailed, ValidationFailed

# as_of values outside this range would overflow the date arithmetic of period and window bounds.
AS_OF_MIN, AS_OF_MAX = date(1900, 1, 1), date(9998, 12, 31)


def _paths(request: Request):
    return request.app.state.paths


def actor(request: Request) -> Actor:
    """Who is asking (set by the sign-in middleware for every request that got this far)."""
    found = getattr(request.state, "actor", None)
    if found is None:
        raise PreconditionFailed("Please sign in to SED.")
    return found


def reviewer(request: Request) -> str:
    """The name a decision made through the API records: the signed-in person's verified address, or in developer
    mode the Windows account name (as before sign-in existed)."""
    actor = getattr(request.state, "actor", None)
    if actor is not None:
        return actor.reviewer
    from sed.bootstrap import reviewer_name

    return reviewer_name()


def read_conn(request: Request) -> Iterator[sqlite3.Connection]:
    """Read-only connection for one request (query_only; never holds a long-lived reader)."""
    paths = _paths(request)
    if not paths.db.exists():
        raise PreconditionFailed(f"No database for profile '{paths.profile}'; run `sed init` first.")
    conn = db.connect(paths.db, readonly=True)
    try:
        yield conn
    finally:
        conn.close()


def write_conn(request: Request) -> Iterator[sqlite3.Connection]:
    """Connection for a write route; the route must wrap its writes in db.write_tx (busy -> 409)."""
    paths = _paths(request)
    if not paths.db.exists():
        raise PreconditionFailed(f"No database for profile '{paths.profile}'; run `sed init` first.")
    conn = db.connect(paths.db)
    try:
        yield conn
    finally:
        conn.close()


@dataclass
class CommonFilters:
    app: list[str] = field(default_factory=list)
    family: str | None = None
    vendor: str | None = None
    group: str | None = None
    period: str | None = None
    as_of: date | None = None  # None -> data as-of (sed.ingest.freshness.data_as_of)
    include_drafts: bool = False


def common_filters(
    app: list[str] = Query(default_factory=list, description="app_id (repeatable)"),
    family: str | None = Query(None),
    vendor: str | None = Query(None, description="vendor_id"),
    group: str | None = Query(None, description="assignment group"),
    period: str | None = Query(None, description="2026-W35, 2026-08 or 2026-Q3"),
    as_of: date | None = Query(None, description="YYYY-MM-DD (default: data as-of)"),
    include_drafts: bool = Query(False, description="Include unapproved AI content"),
) -> CommonFilters:
    if as_of is not None and not AS_OF_MIN <= as_of <= AS_OF_MAX:
        raise ValidationFailed(f"as_of must be between {AS_OF_MIN} and {AS_OF_MAX}", {"as_of": as_of.isoformat()})
    return CommonFilters(list(app), family, vendor, group, period, as_of, include_drafts)


def resolve_as_of(conn: sqlite3.Connection, request: Request, as_of: date | None) -> date:
    """Explicit as_of, else the data as-of, else today."""
    if as_of:
        return as_of
    from sed.ingest.freshness import data_as_of
    from sed.settings import load_settings

    return data_as_of(conn, load_settings(_paths(request))) or date.today()
