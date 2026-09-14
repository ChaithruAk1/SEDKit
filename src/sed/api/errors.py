"""One error envelope for every failure: {ok: false, error: {kind, message, details}}. Never leaks tracebacks."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from sed.errors import Busy, NotImplementedByWorkstream, PreconditionFailed, SedError, ValidationFailed

log = logging.getLogger("sed.api")
ERROR_STATUSES = {
    403: "forbidden",
    404: "not_found",
    409: "busy",
    412: "precondition",
    422: "validation",
    501: "not_implemented",
}


def envelope(
    status: int, kind: str, message: str, details: Any = None, headers: dict[str, str] | None = None
) -> JSONResponse:
    body: dict[str, Any] = {"ok": False, "error": {"kind": kind, "message": message, "details": details}}
    return JSONResponse(body, status_code=status, headers=headers)


def status_for(exc: SedError) -> int:
    if isinstance(exc, NotImplementedByWorkstream):
        return 501
    if isinstance(exc, Busy):
        return 409
    if isinstance(exc, PreconditionFailed):
        return 412
    if isinstance(exc, ValidationFailed):
        return 422
    return 500


async def _sed_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, SedError)
    status = status_for(exc)
    headers = {"Retry-After": "2"} if status == 409 else None
    return envelope(status, exc.kind, exc.message, exc.details, headers)


async def _validation_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    details = [{"loc": ".".join(str(p) for p in e.get("loc", ())), "msg": e.get("msg", "")} for e in exc.errors()]
    return envelope(422, "validation", "Request validation failed", details)


async def _http_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    kind = ERROR_STATUSES.get(exc.status_code, "error")
    return envelope(exc.status_code, kind, str(exc.detail) if exc.detail else kind.replace("_", " "))


async def _internal_error(request: Request, exc: Exception) -> JSONResponse:
    log.error("internal error on %s %s: %s", request.method, request.url.path, type(exc).__name__)
    return envelope(500, "internal", f"Internal error ({type(exc).__name__})")


def install(app: FastAPI) -> None:
    app.add_exception_handler(SedError, _sed_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(Exception, _internal_error)
