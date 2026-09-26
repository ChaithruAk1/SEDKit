# Local API (`sed.api`)

- **App factory:** `app.create_app(paths, *, token, web_dist=None, modules=None)` builds the app.
  - Core routes are mounted at `/api/...` (`routes_core.py`; review queue, finding decisions, run detail, verdicts,
    run approval and label corrections in `routes_review.py`; reports, readiness, background builds and artifact
    downloads in `routes_reports.py`, with the in-memory job worker in `jobs.py`; sources overview, export uploads (raw
    body, stored by `sed.ingest.upload`) and connector "Pull now" in `routes_sources.py`, both POSTs as jobs).
  - Each enabled module's router is mounted at `/api/<key>/...`.
- **Security:** mandatory for every route, including module routers.
  - The host allowlist is 127.0.0.1 and localhost; any other host gets 400. No CORS.
  - Sign-in (`sed.auth`, `routes_auth.py`, `docs/sign-in.md`): every request needs the session cookie unless SED runs
    in developer mode, otherwise 401 `unauthenticated` (deny by default). Open: the dashboard shell (`/`, `/assets/*`,
    top-level files), `/auth/callback`, `/auth/finish`, `/api/health`, `/api/branding*`, `/api/auth/*`.
    Routes read who is asking with `deps.actor(request)`; record decisions with `deps.reviewer(request)`, never the
    Windows user name.
  - Audit trail (`docs/audit.md`): a route that hands out data (a download) calls `sed.audit.record.record` before
    returning it, and one that pulls, imports or clears wraps the work in `sed.audit.actions.start_*`. A job records
    its outcome inside the job. When the trail cannot take the entry the route refuses (412).
  - Every non-GET/HEAD/OPTIONS request under `/api` needs the `X-SED-Token` header (the per-launch token), otherwise 403.
  - The token is never logged, and never put in a URL or response body.
  - Anti-framing and no-sniff headers go on every response. `index.html` is served `no-store`.
- **Errors:** one envelope, `{ok: false, error: {kind, message, details}}`.
  - validation → 422
  - busy → 409 with `Retry-After`
  - precondition → 412
  - forbidden → 403
  - not_found → 404
  - not_implemented → 501
  - internal → 500, with no traceback
- **Database:** GET routes use `deps.read_conn` (read-only, per request). A GET must never write: no snapshots, no rule refresh.
  Writes use `deps.write_conn` and `db.write_tx`.
- **Models:** every route declares a `response_model`. Core models live in `models.py`, module models in the module
  (`sed.modules.<key>.api_models`).
- **Contract:** `contracts/openapi.json` is generated (`uv run python scripts/codegen.py`). Never edit it by hand.
- **Findings:** always go through `findings.published_findings`, so rule and AI semantics are identical everywhere.
