"""Ops ingest hooks: alias seeding, vendor-group overrides and the reresolve re-link of ops tables.

Registered by the ops manifest as `ingest_hooks="sed.modules.ops.ingest_hooks:HOOKS"`; the loader calls them through
the module registry (see `sed.ingest.hooks`).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, ClassVar

from sed.ingest.hooks import BaseIngestHooks, ImportSession, RelinkUpdate, read_optional_yaml, seed_aliases
from sed.ingest.targets import (
    GROUPS_STATE_KEY,
    GroupDirectory,
    doc_page_app,
    group_directory,
    resolve_app,
    work_item_app,
)
from sed.settings import load_layered

# Ticket kinds whose vendor comes from the assignment group (vendor_groups.yaml overrides apply before their files).
VENDOR_GROUP_KINDS = frozenset({"incident", "sc_req_item"})
# Tables re-linked by `sed import reresolve`, in result-key order.
RELINK_TABLES = (
    "ticket",
    "contract",
    "license",
    "cost_line",
    "application",
    "assignment_group",
    "work_item",
    "doc_page",
)
# Tables whose app_id and vendor_id are re-linked from app_raw / vendor_raw, with their key column.
RELINK_KEYS = (
    ("ticket", "ticket_id"),
    ("contract", "contract_id"),
    ("license", "license_id"),
    ("cost_line", "cost_line_id"),
)
RELINK_SQL = {
    **{table: f"UPDATE {table} SET app_id = ?, vendor_id = ? WHERE {key} = ?" for table, key in RELINK_KEYS},
    "application": "UPDATE application SET primary_vendor_id = ? WHERE app_id = ?",
    "assignment_group": "UPDATE assignment_group SET vendor_id = ?, app_id = ? WHERE name = ?",
    "work_item": "UPDATE work_item SET app_id = ? WHERE issue_key = ?",
    "doc_page": "UPDATE doc_page SET app_id = ? WHERE page_id = ?",
}


def seed_config_aliases(session: ImportSession) -> int:
    """Seed aliases from DATA_DIR config/aliases.yaml ({kind: {raw: target}}) and config/ops/ci_to_app.yaml."""
    config = session.paths.config
    seeded = seed_aliases(session.resolver, read_optional_yaml(config / "aliases.yaml"))
    seeded += seed_aliases(session.resolver, read_optional_yaml(config / "ops" / "ci_to_app.yaml"), kind="ci")
    return seeded


def apply_vendor_group_overrides(session: ImportSession) -> None:
    """config/ops/vendor_groups.yaml `groups: {group: vendor}` overrides the vendor of an assignment group."""
    groups = load_layered("ops/vendor_groups.yaml", session.paths).get("groups") or {}
    directory = group_directory(session.state, session.conn)
    resolver = session.resolver
    for group, vendor in groups.items():
        vendor_id = resolver.resolve("vendor", vendor, record_unmapped=False)
        if vendor and not vendor_id:
            resolver.unmapped["vendor"][str(vendor)] += 1
        directory.set_group(group, vendor_id, None)


def _json_list(value: str | None) -> list[Any]:
    try:
        parsed = json.loads(value) if value else []
    except ValueError:
        return []
    return parsed if isinstance(parsed, list) else [parsed]


def relink_updates(session: ImportSession) -> list[RelinkUpdate]:
    """App and vendor ids recomputed from stored raw values; only changed rows are returned.

    Assignment groups are re-resolved first and the ticket pass uses the result, so a new vendor alias reaches the
    tickets of groups with that vendor spelling. Vendor-group overrides apply only to VENDOR_GROUP_KINDS, as at import.
    """
    conn, resolver = session.conn, session.resolver
    updates: dict[str, list[tuple[Any, ...]]] = {table: [] for table in RELINK_TABLES}

    base = GroupDirectory()
    for r in conn.execute(
        "SELECT name, vendor_raw, app_raw, vendor_id, app_id FROM assignment_group ORDER BY is_deleted DESC, name"
    ):
        vendor = resolver.resolve("vendor", r["vendor_raw"], record_unmapped=False)
        app = resolver.resolve("app", r["app_raw"], record_unmapped=False)
        base.set_group(r["name"], vendor, app)
        if (vendor, app) != (r["vendor_id"], r["app_id"]):
            updates["assignment_group"].append((vendor, app, r["name"]))
    session.state[GROUPS_STATE_KEY] = base.copy()
    apply_vendor_group_overrides(session)
    overridden = group_directory(session.state, conn)

    for r in conn.execute(
        "SELECT ticket_id, kind, business_service_raw, cmdb_ci_raw, assignment_group, app_id, vendor_id FROM ticket"
    ):
        app = resolve_app(resolver, r["business_service_raw"], r["cmdb_ci_raw"])
        directory = overridden if r["kind"] in VENDOR_GROUP_KINDS else base
        vendor = directory.vendor_for_group(resolver, r["assignment_group"])
        if app != r["app_id"] or vendor != r["vendor_id"]:
            updates["ticket"].append((app, vendor, r["ticket_id"]))
    for table, key in RELINK_KEYS[1:]:
        for r in conn.execute(f"SELECT {key}, app_raw, vendor_raw, app_id, vendor_id FROM {table}"):
            app = resolver.resolve("app", r["app_raw"], record_unmapped=False)
            vendor = resolver.resolve("vendor", r["vendor_raw"], record_unmapped=False)
            if (app, vendor) != (r["app_id"], r["vendor_id"]):
                updates[table].append((app, vendor, r[key]))
    for r in conn.execute("SELECT app_id, vendor_raw, primary_vendor_id FROM application"):
        vendor = resolver.resolve("vendor", r["vendor_raw"], record_unmapped=False)
        if vendor != r["primary_vendor_id"]:
            updates["application"].append((vendor, r["app_id"]))
    for r in conn.execute("SELECT issue_key, project_key, components_json, app_id FROM work_item"):
        # The Jira project name is not stored, so an app found only through it at import is kept.
        app = work_item_app(resolver, _json_list(r["components_json"]), r["project_key"])
        if app and app != r["app_id"]:
            updates["work_item"].append((app, r["issue_key"]))
    for r in conn.execute("SELECT page_id, space_key, labels_json, app_id FROM doc_page"):
        app = doc_page_app(resolver, r["space_key"], _json_list(r["labels_json"]))
        if app != r["app_id"]:
            updates["doc_page"].append((app, r["page_id"]))
    return [RelinkUpdate(table, RELINK_SQL[table], updates[table]) for table in RELINK_TABLES]


class OpsIngestHooks(BaseIngestHooks):
    """Hooks of the ops module."""

    # Fuzzy suggestions only for kinds whose raw values are names (Jira keys and Confluence spaces are not).
    suggestion_kinds: ClassVar[tuple[str, ...]] = ("app", "vendor", "group", "ci")
    # Tickets resolve a CI through both app and ci aliases (resolve_app), so either alias resolves either value.
    resolved_by: ClassVar[Mapping[str, tuple[str, ...]]] = {"app": ("app", "ci"), "ci": ("app", "ci")}

    def session_start(self, session: ImportSession) -> None:
        session.state[GROUPS_STATE_KEY] = GroupDirectory.load(session.conn)
        seed_config_aliases(session)

    def before_target(self, session: ImportSession, target: Any, spec: Any) -> None:
        if spec.target == "ticket" and spec.constants.get("kind") in VENDOR_GROUP_KINDS:
            apply_vendor_group_overrides(session)

    def relink(self, session: ImportSession) -> list[RelinkUpdate]:
        return relink_updates(session)


HOOKS = OpsIngestHooks()
