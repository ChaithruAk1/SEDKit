"""FastAPI application factory: hardened local host for the core routes, module routers and the built dashboard."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.routing import APIRoute
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from sed import __version__
from sed.api import errors
from sed.api.models import ErrorBody, ErrorEnvelope
from sed.api.security import SecurityHeadersMiddleware, TokenMiddleware, validate_token
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
INDEX = "index.html"
NO_STORE = {"Cache-Control": "no-store"}


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


def _mount_web(app: FastAPI, web_dist: Path, token: str) -> None:
    """Serve the built dashboard: index.html with the launch token injected (never cached), /assets, and the other
    top-level files of web_dist (favicon and similar). Nothing outside web_dist is reachable."""
    dist = web_dist.resolve()
    index_template = (dist / INDEX).read_text(encoding="utf-8")
    index_html = index_template.replace(TOKEN_PLACEHOLDER, token)
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")
    # Fixed at startup: a request can only name one of these files, so there is no path to traverse.
    root_files = {p.name: p for p in dist.iterdir() if p.is_file() and p.name != INDEX and not p.name.startswith(".")}

    @app.get("/", include_in_schema=False)
    def index() -> HTMLResponse:
        return HTMLResponse(index_html, headers=NO_STORE)

    @app.get(f"/{INDEX}", include_in_schema=False)
    def index_file() -> HTMLResponse:
        return HTMLResponse(index_html, headers=NO_STORE)

    @app.get("/{name}", include_in_schema=False)
    def root_file(name: str) -> FileResponse:
        path = root_files.get(name)
        if path is None:
            raise StarletteHTTPException(404)
        return FileResponse(path)


def create_app(
    paths: Paths,
    *,
    token: str,
    web_dist: Path | None = None,
    modules: Iterable[Any] | None = None,
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost"),
) -> FastAPI:
    from sed import modules as registry
    from sed.api import routes_core, routes_reports, routes_review, routes_sources

    validate_token(token)
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
    app.include_router(routes_review.router, prefix="/api")
    app.include_router(routes_reports.router, prefix="/api")
    app.include_router(routes_sources.router, prefix="/api")
    mounted = tuple(modules) if modules is not None else registry.enabled(paths)
    for module in mounted:
        if module.api:
            app.include_router(registry.load_ref(module.api.router), prefix=f"/api/{module.key}", tags=[module.key])
    # The modules this app serves: the core routes (nav, meta, modules) describe exactly these.
    app.state.modules = [m.key for m in mounted]
    app.state.module_manifests = mounted

    if web_dist is not None and (web_dist / INDEX).is_file():
        _mount_web(app, web_dist, token)

    app.openapi = lambda: _openapi(app)  # type: ignore[method-assign]
    # Middleware order, outermost first: security headers (so 400 and 403 carry them too), host check, token.
    app.add_middleware(TokenMiddleware, token=token)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(allowed_hosts))
    app.add_middleware(SecurityHeadersMiddleware)
    return app
