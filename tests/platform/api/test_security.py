"""Local hardening (spec §8, review dimension 2): host allowlist, token on every unsafe method, no CORS, anti-framing
headers on every response, token injection into index.html only, static serving limited to web/dist."""

from __future__ import annotations

import logging
import re

import pytest

from sed import modules
from sed.api.app import create_app
from sed.paths import Paths
from tests.fixtures.api import TEST_TOKEN, api_client
from tests.platform.api.conftest import SECURITY_HEADERS, assert_envelope
from tests.platform.modules.sample_module import MODULE as HELLO

ALIAS_BODY = {"kind": "vendor", "raw_value": "Unlisted Vendor", "target": "No Such Vendor"}


def assert_security_headers(response) -> None:
    for name, value in SECURITY_HEADERS.items():
        assert response.headers.get(name) == value, (name, response.status_code, dict(response.headers))
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize("host", ["evil.com", "evil.com:8000", "127.0.0.1.evil.com", "localhost.evil.com"])
def test_foreign_host_gets_400(ops_profile, host):
    client = api_client(ops_profile.paths)
    for response in (
        client.get("/api/meta", headers={"Host": host}),
        client.post("/api/aliases", json=ALIAS_BODY, headers={"Host": host}),
    ):
        assert response.status_code == 400
        assert_security_headers(response)


def test_allowed_hosts_with_ports_are_accepted(ops_profile):
    client = api_client(ops_profile.paths)
    for host in ("127.0.0.1", "127.0.0.1:8000", "localhost", "localhost:5173"):
        assert client.get("/api/health", headers={"Host": host}).status_code == 200


def test_unsafe_methods_without_or_with_a_wrong_token_get_403(ops_profile):
    client = api_client(ops_profile.paths, send_token=False)
    attempts = [
        client.post("/api/aliases", json=ALIAS_BODY),
        client.post("/api/aliases", json=ALIAS_BODY, headers={"X-SED-Token": "wrong-token"}),
        client.post("/api/aliases", json=ALIAS_BODY, headers={"X-SED-Token": ""}),
        client.post("/api/aliases", json=ALIAS_BODY, headers={"X-SED-Token": TEST_TOKEN.upper()}),
        client.post("/api/aliases", json=ALIAS_BODY, headers={"X-SED-Token": TEST_TOKEN + " "}),
        client.post("/api/aliases", json=ALIAS_BODY, headers=[("X-SED-Token", TEST_TOKEN), ("X-SED-Token", "x")]),
        client.put("/api/aliases", json=ALIAS_BODY),
        client.delete("/api/aliases"),
        client.patch("/api/meta", json={}),
        client.post("/api/health"),
        client.post("/", content=b"x"),
        client.post("/api/does-not-exist"),
    ]
    for response in attempts:
        body = assert_envelope(response, 403, "forbidden")
        assert body["error"] == {"kind": "forbidden", "message": "Missing or invalid X-SED-Token", "details": None}
        assert_security_headers(response)
        assert TEST_TOKEN not in response.text


def test_token_is_accepted_only_from_the_header(ops_profile):
    client = api_client(ops_profile.paths, send_token=False)
    # A token in the URL is never accepted.
    assert client.post(f"/api/aliases?X-SED-Token={TEST_TOKEN}", json=ALIAS_BODY).status_code == 403
    with_token = client.post("/api/aliases", json=ALIAS_BODY, headers={"x-sed-token": TEST_TOKEN})
    assert_envelope(with_token, 422, "validation")


