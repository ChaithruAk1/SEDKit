"""ASGI middleware: per-launch token on unsafe methods, and security headers on every response."""

from __future__ import annotations

import hmac
import json
from typing import Any

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
TOKEN_HEADER = b"x-sed-token"
SECURITY_HEADERS = [
    (b"x-frame-options", b"DENY"),
    (b"content-security-policy", b"frame-ancestors 'none'"),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
]


class TokenMiddleware:
    """Unsafe methods under /api require the X-SED-Token header (constant-time comparison)."""

    def __init__(self, app: Any, token: str) -> None:
        self.app = app
        self._token = token.encode("utf-8")

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] == "http" and scope["method"] not in SAFE_METHODS and scope["path"].startswith("/api"):
            supplied = dict(scope.get("headers") or []).get(TOKEN_HEADER, b"")
            if not hmac.compare_digest(supplied, self._token):
                body = json.dumps(
                    {
                        "ok": False,
                        "error": {"kind": "forbidden", "message": "Missing or invalid X-SED-Token", "details": None},
                    }
                ).encode("utf-8")
                await send(
                    {
                        "type": "http.response.start",
                        "status": 403,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode()),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)


class SecurityHeadersMiddleware:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: dict) -> None:
            if message["type"] == "http.response.start":
                existing = {k.lower() for k, _ in message.get("headers", [])}
                message["headers"] = list(message.get("headers", [])) + [
                    (k, v) for k, v in SECURITY_HEADERS if k not in existing
                ]
            await send(message)

        await self.app(scope, receive, send_with_headers)
