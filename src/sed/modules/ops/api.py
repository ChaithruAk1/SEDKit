"""Ops API routes mounted at /api/ops. Signatures are final for M2; the read models live in `queries/`.

Route order matters: static paths are declared before `{id}` paths. Every route reads through `deps.read_conn`
(query_only) and never writes: no snapshots, no rule-finding refresh.
"""

from __future__ import annotations

import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse

from sed.api.deps import CommonFilters, actor, common_filters, read_conn
from sed.modules.ops.api_models import (
    App360Out,
    AppsOut,
    AttentionOut,
    BacklogOut,
    CostsOut,
    LicensesOut,
    MttrOut,
    OpsFiltersOut,
    OpsOverview,
    RenewalsOut,
    SlaOut,
    TicketDetail,
    TicketPage,
    VendorTrendsOut,
    VolumesOut,
)
from sed.modules.ops.queries import apps as apps_q
from sed.modules.ops.queries import commercial, search
from sed.modules.ops.queries import overview as overview_q
from sed.modules.ops.queries import tickets as tickets_q
from sed.modules.ops.queries.common import Context, build_context

router = APIRouter()


def _ctx(request: Request, conn: sqlite3.Connection, f: CommonFilters | None = None) -> Context:
    return build_context(conn, request.app.state.paths, f)


@router.get("/filters", response_model=OpsFiltersOut)
def filters(conn: sqlite3.Connection = Depends(read_conn)) -> OpsFiltersOut:
    return apps_q.filter_options(conn)


@router.get("/overview", response_model=OpsOverview)
def overview(
    request: Request, f: CommonFilters = Depends(common_filters), conn: sqlite3.Connection = Depends(read_conn)
) -> OpsOverview:
    return overview_q.overview(_ctx(request, conn, f))


@router.get("/attention", response_model=AttentionOut)
def attention(
    request: Request,
    limit: int = Query(200, ge=1, le=1000),
    f: CommonFilters = Depends(common_filters),
    conn: sqlite3.Connection = Depends(read_conn),
) -> AttentionOut:
    return overview_q.attention(_ctx(request, conn, f), limit)


@router.get("/tickets/volumes", response_model=VolumesOut)
def ticket_volumes(
    request: Request,
    granularity: Literal["week", "month"] = "week",
    n: int = Query(12, ge=1, le=60),
    kind: str = "incident",
    f: CommonFilters = Depends(common_filters),
    conn: sqlite3.Connection = Depends(read_conn),
) -> VolumesOut:
    return tickets_q.volumes(_ctx(request, conn, f), granularity, n, kind)


@router.get("/tickets/sla", response_model=SlaOut)
def ticket_sla(
    request: Request,
    granularity: Literal["week", "month"] = "week",
    n: int = Query(12, ge=1, le=60),
    f: CommonFilters = Depends(common_filters),
    conn: sqlite3.Connection = Depends(read_conn),
) -> SlaOut:
    return tickets_q.sla(_ctx(request, conn, f), granularity, n)


@router.get("/tickets/mttr", response_model=MttrOut)
def ticket_mttr(
    request: Request,
    granularity: Literal["week", "month"] = "week",
    n: int = Query(12, ge=1, le=60),
    f: CommonFilters = Depends(common_filters),
    conn: sqlite3.Connection = Depends(read_conn),
) -> MttrOut:
    return tickets_q.mttr(_ctx(request, conn, f), granularity, n)


@router.get("/tickets/backlog", response_model=BacklogOut)
def ticket_backlog(
    request: Request, f: CommonFilters = Depends(common_filters), conn: sqlite3.Connection = Depends(read_conn)
) -> BacklogOut:
    return tickets_q.backlog(_ctx(request, conn, f))


@router.get("/tickets", response_model=TicketPage)
def tickets(
    request: Request,
    q: str | None = Query(None, max_length=200, description="Full-text search (scrubbed text only)"),
    kind: str | None = None,
    priority: list[int] = Query(default_factory=list),
    state: str | None = None,
    open: bool | None = None,
    stale: bool | None = None,
    sn_category: str | None = None,
    am_category: str | None = None,
    sort: Literal["opened_desc", "opened_asc", "priority", "updated_desc"] = "opened_desc",
    page: int = Query(1, ge=1, le=100_000),
    page_size: int = Query(50, ge=1, le=200),
    f: CommonFilters = Depends(common_filters),
    conn: sqlite3.Connection = Depends(read_conn),
) -> TicketPage:
    return search.search(
        _ctx(request, conn, f),
        q=q,
        kind=kind,
        priority=priority,
        state=state,
        is_open=open,
        stale=stale,
        sn_category=sn_category,
        am_category=am_category,
        sort=sort,
        page=page,
        page_size=page_size,
    )


@router.get("/tickets/{ticket_id}", response_model=TicketDetail)
def ticket_detail(
    ticket_id: str,
    include_drafts: bool = Query(False, description="Also show labels of completed, not yet reviewed AI runs"),
    conn: sqlite3.Connection = Depends(read_conn),
) -> TicketDetail:
    found = search.detail(conn, ticket_id, include_drafts)
    if found is None:
        raise HTTPException(status_code=404, detail=f"Unknown ticket '{ticket_id}'")
    return found


