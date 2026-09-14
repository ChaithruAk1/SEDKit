"""Shared fixtures for the ops API tests: a client on the read-only session ops_profile and ids read from the API."""

from __future__ import annotations

from typing import Any

import pytest

from tests.fixtures.api import api_client


@pytest.fixture(scope="module")
def client(ops_profile: Any):
    return api_client(ops_profile.paths)


@pytest.fixture(scope="module")
def ids(client: Any) -> dict[str, Any]:
    """Ids of real rows in the fixture (first app, newest ticket, one family, the P2 vendor)."""
    filters = client.get("/api/ops/filters").json()
    newest = client.get("/api/ops/tickets", params={"page_size": 1}).json()["items"][0]
    return {
        "app_id": filters["apps"][0]["app_id"],
        "family": filters["families"][0],
        "ticket_id": newest["ticket_id"],
    }


@pytest.fixture
def ro_conn(ops_profile: Any):
    """A query_only connection on the shared session profile."""
    from sed import db

    conn = db.connect(ops_profile.paths.db, readonly=True)
    try:
        yield conn
    finally:
        conn.close()
