"""Shared helpers for the core API tests (tests/platform/api)."""

from __future__ import annotations

import socket
from typing import Any

import pytest

from sed.api import routes_core, routes_review

SECURITY_HEADERS = {
    "x-frame-options": "DENY",
    "content-security-policy": "frame-ancestors 'none'",
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
}
# Query strings needed by core GET routes that have required parameters.
REQUIRED_QUERY = {"/alias-targets": "?kind=vendor"}


def core_get_routes() -> list[tuple[str, Any]]:
    """(/api path with required query, response model) for every GET route of the core routers without path
    parameters (routes like /runs/{run_id} are tested with a real run)."""
    out = []
    for route in [*routes_core.router.routes, *routes_review.router.routes]:
        if "GET" in getattr(route, "methods", set()) and "{" not in route.path:
            out.append((f"/api{route.path}{REQUIRED_QUERY.get(route.path, '')}", route.response_model))
    return sorted(out, key=lambda item: item[0])


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def assert_envelope(response: Any, status: int, kind: str) -> dict[str, Any]:
    assert response.status_code == status, (response.status_code, response.text[:500])
    body = response.json()
    assert body["ok"] is False and body["error"]["kind"] == kind, body
    assert set(body) == {"ok", "error"} and set(body["error"]) == {"kind", "message", "details"}
    assert "Traceback" not in response.text
    return body


@pytest.fixture
def core_gets() -> list[tuple[str, Any]]:
    return core_get_routes()
