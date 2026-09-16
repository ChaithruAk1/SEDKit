"""SAP API routes mounted at /api/sap. Every route reads through `deps.read_conn` (query_only) and never writes."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Query, Request

from sed.api.deps import CommonFilters, common_filters, read_conn
from sed.modules.ops.queries.common import build_context
from sed.modules.sap.api_models import SapChangesOut, SapL3Out, SapOverview
from sed.modules.sap.queries import api_views

router = APIRouter()


@router.get("/overview", response_model=SapOverview)
def overview(
    request: Request, f: CommonFilters = Depends(common_filters), conn: sqlite3.Connection = Depends(read_conn)
) -> SapOverview:
    return api_views.overview(build_context(conn, request.app.state.paths, f))


@router.get("/l3", response_model=SapL3Out)
def l3(
    request: Request,
    area: str | None = Query(None, max_length=32, description="SAP area code, or 'unassigned'"),
    landscape: str | None = Query(None, max_length=32, description="Landscape code, or 'unknown'"),
    weeks: int = Query(12, ge=4, le=52),
    f: CommonFilters = Depends(common_filters),
    conn: sqlite3.Connection = Depends(read_conn),
) -> SapL3Out:
    return api_views.l3_view(build_context(conn, request.app.state.paths, f), area, landscape, weeks)


@router.get("/changes", response_model=SapChangesOut)
def changes(
    request: Request,
    area: str | None = Query(None, max_length=32, description="SAP area code, or 'unassigned'"),
    landscape: str | None = Query(None, max_length=32, description="Landscape code, or 'unknown'"),
    weeks: int = Query(12, ge=8, le=52),
    f: CommonFilters = Depends(common_filters),
    conn: sqlite3.Connection = Depends(read_conn),
) -> SapChangesOut:
    return api_views.changes_view(build_context(conn, request.app.state.paths, f), area, landscape, weeks)