def test_safe_methods_need_no_token(ops_profile):
    client = api_client(ops_profile.paths, send_token=False)
    for path in ("/api/health", "/api/meta", "/api/nav", "/api/findings", "/api/openapi.json"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert_security_headers(response)
    assert client.head("/api/health").status_code in {200, 405}
    assert client.options("/api/aliases").status_code != 403


def test_no_cors_even_for_preflight_requests(ops_profile):
    client = api_client(ops_profile.paths, send_token=False)
    origin = {"Origin": "http://evil.example"}
    preflight = client.options(
        "/api/aliases",
        headers={**origin, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "x-sed-token"},
    )
    for response in (client.get("/api/meta", headers=origin), preflight):
        assert not any(name.startswith("access-control-") for name in response.headers), dict(response.headers)


def test_error_responses_carry_security_headers(ops_profile, monkeypatch):
    client = api_client(ops_profile.paths)
    assert_security_headers(assert_envelope_response(client.get("/api/nope"), 404, "not_found"))
    assert_security_headers(assert_envelope_response(client.post("/api/aliases", json={}), 422, "validation"))
    assert_security_headers(assert_envelope_response(client.post("/api/meta"), 405, "method_not_allowed"))

    def boom(*args, **kwargs):
        raise RuntimeError("secret row text that must not leak")

    monkeypatch.setattr("sed.api.routes_core.import_freshness", boom)
    crashed = client.get("/api/meta")
    body = assert_envelope(crashed, 500, "internal")
    assert body["error"]["message"] == "Internal error (RuntimeError)" and "secret row text" not in crashed.text
    assert_security_headers(crashed)


def assert_envelope_response(response, status, kind):
    assert_envelope(response, status, kind)
    return response


def test_module_router_writes_need_the_token(ops_profile):
    mods = [modules.get("ops"), HELLO]
    assert api_client(ops_profile.paths, modules=mods).post("/api/hello/echo", json={"text": "hi"}).json() == {
        "text": "hi"
    }
    anonymous = api_client(ops_profile.paths, modules=mods, send_token=False)
    assert_envelope(anonymous.post("/api/hello/echo", json={"text": "hi"}), 403, "forbidden")
    wrong = anonymous.post("/api/hello/echo", json={"text": "hi"}, headers={"X-SED-Token": "nope"})
    assert_envelope(wrong, 403, "forbidden")
    assert anonymous.get("/api/hello/ping").status_code == 200


def test_token_is_never_logged(ops_profile, monkeypatch, caplog):
    token = "log-probe-token"
    client = api_client(ops_profile.paths, token=token)
    monkeypatch.setattr("sed.api.routes_core.import_freshness", lambda conn: 1 / 0)
    with caplog.at_level(logging.DEBUG):
        client.post("/api/aliases", json=ALIAS_BODY)
        client.post("/api/aliases", json=ALIAS_BODY, headers={"X-SED-Token": "wrong-token"})
        client.get("/api/meta")
    assert caplog.records, "expected at least the internal-error log line"
    assert token not in caplog.text


@pytest.mark.parametrize("token", ["", "has space", 'quote"', "<script>", "a\nb"])
def test_unsafe_tokens_are_refused(tmp_path, token):
    with pytest.raises(ValueError):
        create_app(Paths("synthetic", tmp_path), token=token)


def _dist(tmp_path):
    dist = tmp_path / "web" / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(
        '<!doctype html><meta name="sed-token" content="__SED_TOKEN__"><div id="root"></div>', encoding="utf-8"
    )
    (dist / "assets" / "app.js").write_text("console.log('app');", encoding="utf-8")
    (dist / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    (tmp_path / "web" / "outside.txt").write_text("outside dist", encoding="utf-8")
    return dist


def test_index_has_the_token_meta_and_no_store(ops_profile, tmp_path):
    client = api_client(ops_profile.paths, web_dist=_dist(tmp_path), send_token=False)
    for path in ("/", "/index.html"):
        response = client.get(path)
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        assert re.search(r'<meta name="sed-token" content="test-token">', response.text)
        assert "__SED_TOKEN__" not in response.text
        assert_security_headers(response)
    assert client.get("/assets/app.js").text == "console.log('app');"
    assert client.get("/favicon.svg").status_code == 200


def test_static_serving_is_limited_to_web_dist(ops_profile, tmp_path):
    client = api_client(ops_profile.paths, web_dist=_dist(tmp_path), send_token=False)
    for path in (
        "/outside.txt",
        "/assets/../outside.txt",
        "/assets/..%2F..%2Foutside.txt",
        "/assets/%2e%2e/%2e%2e/outside.txt",
        "/..%2Foutside.txt",
        "/pyproject.toml",
    ):
        response = client.get(path)
        assert response.status_code in {400, 404}, (path, response.status_code)
        assert "outside dist" not in response.text
    assert client.get("/api/health").json()["ok"] is True  # SPA routes never shadow the API


def test_without_web_dist_only_the_api_is_served(ops_profile, tmp_path):
    client = api_client(ops_profile.paths, web_dist=tmp_path / "no-dist")
    assert client.get("/").status_code == 404
    assert client.get("/api/health").status_code == 200


def test_openapi_documents_the_envelope_and_token_errors(ops_profile):
    schema = api_client(ops_profile.paths, send_token=False).get("/api/openapi.json").json()
    assert "ErrorEnvelope" in schema["components"]["schemas"]
    post = schema["paths"]["/api/aliases"]["post"]["responses"]
    assert {"403", "409", "422"} <= set(post)
    assert "403" not in schema["paths"]["/api/meta"]["get"]["responses"]
    assert TEST_TOKEN not in str(schema)
