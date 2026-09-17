"""Source routes mounted at /api (W8): every source works by API pull or by export file.

- `GET /sources`: each export of the enabled modules with the connector sources that can pull it and its newest
  import, and each connector's readiness, sources, watermarks and last pull (`sed.sources.overview`; no network, never
  a secret value).
- `POST /imports/upload?name=<file>[&synthetic_ok=true]`: the raw file as the request body (no multipart). The file is
  checked and stored in the inbox during the request (`sed.ingest.upload.save_upload`, so a bad name, type, size or
  zip fails at once), then imported on the job worker with the normal import of exactly that file.
- `POST /sources/{connector}/pull`: `sed.sources.pull_and_import` on the job worker (read-only against the tool, then
  the normal import of the files the pull wrote). Refused at once when the connector cannot pull (disabled, no
  credential, not the real profile).

Both POSTs need the per-launch token and answer with a job; poll `GET /api/jobs/{job_id}`.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from sed.api.deps import read_conn
from sed.api.models import JobOut, PullIn, SourcesOut
from sed.api.routes_reports import _jobs
from sed.errors import PreconditionFailed, ValidationFailed

router = APIRouter(tags=["core"])

UPLOAD_BODY = {
    "requestBody": {
        "required": True,
        "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
    }
}


@router.get("/sources", response_model=SourcesOut)
def sources(request: Request, conn: sqlite3.Connection = Depends(read_conn)) -> SourcesOut:
    # Read-only overview of every source: connectors and their readiness, exports and their last import.
    from sed.sources import overview

    return SourcesOut(**overview(request.app.state.paths, conn))


@router.post("/imports/upload", response_model=JobOut, openapi_extra=UPLOAD_BODY)
async def upload(
    request: Request,
    name: str = Query(min_length=1, max_length=255, description="The file name, e.g. incident_2026-08.csv"),
    synthetic_ok: bool = Query(False, description="Synthetic profile only: the file is a hand-made fictional fixture"),
) -> JobOut:
    # Store the uploaded export in the inbox, then import exactly that file on the job worker.
    from sed.ingest.upload import MAX_BYTES, save_upload
    from sed.sources import import_paths

    length = request.headers.get("content-length", "")
    if length.isdigit() and int(length) > MAX_BYTES:
        raise ValidationFailed(f"The upload is larger than {MAX_BYTES // (1024 * 1024)} MB")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BYTES:
            raise ValidationFailed(f"The upload is larger than {MAX_BYTES // (1024 * 1024)} MB")
        chunks.append(chunk)
    paths = request.app.state.paths
    saved = save_upload(paths, name, b"".join(chunks), synthetic_ok=synthetic_ok)
    stored = Path(saved["path"])
    info = {k: v for k, v in saved.items() if k != "path"}

    def work() -> dict[str, Any]:
        return {"upload": info, "import": import_paths(paths, [stored], synthetic_ok=synthetic_ok)}

    job = _jobs(request).submit("import_upload", {"name": name, **info}, work)
    return JobOut(**job.as_dict())


@router.post("/sources/{connector}/pull", response_model=JobOut)
def pull_now(connector: str, body: PullIn, request: Request) -> JobOut:
    # Pull one connector (optionally one source) and import what it wrote, on the job worker.
    from sed.connectors.config import CONNECTORS
    from sed.sources import pull_and_import, pull_refusal

    if connector not in CONNECTORS:
        raise ValidationFailed(f"Unknown connector '{connector}' (available: {', '.join(CONNECTORS)})")
    paths = request.app.state.paths
    reason = pull_refusal(paths, connector)
    if reason:
        raise PreconditionFailed(f"Connector '{connector}' cannot pull: {reason}")

    def work() -> dict[str, Any]:
        return pull_and_import(paths, connector, source=body.source, full=body.full)

    job = _jobs(request).submit("source_pull", {"connector": connector, **body.model_dump()}, work)
    return JobOut(**job.as_dict())
