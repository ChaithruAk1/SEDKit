"""Read-only HTTP for connectors: GET only, JSON responses, retries with backoff, a pause between pages, row caps.

`HttpTransport` uses httpx when it is installed (with truststore for the Windows certificate store behind TLS
inspection, when available) and the standard library otherwise, so a work laptop needs no extra packages.
`RecordedTransport` replays recorded synthetic responses in tests (no network in CI).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from sed.errors import PreconditionFailed, ValidationFailed

RETRY_STATUS = {429, 500, 502, 503, 504}
USER_AGENT = "sed-connector (read-only)"


@dataclass(frozen=True)
class Response:
    status: int
    headers: dict[str, str]
    body: Any  # parsed JSON


class Transport(Protocol):
    def get(self, url: str, params: dict[str, Any], headers: dict[str, str]) -> Response: ...


class HttpTransport:
    def __init__(self, timeout: float = 60.0) -> None:
        self.timeout = timeout

    def get(self, url: str, params: dict[str, Any], headers: dict[str, str]) -> Response:
        headers = {"Accept": "application/json", "User-Agent": USER_AGENT, **headers}
        try:
            import httpx
        except ImportError:
            return self._urllib(url, params, headers)
        verify: Any = True
        try:
            import ssl

            import truststore

            verify = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        except ImportError:
            pass
        response = httpx.get(url, params=params, headers=headers, timeout=self.timeout, verify=verify)
        return Response(response.status_code, dict(response.headers), _json(response.content, url))

    def _urllib(self, url: str, params: dict[str, Any], headers: dict[str, str]) -> Response:
        query = urllib.parse.urlencode(params)
        request = urllib.request.Request(f"{url}?{query}" if query else url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return Response(response.status, dict(response.headers), _json(response.read(), url))
        except urllib.error.HTTPError as exc:
            return Response(exc.code, dict(exc.headers or {}), None)


def _json(data: bytes, url: str) -> Any:
    if not data:
        return None
    try:
        return json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise PreconditionFailed(f"{_safe(url)} did not return JSON") from exc


def _safe(url: str) -> str:
    """A URL for messages: scheme, host and path only (never query strings, which may carry filters)."""
    parts = urllib.parse.urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


@dataclass
class RecordedTransport:
    """Replays responses recorded as JSON files: `{"requests": [{"path": ..., "params": {...}, "status": 200,
    "body": ...}]}`. A request matches on the URL path and on every recorded param."""

    requests: list[dict[str, Any]]
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    headers_seen: list[dict[str, str]] = field(default_factory=list)

    @classmethod
    def load(cls, *files: Path) -> RecordedTransport:
        requests: list[dict[str, Any]] = []
        for file in files:
            requests += json.loads(file.read_text(encoding="utf-8"))["requests"]
        return cls(requests)

    def get(self, url: str, params: dict[str, Any], headers: dict[str, str]) -> Response:
        path = urllib.parse.urlsplit(url).path
        self.calls.append((path, dict(params)))
        self.headers_seen.append(dict(headers))
        for recorded in self.requests:
            wanted = recorded.get("params") or {}
            if recorded.get("used") and not recorded.get("repeat"):
                continue
            if recorded["path"] == path and all(str(params.get(k)) == str(v) for k, v in wanted.items()):
                recorded["used"] = True
                return Response(
                    int(recorded.get("status", 200)), dict(recorded.get("headers") or {}), recorded.get("body")
                )
        raise ValidationFailed(f"No recorded response for GET {path} {json.dumps(params, sort_keys=True)}")


@dataclass
class Client:
    """GET with retries (429 and 5xx, honouring Retry-After), a pause between calls and a hard page limit."""

    transport: Transport
    base_url: str
    headers: dict[str, str]
    pause_seconds: float = 0.5
    retries: int = 4
    max_pages: int = 500
    sleep: Callable[[float], None] = time.sleep
    pages: int = 0

    def get(self, path: str, params: dict[str, Any]) -> Any:
        if not self.base_url.lower().startswith("https://"):
            raise ValidationFailed("Connector base_url must start with https://")
        if self.pages >= self.max_pages:
            raise PreconditionFailed(f"Stopped after {self.max_pages} pages (max_pages); narrow the pull or raise it")
        url = self.base_url.rstrip("/") + "/" + path.lstrip("/")
        delay = 2.0
        for attempt in range(self.retries + 1):
            if self.pages and self.pause_seconds:
                self.sleep(self.pause_seconds)
            response = self.transport.get(url, params, self.headers)
            self.pages += 1
            if response.status == 200:
                return response.body
            if response.status in (401, 403):
                raise PreconditionFailed(
                    f"{_safe(url)} refused the credentials (HTTP {response.status}); check the connector secret"
                )
            if response.status in RETRY_STATUS and attempt < self.retries:
                retry_after = response.headers.get("Retry-After") or response.headers.get("retry-after")
                wait = float(retry_after) if retry_after and retry_after.isdigit() else delay
                self.sleep(min(wait, 120.0))
                delay *= 2
                continue
            raise PreconditionFailed(f"{_safe(url)} returned HTTP {response.status}")
        raise PreconditionFailed(f"{_safe(url)} kept failing after {self.retries} retries")
