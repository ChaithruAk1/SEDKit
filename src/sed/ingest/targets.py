"""Canonical import targets: keys, written columns, derived fields and post-load hooks.

A mapping produces a record of canonical field values (already PII-processed: person fields are pseudonyms,
free-text fields are scrubbed; the pre-scrub text is available separately for content hashes). Each target turns
that record into a DB row, resolving references through the Resolver and rejecting rows it cannot use.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from sed.ingest.pii import PiiProcessor, text_hash
from sed.ingest.resolve import Resolver, normalize_alias

CLOSED_STATES = {
    "resolved",
    "closed",
    "canceled",
    "cancelled",
    "closed complete",
    "closed incomplete",
    "closed skipped",
    "closed resolved",
    "complete",
    "completed",
}
TICKET_PREFIX_KIND = {
    "INC": "incident",
    "RITM": "sc_req_item",
    "REQ": "sc_req_item",
    "CHG": "change_request",
    "PRB": "problem",
}


class Reject(Exception):
    """Row cannot be loaded; reason is recorded in row_reject."""


@dataclass
class Ctx:
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
    _ticket_ids: set[str] | None = None
    _license_ids: set[str] | None = None
    _contract_ids: set[str] | None = None

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

    def ticket_exists(self, ticket_id: str) -> bool:
        if self._ticket_ids is None:
            self._ticket_ids = {r[0] for r in self.conn.execute("SELECT ticket_id FROM ticket")}
        return ticket_id in self._ticket_ids

    def license_exists(self, license_id: str) -> bool:
        if self._license_ids is None:
            self._license_ids = {r[0] for r in self.conn.execute("SELECT license_id FROM license WHERE is_deleted = 0")}
        return license_id in self._license_ids

    def contract_id_for(self, raw: Any) -> str | None:
        if raw in (None, ""):
            return None
        if self._contract_ids is None:
            self._contract_ids = {r[0] for r in self.conn.execute("SELECT contract_id FROM contract")}
        value = str(raw).strip()
        if value in self._contract_ids:
            return value
        self.resolver.unmapped["contract"][value] += 1
        return None


@dataclass
class Target:
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


def _req(rec: dict[str, Any], name: str) -> Any:
    value = rec.get(name)
    if value in (None, ""):
        raise Reject(f"missing {name}")
    return value


def _json(value: Any) -> str | None:
    if value in (None, [], ""):
        return None
    return json.dumps(value if isinstance(value, list) else [value], ensure_ascii=False)


def _max_ts(*values: Any) -> str | None:
    present = [v for v in values if v]
    return max(present) if present else None


# ---------------------------------------------------------------------------
# master data
# ---------------------------------------------------------------------------


def build_vendor(rec: dict[str, Any], raw: dict[str, Any], ctx: Ctx) -> dict[str, Any]:
    return {
        "vendor_id": str(_req(rec, "vendor_id")),
        "name": _req(rec, "name"),
        "vendor_type": rec.get("vendor_type"),
        "tier": rec.get("tier"),
        "sla_target_pct": rec.get("sla_target_pct"),
        "is_deleted": 0,
    }


def after_vendor(ctx: Ctx, rows: list[dict[str, Any]]) -> None:
    ctx.resolver.register_ids("vendor", [row["vendor_id"] for row in rows])
    for row in rows:
        ctx.resolver.add_alias("vendor", row["name"], row["vendor_id"], "auto_exact")
        ctx.resolver.add_alias("vendor", row["vendor_id"], row["vendor_id"], "auto_exact")


def build_application(rec: dict[str, Any], raw: dict[str, Any], ctx: Ctx) -> dict[str, Any]:
    vendor_raw = rec.get("vendor")
    return {
        "app_id": str(_req(rec, "app_id")),
        "name": _req(rec, "name"),
        "app_family": rec.get("app_family"),
        "business_criticality": rec.get("business_criticality"),
        "life_cycle_stage": rec.get("life_cycle_stage"),
        "it_owner_pid": rec.get("it_owner"),
        "cost_center": rec.get("cost_center"),
        "vendor_raw": vendor_raw,
        "primary_vendor_id": ctx.resolver.resolve("vendor", vendor_raw),
        "is_deleted": 0,
    }


def after_application(ctx: Ctx, rows: list[dict[str, Any]]) -> None:
    ctx.resolver.register_ids("app", [row["app_id"] for row in rows])
    for row in rows:
        ctx.resolver.add_alias("app", row["name"], row["app_id"], "auto_exact")
        ctx.resolver.add_alias("app", row["app_id"], row["app_id"], "auto_exact")


def build_ci_rel(rec: dict[str, Any], raw: dict[str, Any], ctx: Ctx) -> dict[str, Any]:
    return {
        "parent_ci": str(_req(rec, "parent")),
        "child_ci": str(_req(rec, "child")),
        "type": str(rec.get("type") or "Depends on::Used by"),
        "is_deleted": 0,
    }


def after_ci_rel(ctx: Ctx, rows: list[dict[str, Any]]) -> None:
    for row in rows:
        app_id = ctx.resolver.resolve("app", row["parent_ci"], record_unmapped=False)
        if app_id:
            ctx.resolver.add_alias("ci", row["child_ci"], app_id, "cmdb_rel")
        else:
            child_app = ctx.resolver.resolve("app", row["child_ci"], record_unmapped=False)
            if child_app:
                ctx.resolver.add_alias("ci", row["parent_ci"], child_app, "cmdb_rel")


def build_group(rec: dict[str, Any], raw: dict[str, Any], ctx: Ctx) -> dict[str, Any]:
    name = str(_req(rec, "name"))
    vendor_raw = rec.get("vendor")
    app_raw = rec.get("application")
    vendor_id = ctx.resolver.resolve("vendor", vendor_raw)
    app_id = ctx.resolver.resolve("app", app_raw)
    ctx.resolver.set_group(name, vendor_id, app_id)
    return {
        "name": name,
        "vendor_raw": vendor_raw,
        "vendor_id": vendor_id,
        "app_raw": app_raw,
        "app_id": app_id,
        "is_deleted": 0,
    }


def build_contract(rec: dict[str, Any], raw: dict[str, Any], ctx: Ctx) -> dict[str, Any]:
    number = str(_req(rec, "contract_number"))
    end_date = rec.get("end_date")
    notice_days = rec.get("notice_period_days")
    notice_deadline = None
    if end_date and notice_days is not None:
        notice_deadline = (date.fromisoformat(end_date) - timedelta(days=int(notice_days))).isoformat()
    currency = (rec.get("currency") or ctx.base_currency).upper()
    value = rec.get("annual_value")
    status = rec.get("renewal_status")
    return {
        "contract_id": number,
        "contract_number": number,
        "vendor_raw": rec.get("vendor"),
        "vendor_id": ctx.resolver.resolve("vendor", rec.get("vendor")),
        "app_raw": rec.get("application"),
        "app_id": ctx.resolver.resolve("app", rec.get("application")),
        "product": rec.get("product"),
        "start_date": rec.get("start_date"),
        "end_date": end_date,
        "notice_period_days": notice_days,
        "notice_deadline": notice_deadline,
        "auto_renew": None if rec.get("auto_renew") is None else int(bool(rec.get("auto_renew"))),
        "renewal_status": status.lower().replace(" ", "_").replace("-", "_") if isinstance(status, str) else status,
        "annual_value": value,
        "currency": currency,
        "annual_value_base": ctx.to_base(value, currency),
        "owner_pid": rec.get("owner"),
        "comments_scrubbed": rec.get("comments"),
        "is_deleted": 0,
    }


def build_license(rec: dict[str, Any], raw: dict[str, Any], ctx: Ctx) -> dict[str, Any]:
    return {
        "license_id": str(_req(rec, "license_id")),
        "app_raw": rec.get("application"),
        "app_id": ctx.resolver.resolve("app", rec.get("application")),
        "vendor_raw": rec.get("vendor"),
        "vendor_id": ctx.resolver.resolve("vendor", rec.get("vendor")),
        "contract_raw": rec.get("contract_number"),
        "contract_id": ctx.contract_id_for(rec.get("contract_number")),
        "product": rec.get("product"),
        "license_metric": rec.get("license_metric"),
        "entitled_qty": rec.get("entitled_qty"),
        "unit_cost_base": ctx.to_base(rec.get("unit_cost"), rec.get("currency")),
        "is_deleted": 0,
    }


def build_license_usage(rec: dict[str, Any], raw: dict[str, Any], ctx: Ctx) -> dict[str, Any]:
    license_id = str(_req(rec, "license_id"))
    if not ctx.license_exists(license_id):
        raise Reject("unknown license_id (not in license inventory)")
    as_of = rec.get("as_of_date") or ctx.as_of
    if not as_of:
        raise Reject("missing as-of date")
    return {
        "license_id": license_id,
        "as_of_date": as_of,
        "assigned_qty": rec.get("assigned_qty"),
        "active_qty_90d": rec.get("active_qty_90d"),
    }


_PERIOD_RE = re.compile(r"^\d{4}-\d{2}$")


def build_cost_line(rec: dict[str, Any], raw: dict[str, Any], ctx: Ctx) -> dict[str, Any]:
    period = str(_req(rec, "period"))
    if not _PERIOD_RE.match(period):
        raise Reject(f"period '{period}' is not YYYY-MM")
    amount = rec.get("amount")
    if amount is None:
        raise Reject("missing amount")
    line_type = (rec.get("line_type") or ctx.constants.get("line_type") or "actual").lower()
    currency = (rec.get("currency") or ctx.base_currency).upper()
    as_of = ctx.as_of if line_type in {"budget", "forecast"} else None
    natural = "|".join(
        str(x or "")
        for x in (
            rec.get("application"),
            rec.get("vendor"),
            rec.get("contract_number"),
            period,
            line_type,
            rec.get("cost_category"),
            rec.get("cost_center"),
            as_of,
        )
    )
    return {
        "cost_line_id": hashlib.sha256(natural.lower().encode("utf-8")).hexdigest()[:32],
        "app_raw": rec.get("application"),
        "app_id": ctx.resolver.resolve("app", rec.get("application")),
        "vendor_raw": rec.get("vendor"),
        "vendor_id": ctx.resolver.resolve("vendor", rec.get("vendor")),
        "contract_raw": rec.get("contract_number"),
        "contract_id": ctx.contract_id_for(rec.get("contract_number")),
        "period": period,
        "line_type": line_type,
        "cost_category": rec.get("cost_category"),
        "amount": amount,
        "currency": currency,
        "amount_base": ctx.to_base(amount, currency),
        "cost_center": rec.get("cost_center"),
        "as_of": as_of,
    }


# ---------------------------------------------------------------------------
# work data
# ---------------------------------------------------------------------------


def is_open_state(state: Any, resolved_at: Any, closed_at: Any) -> int:
    if isinstance(state, str) and state.strip():
        normalized = re.sub(r"[^a-z]+", " ", state.lower()).strip()
        return 0 if normalized in CLOSED_STATES else 1
    return 0 if (resolved_at or closed_at) else 1


def build_ticket(rec: dict[str, Any], raw: dict[str, Any], ctx: Ctx) -> dict[str, Any]:
    kind = ctx.constants.get("kind") or rec.get("kind")
    if kind not in {"incident", "sc_req_item", "change_request", "problem"}:
        raise Reject(f"unknown ticket kind {kind!r}")
    number = str(_req(rec, "number")).strip()
    opened, resolved, closed = rec.get("opened_at"), rec.get("resolved_at"), rec.get("closed_at")
    updated = rec.get("sys_updated_on")
    if not updated:
        updated = _max_ts(opened, resolved, closed)
        if not updated:
            raise Reject("no sys_updated_on and no lifecycle timestamps")
        ctx.warn("sys_updated_on missing; derived from lifecycle timestamps")
    if kind == "incident" and resolved is None and closed and not is_open_state(rec.get("state"), None, closed):
        resolved = closed
    open_flag = is_open_state(rec.get("state"), resolved, closed)
    open_hash = text_hash(ctx.salt, kind, raw.get("short_description"), raw.get("description"))
    resolved_hash = None
    if not open_flag:
        resolved_hash = text_hash(
            ctx.salt,
            kind,
            raw.get("short_description"),
            raw.get("description"),
            rec.get("close_code"),
            raw.get("close_notes"),
        )
    group = rec.get("assignment_group")
    return {
        "ticket_id": f"{kind}:{number}",
        "kind": kind,
        "number": number,
        "sys_id": rec.get("sys_id"),
        "app_id": ctx.resolver.resolve_app(rec.get("business_service"), rec.get("cmdb_ci")),
        "cmdb_ci_raw": rec.get("cmdb_ci"),
        "business_service_raw": rec.get("business_service"),
        "short_description": rec.get("short_description"),
        "description": rec.get("description"),
        "close_notes": rec.get("close_notes"),
        "category": rec.get("category"),
        "subcategory": rec.get("subcategory"),
        "priority": rec.get("priority"),
        "impact": rec.get("impact"),
        "urgency": rec.get("urgency"),
        "state": rec.get("state"),
        "is_open": open_flag,
        "assignment_group": group,
        "assigned_to_pid": rec.get("assigned_to"),
        "caller_pid": rec.get("caller"),
        "vendor_id": ctx.resolver.vendor_for_group(group),
        "opened_at": opened,
        "resolved_at": resolved,
        "closed_at": closed,
        "sys_updated_on": updated,
        "made_sla": None if rec.get("made_sla") is None else int(bool(rec.get("made_sla"))),
        "reassignment_count": rec.get("reassignment_count"),
        "reopen_count": rec.get("reopen_count"),
        "close_code": rec.get("close_code"),
        "problem_id": rec.get("problem_id"),
        "caused_by": rec.get("caused_by"),
        "parent_incident": rec.get("parent_incident"),
        "change_type": rec.get("change_type"),
        "risk": rec.get("risk"),
        "start_date": rec.get("start_date"),
        "end_date": rec.get("end_date"),
        "open_hash": open_hash,
        "resolved_hash": resolved_hash,
        "stale_open": 0,
        "raw_keep_json": rec.get("__raw_keep__"),
    }


def build_task_sla(rec: dict[str, Any], raw: dict[str, Any], ctx: Ctx) -> dict[str, Any]:
    task = str(_req(rec, "task")).strip()
    m = re.match(r"^([A-Z]+)\d+", task)
    kind = TICKET_PREFIX_KIND.get(m.group(1)) if m else None
    if not kind:
        raise Reject(f"cannot infer ticket kind from task number {task!r}")
    ticket_id = f"{kind}:{task}"
    if not ctx.ticket_exists(ticket_id):
        raise Reject("task_sla for a ticket that is not imported")
    name = str(_req(rec, "sla_name"))
    lowered = name.lower()
    sla_type = "response" if "response" in lowered else "resolution" if "resol" in lowered else "other"
    return {
        "ticket_id": ticket_id,
        "sla_name": name,
        "sla_type": sla_type,
        "stage": rec.get("stage"),
        "has_breached": None if rec.get("has_breached") is None else int(bool(rec.get("has_breached"))),
        "start_time": rec.get("start_time") or "",
        "end_time": rec.get("end_time"),
        "business_duration_s": rec.get("business_duration"),
        "sla_sys_id": rec.get("sys_id"),
    }


def build_work_item(rec: dict[str, Any], raw: dict[str, Any], ctx: Ctx) -> dict[str, Any]:
    key = str(_req(rec, "issue_key")).strip()
    project = rec.get("project_key") or key.split("-")[0]
    components = rec.get("components") or []
    if isinstance(components, str):
        components = [components]
    app_id = None
    for comp in components:
        app_id = ctx.resolver.resolve("jira_component", comp, record_unmapped=False) or ctx.resolver.resolve(
            "app", comp, record_unmapped=False
        )
        if app_id:
            break
    if not app_id:
        app_id = ctx.resolver.resolve("jira_project", project, record_unmapped=False) or ctx.resolver.resolve(
            "app", rec.get("project_name"), record_unmapped=False
        )
    if not app_id:
        ctx.resolver.unmapped["jira_project"][str(project)] += 1
    status_category = rec.get("status_category")
    if not status_category and rec.get("status"):
        status_category = "Done" if rec.get("resolved") else "In Progress"
    return {
        "issue_key": key,
        "project_key": project,
        "app_raw": project,
        "app_id": app_id,
        "summary": rec.get("summary"),
        "issue_type": rec.get("issue_type"),
        "status": rec.get("status"),
        "status_category": status_category,
        "priority": rec.get("priority"),
        "created": rec.get("created"),
        "updated": rec.get("updated"),
        "resolved": rec.get("resolved"),
        "sprint_json": _json(rec.get("sprint")),
        "labels_json": _json(rec.get("labels")),
        "components_json": _json(components),
        "fix_versions_json": _json(rec.get("fix_versions")),
        "parent_key": rec.get("parent_key"),
        "story_points": rec.get("story_points"),
        "assignee_pid": rec.get("assignee"),
    }


_PAGE_TYPES = (
    ("kb", ("kb", "knowledge", "how-to", "howto", "faq", "troubleshooting")),
    ("runbook", ("runbook", "operations", "procedure", "sop")),
    ("release_note", ("release", "release-notes", "changelog")),
    ("roadmap", ("roadmap", "planning")),
)


def build_doc_page(rec: dict[str, Any], raw: dict[str, Any], ctx: Ctx) -> dict[str, Any]:
    labels = rec.get("labels") or []
    if isinstance(labels, str):
        labels = [part.strip() for part in labels.split(",") if part.strip()]
    title = str(_req(rec, "title"))
    haystack = " ".join([title.lower(), *[lbl.lower() for lbl in labels]])
    page_type = next((ptype for ptype, words in _PAGE_TYPES if any(w in haystack for w in words)), "other")
    space = str(_req(rec, "space_key"))
    app_id = ctx.resolver.resolve("confluence_space", space, record_unmapped=False)
    if not app_id:
        for lbl in labels:
            app_id = ctx.resolver.resolve("app", lbl, record_unmapped=False)
            if app_id:
                break
    if not app_id:
        ctx.resolver.unmapped["confluence_space"][space] += 1
    return {
        "page_id": str(_req(rec, "page_id")),
        "space_key": space,
        "app_raw": space,
        "app_id": app_id,
        "title": title,
        "labels_json": _json(labels),
        "page_type": page_type,
        "last_updated": rec.get("last_updated"),
        "body_text_scrubbed": rec.get("body"),
        "is_deleted": 0,
    }


def _cols(*names: str) -> tuple[str, ...]:
    return names


def build_person(rec: dict[str, Any], raw: dict[str, Any], ctx: Ctx) -> dict[str, Any]:
    """People directory rows only grow the (hash-only) person dictionary; nothing is written to a table."""
    if not rec.get("name"):
        raise Reject("missing name")
    return {"pid": rec["name"]}


TARGETS: dict[str, Target] = {
    "person_directory": Target("person_directory", "", ("pid",), ("pid",), build_person),
    "vendor": Target(
        "vendor",
        "vendor",
        ("vendor_id",),
        _cols("vendor_id", "name", "vendor_type", "tier", "sla_target_pct", "is_deleted"),
        build_vendor,
        soft_delete=True,
        after_load=after_vendor,
    ),
    "application": Target(
        "application",
        "application",
        ("app_id",),
        _cols(
            "app_id",
            "name",
            "app_family",
            "business_criticality",
            "life_cycle_stage",
            "it_owner_pid",
            "cost_center",
            "vendor_raw",
            "primary_vendor_id",
            "is_deleted",
        ),
        build_application,
        soft_delete=True,
        after_load=after_application,
    ),
    "ci_rel": Target(
        "ci_rel",
        "ci_rel",
        ("parent_ci", "child_ci", "type"),
        _cols("parent_ci", "child_ci", "type", "is_deleted"),
        build_ci_rel,
        soft_delete=True,
        after_load=after_ci_rel,
    ),
    "assignment_group": Target(
        "assignment_group",
        "assignment_group",
        ("name",),
        _cols("name", "vendor_raw", "vendor_id", "app_raw", "app_id", "is_deleted"),
        build_group,
        soft_delete=True,
    ),
    "contract": Target(
        "contract",
        "contract",
        ("contract_id",),
        _cols(
            "contract_id",
            "contract_number",
            "vendor_raw",
            "vendor_id",
            "app_raw",
            "app_id",
            "product",
            "start_date",
            "end_date",
            "notice_period_days",
            "notice_deadline",
            "auto_renew",
            "renewal_status",
            "annual_value",
            "currency",
            "annual_value_base",
            "owner_pid",
            "comments_scrubbed",
            "is_deleted",
        ),
        build_contract,
        soft_delete=True,
    ),
    "license": Target(
        "license",
        "license",
        ("license_id",),
        _cols(
            "license_id",
            "app_raw",
            "app_id",
            "vendor_raw",
            "vendor_id",
            "contract_raw",
            "contract_id",
            "product",
            "license_metric",
            "entitled_qty",
            "unit_cost_base",
            "is_deleted",
        ),
        build_license,
        soft_delete=True,
    ),
    "license_usage": Target(
        "license_usage",
        "license_usage",
        ("license_id", "as_of_date"),
        _cols("license_id", "as_of_date", "assigned_qty", "active_qty_90d"),
        build_license_usage,
        snapshot_field="as_of_date",
    ),
    "cost_line": Target(
        "cost_line",
        "cost_line",
        ("cost_line_id",),
        _cols(
            "cost_line_id",
            "app_raw",
            "app_id",
            "vendor_raw",
            "vendor_id",
            "contract_raw",
            "contract_id",
            "period",
            "line_type",
            "cost_category",
            "amount",
            "currency",
            "amount_base",
            "cost_center",
            "as_of",
        ),
        build_cost_line,
        snapshot_field="as_of",
    ),
    "ticket": Target(
        "ticket",
        "ticket",
        ("ticket_id",),
        _cols(
            "ticket_id",
            "kind",
            "number",
            "sys_id",
            "app_id",
            "cmdb_ci_raw",
            "business_service_raw",
            "short_description",
            "description",
            "close_notes",
            "category",
            "subcategory",
            "priority",
            "impact",
            "urgency",
            "state",
            "is_open",
            "assignment_group",
            "assigned_to_pid",
            "caller_pid",
            "vendor_id",
            "opened_at",
            "resolved_at",
            "closed_at",
            "sys_updated_on",
            "made_sla",
            "reassignment_count",
            "reopen_count",
            "close_code",
            "problem_id",
            "caused_by",
            "parent_incident",
            "change_type",
            "risk",
            "start_date",
            "end_date",
            "open_hash",
            "resolved_hash",
            "stale_open",
            "raw_keep_json",
        ),
        build_ticket,
        updated_field="sys_updated_on",
        active_scope=("kind", "incident"),
    ),
    "task_sla": Target(
        "task_sla",
        "task_sla",
        ("ticket_id", "sla_name", "start_time"),
        _cols(
            "ticket_id",
            "sla_name",
            "sla_type",
            "stage",
            "has_breached",
            "start_time",
            "end_time",
            "business_duration_s",
            "sla_sys_id",
        ),
        build_task_sla,
    ),
    "work_item": Target(
        "work_item",
        "work_item",
        ("issue_key",),
        _cols(
            "issue_key",
            "project_key",
            "app_raw",
            "app_id",
            "summary",
            "issue_type",
            "status",
            "status_category",
            "priority",
            "created",
            "updated",
            "resolved",
            "sprint_json",
            "labels_json",
            "components_json",
            "fix_versions_json",
            "parent_key",
            "story_points",
            "assignee_pid",
        ),
        build_work_item,
        updated_field="updated",
    ),
    "doc_page": Target(
        "doc_page",
        "doc_page",
        ("page_id",),
        _cols(
            "page_id",
            "space_key",
            "app_raw",
            "app_id",
            "title",
            "labels_json",
            "page_type",
            "last_updated",
            "body_text_scrubbed",
            "is_deleted",
        ),
        build_doc_page,
        updated_field="last_updated",
        soft_delete=True,
    ),
}

# Dependency order for multi-file imports.
TARGET_ORDER = [
    "person_directory",
    "vendor",
    "application",
    "ci_rel",
    "assignment_group",
    "contract",
    "license",
    "license_usage",
    "cost_line",
    "ticket",
    "task_sla",
    "work_item",
    "doc_page",
]
KIND_ORDER = {"problem": 0, "change_request": 1, "incident": 2, "sc_req_item": 3}


def normalize_key_part(value: Any) -> str:
    return normalize_alias(value)
