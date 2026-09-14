"""FastAPI application factory: hardened local host for the core routes, module routers and the built dashboard."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse
from fastapi.routing import APIRoute
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from sed import __version__
from sed.api import errors
from sed.api.models import ErrorBody, ErrorEnvelope
from sed.api.security import SecurityHeadersMiddleware, TokenMiddleware
from sed.paths import Paths

ENVELOPE_REF = {"$ref": "#/components/schemas/ErrorEnvelope"}
ERROR_RESPONSES = {
    "403": "Missing or invalid X-SED-Token",
    "404": "Not found",
    "409": "Database busy; retry",
    "412": "Precondition failed",
    "422": "Validation error",
    "501": "Not implemented yet",
}
TOKEN_PLACEHOLDER = "__SED_TOKEN__"


def operation_id(route: APIRoute) -> str:
    return f"{route.tags[0] if route.tags else 'core'}_{route.name}"


def _openapi(app: FastAPI) -> dict[str, Any]:
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(title=app.title, version=app.version, routes=app.routes, description=app.description)
    components = schema.setdefault("components", {}).setdefault("schemas", {})
    components.pop("HTTPValidationError", None)
    components.pop("ValidationError", None)
    for model in (ErrorBody, ErrorEnvelope):
        definition = model.model_json_schema(ref_template="#/components/schemas/{model}")
        components.update(definition.pop("$defs", {}))
        components[model.__name__] = definition
    for path_item in schema.get("paths", {}).values():
        for method, operation in path_item.items():
            responses = operation.setdefault("responses", {})
            for code, description in ERROR_RESPONSES.items():
                if code == "403" and method in {"get", "head", "options"}:
                    continue
                responses[code] = {
                    "description": description,
                    "content": {"application/json": {"schema": ENVELOPE_REF}},
                }
    app.openapi_schema = schema
    return schema


def create_app(
    paths: Paths,
    *,
    token: str,
    web_dist: Path | None = None,
    modules: Iterable[Any] | None = None,
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost"),
) -> FastAPI:
    from sed import modules as registry
    from sed.api import routes_core

    app = FastAPI(
        title="SED",
        version=__version__,
        description="Local SED API (127.0.0.1 only). Unsafe methods require the X-SED-Token header.",
        openapi_url="/api/openapi.json",
        docs_url=None,
        redoc_url=None,
        generate_unique_id_function=operation_id,
    )
    app.state.paths = paths
    app.state.token = token
    errors.install(app)
    app.include_router(routes_core.router, prefix="/api")
    mounted = tuple(modules) if modules is not None else registry.enabled(paths)
    for module in mounted:
        if module.api:
            app.include_router(registry.load_ref(module.api.router), prefix=f"/api/{module.key}", tags=[module.key])
    app.state.modules = [m.key for m in mounted]

    if web_dist is not None and (web_dist / "index.html").is_file():
        index_template = (web_dist / "index.html").read_text(encoding="utf-8")
        if (web_dist / "assets").is_dir():
            app.mount("/assets", StaticFiles(directory=web_dist / "assets"), name="assets")

        @app.get("/", include_in_schema=False)
        def index() -> HTMLResponse:
            return HTMLResponse(index_template.replace(TOKEN_PLACEHOLDER, token), headers={"Cache-Control": "no-store"})

    app.openapi = lambda: _openapi(app)  # type: ignore[method-assign]
    # Middleware order (outermost last): host check, then token, then security headers on every response.
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(TokenMiddleware, token=token)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(allowed_hosts))
    return app
