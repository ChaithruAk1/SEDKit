"""Sign-in over HTTP: /api needs a session, the callback pages, cookies, the host handoff, sign-out, developer mode."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from sed.api.app import create_app
from sed.paths import get_paths
from tests.fixtures.api import TEST_TOKEN
from tests.fixtures.auth import OWNER, query, sign_in_runtime
from tests.platform.api.conftest import SECURITY_HEADERS, assert_envelope

BASE = "http://127.0.0.1:8123"


@pytest.fixture
def signed_app(data_root):
    """(client on 127.0.0.1:8123, runtime, fake providers, clock, events) for a sign-in-required app."""
    events: list = []
    runtime, fake, clock = sign_in_runtime(events=events)
    app = create_app(get_paths("synthetic"), token=TEST_TOKEN, auth=runtime)
    client = TestClient(app, base_url=BASE, headers={"X-SED-Token": TEST_TOKEN}, raise_server_exceptions=False)
    return client, runtime, fake, clock, events


def _google_sign_in(client, fake, **claims):
    started = client.post("/api/auth/start", json={"provider": "google"})
    assert started.status_code == 200, started.text
    params = query(started.json()["authorize_url"])
    fake.google(params["nonce"], **claims)
    return client.get("/auth/callback", params={"state": params["state"], "code": "code-1"})


def test_api_needs_a_session(signed_app):
    client, *_ = signed_app
    response = client.get("/api/nav")
    body = assert_envelope(response, 401, "unauthenticated")
    assert body["error"]["message"] == "Please sign in to SED."
    for name, value in SECURITY_HEADERS.items():
        assert response.headers[name] == value
    for public in ("/api/health", "/api/branding", "/api/auth/session"):
        assert client.get(public).status_code == 200, public
    assert client.get("/api/openapi.json").status_code == 401
    assert client.get("/api/navx").status_code == 401  # unknown paths do not reveal what exists


def test_session_describes_the_sign_in_screen(signed_app):
    client, *_ = signed_app
    body = client.get("/api/auth/session").json()
    assert body["mode"] == "sign_in" and body["signed_in"] is False and body["user"] is None
    assert [(p["key"], p["flow"]) for p in body["providers"]] == [
        ("microsoft", "redirect"),
        ("google", "redirect"),
        ("github", "device"),
    ]
    assert body["setup_needed"] == [] and OWNER not in str(body)


def _set_cookies(response) -> dict[str, str]:
    return {line.split("=", 1)[0]: line for line in response.headers.get_list("set-cookie")}


def test_google_sign_in_sets_a_strict_http_only_cookie(signed_app):
    client, _, fake, _, events = signed_app
    started = client.post("/api/auth/start", json={"provider": "google"})
    binding = _set_cookies(started)["sed_signin_8123"]
    assert "HttpOnly" in binding and "SameSite=lax" in binding and "Path=/auth" in binding and "Max-Age=600" in binding
    params = query(started.json()["authorize_url"])
    fake.google(params["nonce"])
    response = client.get("/auth/callback", params={"state": params["state"], "code": "code-1"})
    assert response.status_code == 200 and 'http-equiv="refresh" content="0;url=/"' in response.text
    cookies = _set_cookies(response)
    cookie = cookies["sed_session_8123"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Max-Age" not in cookie
    assert "Max-Age=0" in cookies["sed_signin_8123"]  # the binding is spent
    assert "default-src 'none'" in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "no-store"

    assert client.get("/api/nav").status_code == 200
    session = client.get("/api/auth/session").json()
    assert session["signed_in"] is True and session["user"]["email"] == OWNER and session["expires_at"]
    assert [e.kind for e in events] == ["sign_in"]


def test_a_cookie_for_another_port_is_ignored(signed_app):
    client, _, fake, _, _ = signed_app
    _google_sign_in(client, fake)
    token = client.cookies.get("sed_session_8123")
    other = TestClient(client.app, base_url="http://127.0.0.1:9999", raise_server_exceptions=False)
    other.cookies.set("sed_session_8123", token)
    assert other.get("/api/nav").status_code == 401


def test_the_start_needs_the_launch_token(signed_app):
    client, *_ = signed_app
    anonymous = TestClient(client.app, base_url=BASE, raise_server_exceptions=False)
    assert_envelope(anonymous.post("/api/auth/start", json={"provider": "google"}), 403, "forbidden")
    assert_envelope(client.post("/api/auth/start", json={"provider": "github"}), 412, "precondition")
    assert_envelope(client.post("/api/auth/start", json={"provider": "yahoo"}), 422, "validation")


def test_a_refusal_page_escapes_what_the_provider_said(signed_app):
    client, _, fake, _, events = signed_app
    response = _google_sign_in(client, fake, email="<script>x</script>@example.org")
    assert response.status_code == 403 and "set-cookie" not in response.headers
    assert "<script>" not in response.text and "&lt;script&gt;" in response.text
    assert "Not allowed" in response.text
    assert client.get("/api/nav").status_code == 401
    assert [e.kind for e in events] == ["sign_in_refused"]


def test_a_forged_or_replayed_callback_is_refused(signed_app):
    client, *_ = signed_app
    response = client.get("/auth/callback", params={"state": "made-up", "code": "x"})
    assert response.status_code == 400 and "set-cookie" not in response.headers
    assert client.get("/auth/finish", params={"handoff": "made-up"}).status_code == 400


def test_an_answer_replayed_from_another_client_never_becomes_a_session(signed_app):
    client, _, fake, _, events = signed_app
    params = query(client.post("/api/auth/start", json={"provider": "google"}).json()["authorize_url"])
    fake.google(params["nonce"])
    thief = TestClient(client.app, base_url=BASE, raise_server_exceptions=False)  # no binding cookie
    response = thief.get("/auth/callback", params={"state": params["state"], "code": "code-1"})
    assert response.status_code == 400 and "another browser" in response.text
    assert "sed_session_8123" not in _set_cookies(response) and thief.get("/api/nav").status_code == 401
    assert fake.forms("token") == [] and events[-1].detail == {"error": "other_browser"}


def test_the_redirect_port_comes_from_the_socket_not_the_host_header(signed_app):
    client, *_ = signed_app
    started = client.post("/api/auth/start", json={"provider": "google"}, headers={"host": "127.0.0.1:1234"})
    assert query(started.json()["authorize_url"])["redirect_uri"] == "http://127.0.0.1:8123/auth/callback"


def test_a_malformed_cookie_from_another_program_does_not_hide_the_session(signed_app):
    client, _, fake, _, _ = signed_app
    _google_sign_in(client, fake)
    token = client.cookies.get("sed_session_8123")
    bare = TestClient(client.app, base_url=BASE, raise_server_exceptions=False)
    for junk in ('prefs={"a":1}', "nameless", "a b=c d", "path=/x", "x@y=1", "expires=soon"):
        response = bare.get("/api/nav", headers={"cookie": f"{junk}; sed_session_8123={token}; other=1"})
        assert response.status_code == 200, junk


def test_microsoft_hands_the_session_back_to_the_host_that_started(signed_app):
    client, _, fake, _, _ = signed_app
    started = client.post("/api/auth/start", json={"provider": "microsoft"}).json()
    params = query(started["authorize_url"])
    assert params["redirect_uri"] == "http://localhost:8123/auth/callback"
    fake.microsoft(params["nonce"])
    on_localhost = TestClient(client.app, base_url="http://localhost:8123", raise_server_exceptions=False)
    landed = on_localhost.get("/auth/callback", params={"state": params["state"], "code": "code-1"})
    assert landed.status_code == 200 and "set-cookie" not in landed.headers
    target = landed.text.split('content="0;url=', 1)[1].split('"', 1)[0].replace("&amp;", "&")
    assert target.startswith("http://127.0.0.1:8123/auth/finish?handoff=")
    finished = client.get(target.removeprefix("http://127.0.0.1:8123"))
    assert finished.status_code == 200 and finished.headers["set-cookie"].startswith("sed_session_8123=")
    assert client.get("/api/nav").status_code == 200
    assert client.get(target.removeprefix("http://127.0.0.1:8123")).status_code == 400  # single use


def test_github_device_sign_in_over_http(signed_app):
    client, _, fake, clock, _ = signed_app
    started = client.post("/api/auth/device/start", json={"provider": "github"})
    assert started.status_code == 200
    body = started.json()
    assert body["user_code"] == "WDJB-MJHT" and body["verification_uri"] == "https://github.com/login/device"
    poll = client.post("/api/auth/device/poll", json={"flow_id": body["flow_id"]})
    assert poll.json()["status"] == "pending" and "set-cookie" not in poll.headers
    fake.github_polls = [{"access_token": "gh-token"}]
    clock.advance(5)
    poll = client.post("/api/auth/device/poll", json={"flow_id": body["flow_id"]})
    assert poll.json()["status"] == "signed_in" and poll.headers["set-cookie"].startswith("sed_session_8123=")
    assert client.get("/api/nav").status_code == 200


def test_sign_out_clears_the_cookie(signed_app):
    client, _, fake, _, events = signed_app
    _google_sign_in(client, fake)
    response = client.post("/api/auth/sign-out", json={})
    assert response.json() == {"signed_out": True}
    assert 'sed_session_8123=""' in response.headers["set-cookie"] or "Max-Age=0" in response.headers["set-cookie"]
    assert client.get("/api/nav").status_code == 401
    assert [e.kind for e in events] == ["sign_in", "sign_out"]


def test_developer_mode_is_the_synthetic_default_and_names_the_windows_account(data_root, monkeypatch):
    monkeypatch.setenv("USERNAME", "synthetic-user")
    app = create_app(get_paths("synthetic"), token=TEST_TOKEN)
    client = TestClient(app, base_url=BASE, raise_server_exceptions=False)
    assert client.get("/api/nav").status_code == 200
    body = client.get("/api/auth/session").json()
    assert body["mode"] == "developer" and body["signed_in"] is False and body["providers"] == []
    assert body["user"] == {"name": "synthetic-user", "email": None, "method": "developer_mode", "verified": False}


def test_the_real_profile_requires_sign_in_unless_the_launch_says_otherwise(data_root):
    paths = get_paths("real")
    client = TestClient(create_app(paths, token=TEST_TOKEN), base_url=BASE, raise_server_exceptions=False)
    assert client.get("/api/nav").status_code == 401
    body = client.get("/api/auth/session").json()
    assert body["mode"] == "sign_in" and body["providers"] == [] and len(body["setup_needed"]) == 2
    developer = create_app(paths, token=TEST_TOKEN, developer_mode=True)
    assert TestClient(developer, base_url=BASE).get("/api/nav").status_code == 200


def test_only_switched_on_providers_are_offered(data_root):
    paths = get_paths("synthetic")
    paths.config.mkdir(parents=True)
    settings = {"mode": "sign_in", "providers": {"github": {"enabled": True, "client_id": "test-github-client"}}}
    (paths.config / "auth.yaml").write_text(json.dumps(settings), encoding="utf-8")  # JSON is YAML
    body = TestClient(create_app(paths, token=TEST_TOKEN), base_url=BASE).get("/api/auth/session").json()
    assert [p["key"] for p in body["providers"]] == ["github"]
    assert body["setup_needed"] == ["nobody is on the list of people allowed in"]


def _raw_status(app, path: str) -> int:
    """Send one GET exactly as written (an HTTP client would normalise paths like //api/nav)."""
    import asyncio

    sent: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("latin-1"),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"127.0.0.1:8123")],
        "client": ("127.0.0.1", 50000),
        "server": ("127.0.0.1", 8123),
    }
    asyncio.run(app(scope, receive, send))
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


@pytest.mark.parametrize("path", ["//api/nav", "/API/nav", "/api", "/api/healthz", "/api/branding.x", "/other/page"])
def test_the_gate_denies_by_default(signed_app, path):
    client, *_ = signed_app
    assert _raw_status(client.app, path) == 401


def test_a_public_prefix_cannot_reach_another_route(signed_app):
    client, *_ = signed_app
    assert _raw_status(client.app, "/api/auth/../nav") == 404  # routes match literally


@pytest.mark.parametrize("path", ["/", "/index.html", "/assets/x.js", "/favicon.ico", "/auth/finish", "/api/health"])
def test_the_dashboard_shell_is_open(signed_app, path):
    client, *_ = signed_app
    assert _raw_status(client.app, path) != 401
