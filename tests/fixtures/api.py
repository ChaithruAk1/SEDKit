"""API test client helper (import explicitly: `from tests.fixtures.api import api_client, TEST_TOKEN`)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

TEST_TOKEN = "test-token"


def api_client(
    paths: Any, *, token: str = TEST_TOKEN, modules: Any = None, web_dist: Path | None = None, send_token: bool = True
) -> TestClient:
    """TestClient on http://127.0.0.1 (an allowed host). With send_token, unsafe requests carry X-SED-Token."""
    from sed.api.app import create_app

    app = create_app(paths, token=token, modules=modules, web_dist=web_dist)
    headers = {"X-SED-Token": token} if send_token else {}
    return TestClient(app, base_url="http://127.0.0.1", headers=headers, raise_server_exceptions=False)
