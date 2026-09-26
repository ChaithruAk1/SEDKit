"""Audit trail routes mounted at /api (docs/audit.md). The reading lives in `sed.audit.query`.

- `GET /audit`: entries newest first, filtered by person, action, outcome, days, words or one action's id, with the
  people and actions to filter by and the chain check. Reading the trail is not recorded (actions only).
- `GET /audit-export.xlsx`: the same filters as a workbook (not in the API contract, like the ticket workbook). The
  download is itself recorded first, and refused when it cannot be.
"""

from __future__ import annotations

from datetime import date
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse

from sed.api.deps import actor
from sed.api.models import AuditPageOut
from sed.audit.query import AuditFilters
from sed.errors import ValidationFailed

router = APIRouter()

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def audit_filters(
    person: str | None = Query(None, max_length=320, description="One person: an address or windows:<account>"),
    action: str | None = Query(None, max_length=40, description="An action key, e.g. download"),
    outcome: Literal["started", "done", "failed", "refused"] | None = Query(None),
    since: date | None = Query(None, description="First day, YYYY-MM-DD (reporting time zone)"),
    until: date | None = Query(None, description="Last day, YYYY-MM-DD (reporting time zone)"),
    q: str | None = Query(None, max_length=200, description="Words in what happened, who or the target"),
    correlation: str | None = Query(None, max_length=64, description="All entries of one action"),
) -> AuditFilters:
    from sed.audit.record import ACTIONS

    if action and action not in ACTIONS:
        raise ValidationFailed(f"Unknown action '{action}'", {"actions": sorted(ACTIONS)})
    if since and until and since > until:
        raise ValidationFailed("The first day comes after the last day")
    return AuditFilters(person, action, outcome, since, until, q, correlation)


def _tz(request: Request) -> ZoneInfo:
    from sed.settings import load_settings

    return ZoneInfo(load_settings(request.app.state.paths).reporting_tz)


@router.get("/audit", response_model=AuditPageOut)
def audit(
    request: Request,
    filters: AuditFilters = Depends(audit_filters),
    page: int = Query(1, ge=1, le=100_000),
    page_size: int = Query(100, ge=1, le=500),
) -> AuditPageOut:
    # The audit trail, newest first, with who is in it, the actions and whether the chain of entries is intact.
    from sed.audit.query import read_page

    body = read_page(request.app.state.paths, filters, tz=_tz(request), page=page, page_size=page_size)
    return AuditPageOut.model_validate(body)


@router.get("/audit-export.xlsx", include_in_schema=False)
def audit_export(request: Request, filters: AuditFilters = Depends(audit_filters)) -> FileResponse:
    # The filtered trail as a workbook. On the trail before it leaves: when that cannot be written, it is refused.
    from sed.audit.query import write_workbook
    from sed.audit.record import AuditUnavailable, file_sha256, record

    paths = request.app.state.paths
    path, count = write_workbook(paths, filters, tz=_tz(request))
    chosen = {k: v for k, v in vars(filters).items() if v}
    detail = {
        "rows": count,
        "filters": {k: v.isoformat() if isinstance(v, date) else v for k, v in chosen.items()},
        "sha256": file_sha256(path),
    }
    try:
        summary = f"Downloaded {count} audit {'entry' if count == 1 else 'entries'} as a workbook."
        record(
            paths,
            actor(request),
            "download",
            summary=summary,
            target_type="audit_log",
            target_id=path.name,
            detail=detail,
        )
    except AuditUnavailable:
        path.unlink(missing_ok=True)
        raise
    return FileResponse(path, media_type=XLSX, filename=path.name, headers={"Cache-Control": "no-store"})
