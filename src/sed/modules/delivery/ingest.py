"""Delivery ingest targets: the project register, plan tasks per plan version and the RAID log.

Person fields arrive pseudonymised and free text scrubbed by the mappings. Raw values stay raw (phase, RAG, RAID status
and severity); `queries/portfolio.py` normalises them on read.
"""

from __future__ import annotations

import json
from typing import Any

from sed.ingest.target import Ctx, Reject, Target

PROJECT_COLUMNS = (
    "project_id",
    "name",
    "app_raw",
    "app_id",
    "phase",
    "rag_raw",
    "sponsor_pid",
    "manager_pid",
    "jira_keys_json",
    "confluence_space",
    "start_date",
    "target_date",
    "budget",
    "currency",
    "budget_base",
    "is_deleted",
)
MILESTONE_COLUMNS = (
    "project_id",
    "task_id",
    "status_date",
    "name",
    "is_milestone",
    "start_date",
    "finish_date",
    "baseline_finish",
    "actual_finish",
    "percent_complete",
)
RAID_COLUMNS = (
    "raid_id",
    "project_id",
    "raid_type",
    "title",
    "description",
    "owner_pid",
    "severity",
    "status",
    "raised_on",
    "due_date",
    "closed_on",
    "is_deleted",
)


def _text(value: Any) -> str | None:
    text = " ".join(str(value).split()) if value is not None else ""
    return text or None


def _required(rec: dict[str, Any], name: str) -> str:
    value = _text(rec.get(name))
    if value is None:
        raise Reject(f"missing {name}")
    return value


def build_project(rec: dict[str, Any], raw: dict[str, Any], ctx: Ctx) -> dict[str, Any]:
    keys = rec.get("jira_keys") or []
    if isinstance(keys, str):
        keys = [keys]
    currency = (_text(rec.get("currency")) or ctx.base_currency).upper()
    budget = rec.get("budget")
    return {
        "project_id": _required(rec, "project_id").upper(),
        "name": _required(rec, "name"),
        "app_raw": _text(rec.get("application")),
        "app_id": ctx.resolver.resolve("app", rec.get("application")) if rec.get("application") else None,
        "phase": _text(rec.get("phase")),
        "rag_raw": _text(rec.get("rag")),
        "sponsor_pid": rec.get("sponsor"),
        "manager_pid": rec.get("manager"),
        "jira_keys_json": json.dumps(sorted({str(k).strip().upper() for k in keys if str(k).strip()})),
        "confluence_space": (_text(rec.get("confluence_space")) or "").upper() or None,
        "start_date": rec.get("start_date"),
        "target_date": rec.get("target_date"),
        "budget": budget,
        "currency": currency,
        "budget_base": ctx.to_base(budget, currency),
        "is_deleted": 0,
    }


def build_milestone(rec: dict[str, Any], raw: dict[str, Any], ctx: Ctx) -> dict[str, Any]:
    percent = rec.get("percent_complete")
    return {
        "project_id": _required(rec, "project_id").upper(),
        "task_id": _required(rec, "task_id"),
        "status_date": _required(rec, "status_date"),
        "name": _text(rec.get("name")),
        "is_milestone": int(bool(rec.get("is_milestone"))),
        "start_date": rec.get("start_date"),
        "finish_date": rec.get("finish_date"),
        "baseline_finish": rec.get("baseline_finish"),
        "actual_finish": rec.get("actual_finish"),
        "percent_complete": float(percent) if percent not in (None, "") else None,
    }


def build_raid(rec: dict[str, Any], raw: dict[str, Any], ctx: Ctx) -> dict[str, Any]:
    return {
        "raid_id": _required(rec, "raid_id").upper(),
        "project_id": _required(rec, "project_id").upper(),
        "raid_type": (_text(rec.get("raid_type")) or "").casefold() or None,
        "title": rec.get("title"),
        "description": rec.get("description"),
        "owner_pid": rec.get("owner"),
        "severity": (_text(rec.get("severity")) or "").casefold() or None,
        "status": (_text(rec.get("status")) or "").casefold() or None,
        "raised_on": rec.get("raised_on"),
        "due_date": rec.get("due_date"),
        "closed_on": rec.get("closed_on"),
        "is_deleted": 0,
    }


TARGETS = {
    "delivery_project": Target(
        "delivery_project",
        "delivery_project",
        ("project_id",),
        PROJECT_COLUMNS,
        build_project,
        soft_delete=True,
        order=300,
    ),
    "delivery_milestone": Target(
        "delivery_milestone",
        "delivery_milestone",
        ("project_id", "task_id", "status_date"),
        MILESTONE_COLUMNS,
        build_milestone,
        order=310,
    ),
    "delivery_raid": Target(
        "delivery_raid",
        "delivery_raid",
        ("raid_id",),
        RAID_COLUMNS,
        build_raid,
        soft_delete=True,
        order=320,
    ),
}
