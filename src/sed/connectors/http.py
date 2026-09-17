"""Read-only HTTP for connectors: GET only, JSON responses (and capped file downloads for document libraries), retries
with backoff, a pause between pages, row caps.

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


@dataclass(frozen=True)
class BytesResponse:
    status: int
    headers: dict[str, str]
    content: bytes


class Transport(Protocol):
    def get(self, url: str, params: dict[str, Any], headers: dict[str, str]) -> Response: ...

    def get_bytes(self, url: str, headers: dict[str, str], max_bytes: int) -> BytesResponse: ...


def _verify() -> Any:
    try:
        import ssl

        import truststore

        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:
        return True


def _too_large(url: str, max_bytes: int) -> PreconditionFailed:
    return PreconditionFailed(f"{_safe(url)} is larger than {max_bytes // (1024 * 1024)} MB; stopped")


class HttpTransport:
    def __init__(self, timeout: float = 60.0) -> None:
        self.timeout = timeout

    def get(self, url: str, params: dict[str, Any], headers: dict[str, str]) -> Response:
        headers = {"Accept": "application/json", "User-Agent": USER_AGENT, **headers}
        try:
            import httpx
        except ImportError:
            return self._urllib(url, params, headers)
        response = httpx.get(url, params=params, headers=headers, timeout=self.timeout, verify=_verify())
        return Response(response.status_code, dict(response.headers), _json(response.content, url))

    def get_bytes(self, url: str, headers: dict[str, str], max_bytes: int) -> BytesResponse:
        """A file download (no redirects followed), read in chunks and stopped past max_bytes."""
        headers = {"User-Agent": USER_AGENT, **headers}
        try:
            import httpx
        except ImportError:
            return self._urllib_bytes(url, headers, max_bytes)
        with httpx.stream("GET", url, headers=headers, timeout=self.timeout, verify=_verify()) as response:
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > max_bytes:
                    raise _too_large(url, max_bytes)
                chunks.append(chunk)
            return BytesResponse(response.status_code, dict(response.headers), b"".join(chunks))

    def _urllib_bytes(self, url: str, headers: dict[str, str], max_bytes: int) -> BytesResponse:
        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = response.read(max_bytes + 1)
                if len(data) > max_bytes:
                    raise _too_large(url, max_bytes)
                return BytesResponse(response.status, dict(response.headers), data)
        except urllib.error.HTTPError as exc:
            return BytesResponse(exc.code, dict(exc.headers or {}), b"")

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

    def get_bytes(self, url: str, headers: dict[str, str], max_bytes: int) -> BytesResponse:
        """Recorded downloads: `{"download": "<full url>", "status": 200, "text": "..."}` (UTF-8 content)."""
        self.calls.append((url, {}))
        self.headers_seen.append(dict(headers))
        for recorded in self.requests:
            if recorded.get("download") == url:
                content = str(recorded.get("text", "")).encode("utf-8")
                if len(content) > max_bytes:
                    raise _too_large(url, max_bytes)
                return BytesResponse(int(recorded.get("status", 200)), {}, content)
        raise ValidationFailed(f"No recorded download for {_safe(url)}")


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

    def download(self, url: str, allowed_hosts: list[str], max_bytes: int) -> bytes:
        """GET a pre-authenticated download link (Graph `@microsoft.graph.downloadUrl`) WITHOUT the connector's
        credentials: https only, and only to a host ending in one of `allowed_hosts` (e.g. `.sharepoint.com`)."""
        parts = urllib.parse.urlsplit(url)
        host = (parts.hostname or "").lower()
        if parts.scheme != "https" or not any(
            host == h.lower().lstrip(".") or host.endswith("." + h.lower().lstrip(".")) for h in allowed_hosts
        ):
            raise PreconditionFailed(f"Download link to '{host or url[:40]}' is not an allowed https host; stopped")
        delay = 2.0
        for attempt in range(self.retries + 1):
            if self.pause_seconds:
                self.sleep(self.pause_seconds)
            response = self.transport.get_bytes(url, {}, max_bytes)
            if response.status == 200:
                return response.content
            if response.status in RETRY_STATUS and attempt < self.retries:
                self.sleep(delay)
                delay *= 2
                continue
            raise PreconditionFailed(f"{_safe(url)} returned HTTP {response.status}")
        raise PreconditionFailed(f"{_safe(url)} kept failing after {self.retries} retries")
