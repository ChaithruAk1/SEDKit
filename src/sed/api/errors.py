"""One error envelope for every failure: {ok: false, error: {kind, message, details}}. Never leaks tracebacks."""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from sed.api.security import SECURITY_HEADERS
from sed.errors import Busy, NotImplementedByWorkstream, PreconditionFailed, SedError, ValidationFailed

log = logging.getLogger("sed.api")
RETRY_AFTER = {"Retry-After": "2"}
ERROR_STATUSES = {
    400: "bad_request",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
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


def is_locked_error(exc: BaseException) -> bool:
    """SQLite lock contention that escaped db.write_tx (e.g. a reader during a checkpoint): retryable, not a bug."""
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    message = str(exc).lower()
    return "database is locked" in message or "database is busy" in message or "database table is locked" in message


async def _sed_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, SedError)
    status = status_for(exc)
    headers = RETRY_AFTER if status == 409 else None
    return envelope(status, exc.kind, exc.message, exc.details, headers)


async def _validation_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    # Only the location and message: the rejected input value is never echoed back.
    details = [{"loc": ".".join(str(p) for p in e.get("loc", ())), "msg": e.get("msg", "")} for e in exc.errors()]
    return envelope(422, "validation", "Request validation failed", details)


async def _http_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    kind = ERROR_STATUSES.get(exc.status_code, "error")
    message = str(exc.detail) if exc.detail else kind.replace("_", " ")
    headers = dict(exc.headers) if exc.headers else None
    if exc.status_code == 409:
        headers = {**(headers or {}), **RETRY_AFTER}
    return envelope(exc.status_code, kind, message, headers=headers)


async def _sqlite_error(request: Request, exc: Exception) -> JSONResponse:
    if is_locked_error(exc):
        return envelope(409, "busy", "Database is busy; retry shortly.", headers=RETRY_AFTER)
    return await _internal_error(request, exc)


async def _internal_error(request: Request, exc: Exception) -> JSONResponse:
    # Log the exception type only: messages can carry row data, and request headers (the token) are never logged.
    log.error("internal error on %s %s: %s", request.method, request.url.path, type(exc).__name__)
    # This response is sent by the outermost ServerErrorMiddleware, outside SecurityHeadersMiddleware.
    headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in SECURITY_HEADERS}
    return envelope(500, "internal", f"Internal error ({type(exc).__name__})", headers=headers)


def install(app: FastAPI) -> None:
    app.add_exception_handler(SedError, _sed_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(sqlite3.OperationalError, _sqlite_error)
    app.add_exception_handler(Exception, _internal_error)