@router.get("/apps", response_model=AppsOut)
def apps(
    request: Request, f: CommonFilters = Depends(common_filters), conn: sqlite3.Connection = Depends(read_conn)
) -> AppsOut:
    return apps_q.apps(_ctx(request, conn, f))


@router.get("/apps/{app_id}", response_model=App360Out)
def app_360(
    app_id: str,
    request: Request,
    f: CommonFilters = Depends(common_filters),
    conn: sqlite3.Connection = Depends(read_conn),
) -> App360Out:
    found = apps_q.app_360(_ctx(request, conn, f), app_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"Unknown application '{app_id}'")
    return found


@router.get("/costs", response_model=CostsOut)
def costs(
    request: Request,
    group_by: Literal["app", "vendor", "category", "app_category"] = "app",
    months: int = Query(3, ge=1, le=24),
    f: CommonFilters = Depends(common_filters),
    conn: sqlite3.Connection = Depends(read_conn),
) -> CostsOut:
    return commercial.costs(_ctx(request, conn, f), group_by, months)


@router.get("/contracts/renewals", response_model=RenewalsOut)
def renewals(
    request: Request,
    days: int = Query(180, ge=1, le=1095),
    f: CommonFilters = Depends(common_filters),
    conn: sqlite3.Connection = Depends(read_conn),
) -> RenewalsOut:
    return commercial.renewals(_ctx(request, conn, f), days)


@router.get("/licenses/utilization", response_model=LicensesOut)
def licenses(
    request: Request, f: CommonFilters = Depends(common_filters), conn: sqlite3.Connection = Depends(read_conn)
) -> LicensesOut:
    return commercial.licenses(_ctx(request, conn, f))


@router.get("/vendors/sla-trend", response_model=VendorTrendsOut)
def vendor_sla_trend(
    request: Request,
    months: int = Query(6, ge=2, le=24),
    f: CommonFilters = Depends(common_filters),
    conn: sqlite3.Connection = Depends(read_conn),
) -> VendorTrendsOut:
    return commercial.vendor_trend(_ctx(request, conn, f), months)


# Not /tickets/export.xlsx: that path is matched by the "one ticket by id" route above, which would
# read "export.xlsx" as a ticket id. A sibling path cannot be broken by route ordering.
@router.get("/tickets-export.xlsx", include_in_schema=False)
def export_tickets(
    request: Request,
    q: str | None = Query(None, max_length=200),
    kind: str | None = None,
    priority: list[int] = Query(default_factory=list),
    state: str | None = None,
    open: bool | None = None,
    stale: bool | None = None,
    sn_category: str | None = None,
    am_category: str | None = None,
    sort: Literal["opened_desc", "opened_asc", "priority", "updated_desc"] = "opened_desc",
    columns: str | None = Query(None, max_length=2000, description="Comma-separated column keys, in order"),
    limit: int = Query(20_000, ge=1, le=100_000),
    f: CommonFilters = Depends(common_filters),
    conn: sqlite3.Connection = Depends(read_conn),
) -> FileResponse:
    # The ticket list as the reader is looking at it: same filters, same columns, as a workbook. Read-only, and the
    # file is written into the profile's out folder so nothing escapes DATA_DIR.
    from sed.modules.ops.export import write_ticket_workbook

    page = search.search(
        _ctx(request, conn, f),
        q=q,
        kind=kind,
        priority=priority,
        state=state,
        is_open=open,
        stale=stale,
        sn_category=sn_category,
        am_category=am_category,
        sort=sort,
        page=1,
        page_size=limit,
    )
    keys = [k.strip() for k in (columns or "").split(",") if k.strip()] or None
    paths = request.app.state.paths
    path = write_ticket_workbook(paths, page.items, keys)
    # Nothing leaves without its audit entry: when the entry cannot be written the file is removed and refused.
    from sed.audit.record import AuditUnavailable, file_sha256, record

    chosen = {
        "q": q,
        "kind": kind,
        "priority": priority or None,
        "state": state,
        "open": open,
        "stale": stale,
        "sn_category": sn_category,
        "am_category": am_category,
        "sort": sort,
        "app": f.app or None,
        "family": f.family,
        "vendor": f.vendor,
        "group": f.group,
        "period": f.period,
        "as_of": f.as_of.isoformat() if f.as_of else None,
        "include_drafts": f.include_drafts or None,
    }
    detail = {
        "rows": len(page.items),
        "matching": page.total,
        "columns": keys,
        "filters": {k: v for k, v in chosen.items() if v is not None},
        "sha256": file_sha256(path),
    }
    try:
        summary = f"Downloaded {len(page.items)} tickets as a workbook."
        record(
            paths,
            actor(request),
            "download",
            summary=summary,
            target_type="ticket_workbook",
            target_id=path.name,
            detail=detail,
        )
    except AuditUnavailable:
        path.unlink(missing_ok=True)
        raise
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=path.name,
        headers={"Cache-Control": "no-store"},
    )
