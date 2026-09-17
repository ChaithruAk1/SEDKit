"""Delivery API routes mounted at /api/delivery. Every route reads through `deps.read_conn` (query_only) and never
writes."""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, Request

from sed.api.deps import CommonFilters, common_filters, read_conn
from sed.api.findings import published_findings
from sed.api.models import FindingOut
from sed.errors import PreconditionFailed
from sed.modules.delivery.api_models import (
    DeliveryDocuments,
    DeliveryPortfolioOut,
    DeliveryProgress,
    DeliveryProjectOut,
    DeliveryProjectRow,
    DeliveryRaid,
    DeliveryTask,
    DeliveryWeek,
)
from sed.modules.delivery.queries import portfolio as P
from sed.modules.ops.queries.common import build_context

router = APIRouter()
FINDING_LIMIT = 200


def _row(item: dict[str, Any]) -> DeliveryProjectRow:
    project, plan, prog, raid = item["project"], item["plan"], item["progress"], item["raid"]
    open_tasks = [t for t in plan["tasks"] if t["is_milestone"] and not t["actual_finish"]]
    upcoming = sorted(open_tasks, key=lambda t: t["finish"] or "9999")
    return DeliveryProjectRow(
        project_id=project["project_id"],
        name=project["name"],
        app_id=project["app_id"],
        app_raw=project["app_raw"],
        phase=project["phase"],
        reported_rag=(project["rag_raw"] or "").lower() or None,
        computed_rag=item["health"]["rag"],
        reasons=item["health"]["reasons"],
        target_date=project["target_date"],
        start_date=project["start_date"],
        jira_keys=project["jira_keys"],
        confluence_space=project["confluence_space"],
        budget_base=project["budget_base"],
        plan_status_date=plan["status_date"],
        next_milestone=DeliveryTask(**upcoming[0]) if upcoming else None,
        worst_slip_days=max((t["slip_days"] or 0 for t in open_tasks), default=0),
        open_high_raid=sum(1 for i in raid if i["open"] and i["severity"] in ("high", "critical")),
        overdue_raid=sum(1 for i in raid if i["days_overdue"] > 0),
        points_done_pct=round(100.0 * prog.points_done / prog.points_total, 1) if prog.points_total else None,
        forecast_finish=prog.forecast_finish.isoformat() if prog.forecast_finish else None,
    )


def _findings(conn: sqlite3.Connection, as_of: Any, project_id: str | None = None) -> list[FindingOut]:
    items = published_findings(conn, as_of, kind="delivery_risk", limit=FINDING_LIMIT)
    if project_id:
        items = [f for f in items if f.subject_id == project_id]
    return items


@router.get("/portfolio", response_model=DeliveryPortfolioOut)
def portfolio(
    request: Request, f: CommonFilters = Depends(common_filters), conn: sqlite3.Connection = Depends(read_conn)
) -> DeliveryPortfolioOut:
    ctx = build_context(conn, request.app.state.paths, f)
    rows = [_row(item) for item in P.portfolio(conn, ctx.paths, ctx.as_of)]
    counts = {rag: sum(1 for r in rows if r.computed_rag == rag) for rag in ("red", "amber", "green")}
    counts["projects"] = len(rows)
    return DeliveryPortfolioOut(
        as_of=ctx.as_of.isoformat(), projects=rows, counts=counts, findings=_findings(conn, ctx.as_of)
    )


@router.get("/projects/{project_id}", response_model=DeliveryProjectOut)
def project(
    project_id: str,
    request: Request,
    f: CommonFilters = Depends(common_filters),
    conn: sqlite3.Connection = Depends(read_conn),
) -> DeliveryProjectOut:
    ctx = build_context(conn, request.app.state.paths, f)
    item = next((i for i in P.portfolio(conn, ctx.paths, ctx.as_of) if i["project"]["project_id"] == project_id), None)
    if item is None:
        raise PreconditionFailed(f"Unknown delivery project '{project_id}'")
    prog = item["progress"]
    return DeliveryProjectOut(
        as_of=ctx.as_of.isoformat(),
        project=_row(item),
        tasks=[DeliveryTask(**t) for t in item["plan"]["tasks"]],
        plan_versions=item["plan"]["versions"],
        raid=[DeliveryRaid(**r) for r in item["raid"]],
        progress=DeliveryProgress(
            stories=prog.stories,
            points_total=prog.points_total,
            points_done=prog.points_done,
            points_added_window=prog.points_added_window,
            scope_growth_pct=prog.scope_growth_pct,
            velocity_per_week=prog.velocity_per_week,
            forecast_finish=prog.forecast_finish.isoformat() if prog.forecast_finish else None,
            weekly=[DeliveryWeek(**w) for w in prog.weekly],
        ),
        documents=DeliveryDocuments(**item["documents"]),
        findings=_findings(conn, ctx.as_of, project_id),
    )
