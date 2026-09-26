"""HTTPS calls to the identity providers: form POSTs and JSON GETs, nothing else.

Signing in is the one deliberate exception to "nothing leaves the laptop", and it reaches only the provider.
Certificates are checked against the Windows certificate store (truststore when installed, else the standard library's
default context, which also loads it), so TLS inspection by a company proxy works as it does for the browser. Tests use
a fake transport; CI makes no network calls.
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Protocol

from sed.auth.oidc import SignInFailedError

MAX_BYTES = 1_000_000
BASE_HEADERS = {"Accept": "application/json", "User-Agent": "SED sign-in"}


class AuthTransport(Protocol):
    def post_form(self, url: str, form: dict[str, str], headers: dict[str, str] | None = None) -> tuple[int, Any]: ...

    def get_json(self, url: str, headers: dict[str, str] | None = None) -> tuple[int, Any]: ...


def _ssl_context() -> ssl.SSLContext:
    try:
        import truststore

        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:
        return ssl.create_default_context()


def _json(data: bytes) -> Any:
    try:
        return json.loads(data.decode("utf-8")) if data else None
    except (UnicodeDecodeError, ValueError):
        return None


class HttpsTransport:
    def __init__(self, timeout: float = 20.0) -> None:
        self.timeout = timeout

    def post_form(self, url: str, form: dict[str, str], headers: dict[str, str] | None = None) -> tuple[int, Any]:
        body = urllib.parse.urlencode(form).encode("ascii")
        all_headers = {**BASE_HEADERS, "Content-Type": "application/x-www-form-urlencoded", **(headers or {})}
        return self._send(urllib.request.Request(url, data=body, headers=all_headers, method="POST"))

    def get_json(self, url: str, headers: dict[str, str] | None = None) -> tuple[int, Any]:
        return self._send(urllib.request.Request(url, headers={**BASE_HEADERS, **(headers or {})}, method="GET"))

    def _send(self, request: urllib.request.Request) -> tuple[int, Any]:
        host = urllib.parse.urlsplit(request.full_url).hostname or "the sign-in provider"
        if not request.full_url.startswith("https://"):
            raise SignInFailedError(f"Refusing to contact {host} without https.")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout, context=_ssl_context()) as response:
                return response.status, _json(response.read(MAX_BYTES))
        except urllib.error.HTTPError as exc:
            return exc.code, _json(exc.read(MAX_BYTES) if exc.fp else b"")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise SignInFailedError(f"Could not reach {host}. Check the network connection and try again.") from exc
