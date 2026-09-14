"""ASGI middleware: per-launch token on unsafe methods, and security headers on every response.

The token is compared in constant time and never logged, echoed or put in a URL. Responses carry anti-framing,
no-sniff and no-referrer headers; API responses are additionally `Cache-Control: no-store`.
"""

from __future__ import annotations

import hmac
import json
import re
from typing import Any

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
TOKEN_HEADER = b"x-sed-token"
# The token is injected into index.html as an attribute value, so it must be HTML-attribute safe.
TOKEN_RE = re.compile(r"^[A-Za-z0-9._~-]{1,512}$")
SECURITY_HEADERS = [
    (b"x-frame-options", b"DENY"),
    (b"content-security-policy", b"frame-ancestors 'none'"),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
]
API_HEADERS = [(b"cache-control", b"no-store")]


def validate_token(token: str) -> str:
    """Reject empty or unsafe tokens (an empty token would make a missing header match)."""
    if not isinstance(token, str) or not TOKEN_RE.match(token):
        raise ValueError("The API token must be 1-512 characters from [A-Za-z0-9._~-]")
    return token


def forbidden_body() -> bytes:
    return json.dumps(
        {"ok": False, "error": {"kind": "forbidden", "message": "Missing or invalid X-SED-Token", "details": None}}
    ).encode("utf-8")


class TokenMiddleware:
    """Unsafe methods (anything but GET/HEAD/OPTIONS) require the X-SED-Token header (constant-time comparison).

    The check covers every path, so module routers mounted at /api/<key> are protected like the core routes.
    """

    def __init__(self, app: Any, token: str) -> None:
        self.app = app
        self._token = validate_token(token).encode("utf-8")

    def _authorised(self, scope: dict) -> bool:
        supplied = [value for name, value in scope.get("headers") or [] if name.lower() == TOKEN_HEADER]
        # Exactly one header; a repeated header is refused rather than guessing which value counts.
        return len(supplied) == 1 and hmac.compare_digest(supplied[0], self._token)

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] == "http" and scope["method"] not in SAFE_METHODS and not self._authorised(scope):
            body = forbidden_body()
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
    """Adds SECURITY_HEADERS to every HTTP response (and no-store to /api responses) unless already set."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        extra = SECURITY_HEADERS + (API_HEADERS if scope.get("path", "").startswith("/api") else [])

        async def send_with_headers(message: dict) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                existing = {k.lower() for k, _ in headers}
                message["headers"] = headers + [(k, v) for k, v in extra if k not in existing]
            await send(message)

        await self.app(scope, receive, send_with_headers)
