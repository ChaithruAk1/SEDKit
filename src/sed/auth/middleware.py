"""ASGI middleware: every request knows who is asking, or is refused with 401.

The signed-in person (or, in developer mode, the Windows account) is put on `request.state.actor`. Deny by default:
only the dashboard shell (the page, its assets and top-level files such as the icon, which hold no data), the pages a
provider sends the browser back to, and the few API routes the sign-in screen needs (health, branding for its logo and
title, /api/auth/*) are open. Every other path, including any under /api and any oddly spelled one, needs a session.
"""

from __future__ import annotations

import json
import re
from typing import Any

from starlette.requests import cookie_parser

from sed.auth.runtime import AuthRuntime, origin_port

PUBLIC_API = ("/api/health", "/api/branding", "/api/auth/")
SHELL_PATHS = ("/", "/index.html", "/auth/callback", "/auth/finish")
TOP_LEVEL_FILE_RE = re.compile(r"^/[^/]+$")
LOOPBACK_HOST_RE = re.compile(r"^(127\.0\.0\.1|localhost)(?::(\d{1,5}))?$", re.IGNORECASE)


def is_public(path: str) -> bool:
    return any(path == p or path.startswith(p if p.endswith("/") else p + "/") for p in PUBLIC_API)


def needs_session(path: str) -> bool:
    if is_public(path) or path in SHELL_PATHS or path.startswith("/assets/"):
        return False
    return not (TOP_LEVEL_FILE_RE.match(path) and not path.lower().startswith("/api"))


def header(scope: dict, name: bytes) -> str | None:
    for key, value in scope.get("headers") or []:
        if key.lower() == name:
            return value.decode("latin-1")
    return None


def request_origin(scope: dict) -> str:
    """http://host:port for this request: the loopback name the browser used (the trusted-host check lets only
    127.0.0.1 and localhost through), and the port of the socket SED actually listens on. The port never comes from
    the Host header, which the caller controls; anything odd becomes http://127.0.0.1."""
    match = LOOPBACK_HOST_RE.match((header(scope, b"host") or "").strip())
    host = match.group(1).lower() if match else "127.0.0.1"
    server = scope.get("server")
    if server and len(server) > 1 and isinstance(server[1], int):
        port = server[1]
    else:
        port = int(match.group(2)) if match and match.group(2) else 80
    return f"http://{host}:{port}"


def read_cookie(scope: dict, name: str) -> str | None:
    """One cookie by name. Parsed leniently (as Starlette does): a malformed cookie set by some other program on this
    host must never hide SED's own."""
    found = None
    for key, value in scope.get("headers") or []:
        if key.lower() == b"cookie":
            found = cookie_parser(value.decode("latin-1")).get(name, found)
    return found or None


def session_cookie(scope: dict, runtime: AuthRuntime) -> str | None:
    return read_cookie(scope, runtime.cookie_name(origin_port(request_origin(scope))))


def binding_cookie(scope: dict, runtime: AuthRuntime) -> str | None:
    return read_cookie(scope, runtime.binding_cookie_name(origin_port(request_origin(scope))))


def unauthenticated_body() -> bytes:
    return json.dumps(
        {"ok": False, "error": {"kind": "unauthenticated", "message": "Please sign in to SED.", "details": None}}
    ).encode("utf-8")


class SessionMiddleware:
    def __init__(self, app: Any, runtime: AuthRuntime) -> None:
        self.app = app
        self.runtime = runtime

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        actor = self.runtime.actor_for(session_cookie(scope, self.runtime))
        scope.setdefault("state", {})["actor"] = actor
        if actor is None and needs_session(scope.get("path", "")):
            body = unauthenticated_body()
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)
