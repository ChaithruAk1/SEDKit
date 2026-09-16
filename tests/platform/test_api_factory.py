"""App factory security and contract: host allowlist, token, headers, envelopes, OpenAPI shape, module mounting."""

from __future__ import annotations

from sed import modules
from sed.api.app import create_app
from tests.fixtures.api import TEST_TOKEN, api_client
from tests.platform.modules.sample_module import MODULE as HELLO

SECURITY_HEADERS = {
    "x-frame-options": "DENY",
    "content-security-policy": "frame-ancestors 'none'",
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
}


def test_health_and_security_headers(ops_profile):
    client = api_client(ops_profile.paths)
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["ok"] is True
    for name, value in SECURITY_HEADERS.items():
        assert r.headers[name] == value


def test_foreign_host_is_rejected(ops_profile):
    client = api_client(ops_profile.paths)
    assert client.get("/api/health", headers={"Host": "evil.example"}).status_code == 400


def test_unsafe_methods_need_the_token(ops_profile):
    body = {"kind": "vendor", "raw_value": "x", "target": "y"}
    no_token = api_client(ops_profile.paths, send_token=False).post("/api/aliases", json=body)
    assert no_token.status_code == 403 and no_token.json()["error"]["kind"] == "forbidden"
    wrong = api_client(ops_profile.paths, send_token=False).post(
        "/api/aliases", json=body, headers={"X-SED-Token": "nope"}
    )
    assert wrong.status_code == 403
    with_token = api_client(ops_profile.paths).post("/api/aliases", json=body)
    # With the token the request reaches the route: an unknown target is a validation error, raised before any write.
    assert with_token.status_code == 422
    assert with_token.json()["ok"] is False and with_token.json()["error"]["kind"] == "validation"


def test_validation_and_not_found_use_the_envelope(ops_profile):
    client = api_client(ops_profile.paths)
    bad = client.post("/api/aliases", json={"kind": "vendor"})
    assert bad.status_code == 422 and bad.json()["error"]["kind"] == "validation"
    missing = client.get("/api/does-not-exist")
    assert missing.status_code == 404 and missing.json()["ok"] is False


def test_openapi_uses_error_envelope_and_response_models(tmp_path):
    from sed.paths import Paths

    app = create_app(Paths("synthetic", tmp_path), token=TEST_TOKEN, modules=modules.installed(include_extra=False))
    schema = app.openapi()
    components = schema["components"]["schemas"]
    assert "ErrorEnvelope" in components and "HTTPValidationError" not in components
    ops_routes = [p for p in schema["paths"] if p.startswith("/api/ops/")]
    assert len(ops_routes) == 15
    keys = {m.key for m in modules.installed(include_extra=False)}
    for path, item in schema["paths"].items():
        segment = path.split("/")[2]
        prefix = f"{segment}_" if segment in keys else "core_"
        for method, operation in item.items():
            responses = operation["responses"]
            assert responses["422"]["content"]["application/json"]["schema"] == {
                "$ref": "#/components/schemas/ErrorEnvelope"
            }
            assert "200" in responses and responses["200"].get("content"), (method, path)
            assert operation["operationId"].startswith(prefix), (path, operation["operationId"])
            if method == "post":
                assert "403" in responses


def test_extra_module_router_is_mounted_and_protected(ops_profile):
    ops = modules.get("ops")
    client = api_client(ops_profile.paths, modules=[ops, HELLO])
    assert client.get("/api/hello/ping").json() == {"module": "hello", "reply": "pong"}
    assert client.post("/api/hello/echo", json={"text": "hi"}).json() == {"text": "hi"}
    unprotected = api_client(ops_profile.paths, modules=[ops, HELLO], send_token=False)
    assert unprotected.post("/api/hello/echo", json={"text": "hi"}).status_code == 403


def test_disabled_module_is_not_mounted(ops_profile):
    with modules.use_modules([modules.get("ops")], enabled_keys=set()):
        client = api_client(ops_profile.paths)
        assert client.get("/api/ops/filters").status_code == 404
    assert api_client(ops_profile.paths).get("/api/ops/filters").status_code == 200


def test_index_injects_token_without_caching(ops_profile, tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text('<meta name="sed-token" content="__SED_TOKEN__">', encoding="utf-8")
    r = api_client(ops_profile.paths, web_dist=dist).get("/")
    assert r.status_code == 200 and TEST_TOKEN in r.text and r.headers["cache-control"] == "no-store"
