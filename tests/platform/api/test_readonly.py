"""GET routes never write: every request uses a query_only connection, persisted rule-finding state is untouched even
for a future as_of, and a write attempted inside a GET fails as a 500 envelope without a traceback."""

from __future__ import annotations

import pytest

from sed import db
from tests.fixtures.api import api_client
from tests.fixtures.ops_profile import state_digest
from tests.platform.api.conftest import SECURITY_HEADERS, assert_envelope


def _state(paths):
    conn = db.connect(paths.db, readonly=True)
    try:
        return state_digest(conn), db.get_meta(conn, "ops.rule_findings_as_of")
    finally:
        conn.close()


@pytest.fixture
def connections(monkeypatch):
    """Record (readonly flag, query_only pragma) for every connection opened while the fixture is active."""
    opened: list[tuple[bool, int]] = []
    real_connect = db.connect

    def recording_connect(path, *, readonly=False):
        conn = real_connect(path, readonly=readonly)
        opened.append((readonly, conn.execute("PRAGMA query_only").fetchone()[0]))
        return conn

    monkeypatch.setattr(db, "connect", recording_connect)
    return opened


def test_every_get_uses_query_only_connections(ops_profile, core_gets, connections):
    paths = ops_profile.paths
    before = _state(paths)
    client = api_client(paths, send_token=False)
    queries = [path for path, _ in core_gets] + [
        "/api/findings?as_of=2027-12-31&status=all",
        "/api/findings?as_of=2026-12-31",
        "/api/meta?as_of=2030-01-01",
        "/api/dq/unmapped?kind=vendor",
        "/api/alias-targets?kind=app&q=a",
        "/api/runs?status=approved",
    ]
    for path in queries:
        connections.clear()
        response = client.get(path)
        assert response.status_code == 200, (path, response.text[:300])
        assert all(flag == (True, 1) for flag in connections), (path, connections)
        if not path.startswith(("/api/health", "/api/nav", "/api/modules")):
            assert connections, f"{path} should read through deps.read_conn"
    assert _state(paths) == before  # includes meta.ops.rule_findings_as_of: no refresh from a GET


def test_a_write_inside_a_get_is_a_500_envelope_without_traceback(ops_profile, monkeypatch):
    paths = ops_profile.paths
    before = _state(paths)

    def writing_freshness(conn):
        conn.execute("INSERT INTO meta (key, value, updated_at) VALUES ('probe', 'x', '2026-09-01T00:00:00Z')")
        return []

    monkeypatch.setattr("sed.api.routes_core.import_freshness", writing_freshness)
    response = api_client(paths).get("/api/meta")
    body = assert_envelope(response, 500, "internal")
    assert body["error"]["message"] == "Internal error (OperationalError)"
    for text in ("Traceback", "INSERT", "readonly", 'File "'):
        assert text not in response.text
    for name, value in SECURITY_HEADERS.items():
        assert response.headers[name] == value
    assert _state(paths) == before


def test_a_crash_inside_a_get_is_a_500_envelope(ops_profile, monkeypatch):
    def crash(*args, **kwargs):
        raise KeyError("ticket text")

    monkeypatch.setattr("sed.api.routes_core.published_findings", crash)
    response = api_client(ops_profile.paths).get("/api/findings")
    body = assert_envelope(response, 500, "internal")
    assert body["error"]["details"] is None and "ticket text" not in response.text


def test_lock_contention_on_a_get_is_a_retryable_409(ops_profile, monkeypatch):
    import sqlite3

    def locked(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr("sed.api.routes_core.published_findings", locked)
    response = api_client(ops_profile.paths).get("/api/findings")
    assert_envelope(response, 409, "busy")
    assert response.headers["retry-after"] == "2"
