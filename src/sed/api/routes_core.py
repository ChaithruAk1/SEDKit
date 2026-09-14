"""Core routes mounted at /api. Signatures are final for M2; bodies belong to ws4-api-platform (health works now)."""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request

from sed import __version__
from sed.api.deps import read_conn, write_conn
from sed.api.models import (
    AliasIn,
    AliasOut,
    AliasTargetsOut,
    FindingsOut,
    HealthOut,
    ImportsOut,
    MetaOut,
    ModulesOut,
    NavOut,
    RunsOut,
    UnmappedList,
)
from sed.errors import NotImplementedByWorkstream

WS = "ws4-api-platform"
router = APIRouter(tags=["core"])


@router.get("/health", response_model=HealthOut)
def health() -> HealthOut:
    return HealthOut(ok=True, version=__version__)


@router.get("/meta", response_model=MetaOut)
def meta(request: Request, conn: sqlite3.Connection = Depends(read_conn)) -> MetaOut:
    raise NotImplementedByWorkstream(WS)


@router.get("/nav", response_model=NavOut)
def nav(request: Request) -> NavOut:
    raise NotImplementedByWorkstream(WS)


@router.get("/modules", response_model=ModulesOut)
def modules_list(request: Request) -> ModulesOut:
    raise NotImplementedByWorkstream(WS)


@router.get("/findings", response_model=FindingsOut)
def findings(
    request: Request,
    kind: str | None = None,
    origin: Literal["rule", "ai"] | None = None,
    status: Literal["published", "all"] = "published",
    subject_type: str | None = None,
    subject_id: str | None = None,
    as_of: date | None = None,
    limit: int = Query(100, ge=1, le=1000),
    conn: sqlite3.Connection = Depends(read_conn),
) -> FindingsOut:
    raise NotImplementedByWorkstream(WS)


@router.get("/imports", response_model=ImportsOut)
def imports(limit: int = Query(50, ge=1, le=1000), conn: sqlite3.Connection = Depends(read_conn)) -> ImportsOut:
    raise NotImplementedByWorkstream(WS)


@router.get("/dq/unmapped", response_model=UnmappedList)
def dq_unmapped(
    kind: str | None = None,
    limit: int = Query(500, ge=1, le=5000),
    conn: sqlite3.Connection = Depends(read_conn),
) -> UnmappedList:
    raise NotImplementedByWorkstream(WS)


@router.get("/alias-targets", response_model=AliasTargetsOut)
def alias_targets(
    kind: str,
    q: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    conn: sqlite3.Connection = Depends(read_conn),
) -> AliasTargetsOut:
    raise NotImplementedByWorkstream(WS)


@router.post("/aliases", response_model=AliasOut)
def create_alias(body: AliasIn, request: Request, conn: sqlite3.Connection = Depends(write_conn)) -> AliasOut:
    raise NotImplementedByWorkstream(WS)


@router.get("/runs", response_model=RunsOut)
def runs(
    skill: str | None = None,
    status: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    conn: sqlite3.Connection = Depends(read_conn),
) -> RunsOut:
    raise NotImplementedByWorkstream(WS)
