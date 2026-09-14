"""Ops API routes mounted at /api/ops. Signatures are final for M2; bodies belong to ws5-api-ops.

Route order matters: static paths are declared before `{id}` paths.
"""

from __future__ import annotations

import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request

from sed.api.deps import CommonFilters, common_filters, read_conn
from sed.errors import NotImplementedByWorkstream
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

WS = "ws5-api-ops"
router = APIRouter()


@router.get("/filters", response_model=OpsFiltersOut)
def filters(conn: sqlite3.Connection = Depends(read_conn)) -> OpsFiltersOut:
    raise NotImplementedByWorkstream(WS)


@router.get("/overview", response_model=OpsOverview)
def overview(
    request: Request, f: CommonFilters = Depends(common_filters), conn: sqlite3.Connection = Depends(read_conn)
) -> OpsOverview:
    raise NotImplementedByWorkstream(WS)


@router.get("/attention", response_model=AttentionOut)
def attention(
    request: Request,
    limit: int = Query(200, ge=1, le=1000),
    f: CommonFilters = Depends(common_filters),
    conn: sqlite3.Connection = Depends(read_conn),
) -> AttentionOut:
    raise NotImplementedByWorkstream(WS)


@router.get("/tickets/volumes", response_model=VolumesOut)
def ticket_volumes(
    request: Request,
    granularity: Literal["week", "month"] = "week",
    n: int = Query(12, ge=1, le=60),
    kind: str = "incident",
    f: CommonFilters = Depends(common_filters),
    conn: sqlite3.Connection = Depends(read_conn),
) -> VolumesOut:
    raise NotImplementedByWorkstream(WS)


@router.get("/tickets/sla", response_model=SlaOut)
def ticket_sla(
    request: Request,
    granularity: Literal["week", "month"] = "week",
    n: int = Query(12, ge=1, le=60),
    f: CommonFilters = Depends(common_filters),
    conn: sqlite3.Connection = Depends(read_conn),
) -> SlaOut:
    raise NotImplementedByWorkstream(WS)


@router.get("/tickets/mttr", response_model=MttrOut)
def ticket_mttr(
    request: Request,
    granularity: Literal["week", "month"] = "week",
    n: int = Query(12, ge=1, le=60),
    f: CommonFilters = Depends(common_filters),
    conn: sqlite3.Connection = Depends(read_conn),
) -> MttrOut:
    raise NotImplementedByWorkstream(WS)


@router.get("/tickets/backlog", response_model=BacklogOut)
def ticket_backlog(
    request: Request, f: CommonFilters = Depends(common_filters), conn: sqlite3.Connection = Depends(read_conn)
) -> BacklogOut:
    raise NotImplementedByWorkstream(WS)


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
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    f: CommonFilters = Depends(common_filters),
    conn: sqlite3.Connection = Depends(read_conn),
) -> TicketPage:
    raise NotImplementedByWorkstream(WS)


@router.get("/tickets/{ticket_id}", response_model=TicketDetail)
def ticket_detail(ticket_id: str, conn: sqlite3.Connection = Depends(read_conn)) -> TicketDetail:
    raise NotImplementedByWorkstream(WS)


@router.get("/apps", response_model=AppsOut)
def apps(
    request: Request, f: CommonFilters = Depends(common_filters), conn: sqlite3.Connection = Depends(read_conn)
) -> AppsOut:
    raise NotImplementedByWorkstream(WS)


@router.get("/apps/{app_id}", response_model=App360Out)
def app_360(
    app_id: str,
    request: Request,
    f: CommonFilters = Depends(common_filters),
    conn: sqlite3.Connection = Depends(read_conn),
) -> App360Out:
    raise NotImplementedByWorkstream(WS)


@router.get("/costs", response_model=CostsOut)
def costs(
    request: Request,
    group_by: Literal["app", "vendor", "category", "app_category"] = "app",
    months: int = Query(3, ge=1, le=24),
    f: CommonFilters = Depends(common_filters),
    conn: sqlite3.Connection = Depends(read_conn),
) -> CostsOut:
    raise NotImplementedByWorkstream(WS)


@router.get("/contracts/renewals", response_model=RenewalsOut)
def renewals(
    request: Request,
    days: int = Query(180, ge=1, le=1095),
    f: CommonFilters = Depends(common_filters),
    conn: sqlite3.Connection = Depends(read_conn),
) -> RenewalsOut:
    raise NotImplementedByWorkstream(WS)


@router.get("/licenses/utilization", response_model=LicensesOut)
def licenses(
    request: Request, f: CommonFilters = Depends(common_filters), conn: sqlite3.Connection = Depends(read_conn)
) -> LicensesOut:
    raise NotImplementedByWorkstream(WS)


@router.get("/vendors/sla-trend", response_model=VendorTrendsOut)
def vendor_sla_trend(
    request: Request,
    months: int = Query(6, ge=2, le=24),
    f: CommonFilters = Depends(common_filters),
    conn: sqlite3.Connection = Depends(read_conn),
) -> VendorTrendsOut:
    raise NotImplementedByWorkstream(WS)
