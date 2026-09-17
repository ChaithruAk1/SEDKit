"""Report routes mounted at /api: the reports of enabled modules with recent artifacts, AI section readiness, background
builds and artifact downloads.

GET routes are read-only (`deps.read_conn`; readiness compares with the latest stored snapshot and never takes a new
one). POST /reports/build needs the per-launch token and runs `sed.reports.build.build_report` on the job worker, the
same function as `sed report build`. Downloads are limited to artifacts recorded in report_artifact whose file lies
inside the profile's out folder.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse

from sed.api.deps import read_conn
from sed.api.models import ArtifactRow, BuildIn, JobOut, ReadinessOut, ReportInfo, ReportsOut
from sed.errors import PreconditionFailed

router = APIRouter(tags=["core"])
MEDIA_TYPES = {
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".md": "text/markdown; charset=utf-8",
}


def _jobs(request: Request) -> Any:
    runner = getattr(request.app.state, "jobs", None)
    if runner is None:
        from sed.api.jobs import JobRunner

        runner = request.app.state.jobs = JobRunner()
    return runner


def _json_list(text: str | None) -> list[str]:
    try:
        value = json.loads(text or "[]")
    except ValueError:
        return []
    return [str(v) for v in value] if isinstance(value, list) else []


@router.get("/reports", response_model=ReportsOut)
def reports(
    request: Request,
    limit: int = Query(50, ge=1, le=500),
    conn: sqlite3.Connection = Depends(read_conn),
) -> ReportsOut:
    # Reports of the enabled modules (with their AI section keys) and the newest artifacts.
    from sed.modules import reports as module_reports
    from sed.reports.specs import load_report_spec

    paths = request.app.state.paths
    infos = []
    for module, rdef in module_reports(paths):
        spec = load_report_spec(rdef.key, paths)
        infos.append(
            ReportInfo(
                key=rdef.key,
                module=module.key,
                title=rdef.title,
                period_kinds=list(rdef.period_kinds),
                needs_vendor=rdef.needs_vendor,
                formats=list(rdef.formats),
                sections=[s.key for s in spec.sections],
            )
        )
    rows = conn.execute(
        "SELECT a.artifact_id, a.format, a.path, a.sha256, a.ai_mode, a.ai_run_ids_json, a.unapproved_omitted_json, "
        "a.built_at, s.report_key, s.period, s.vendor_id, s.snapshot_id FROM report_artifact a "
        "JOIN report_snapshot s ON s.snapshot_id = a.snapshot_id ORDER BY a.built_at DESC, a.artifact_id LIMIT ?",
        (limit,),
    ).fetchall()
    artifacts = [
        ArtifactRow(
            artifact_id=r["artifact_id"],
            report=r["report_key"],
            period=r["period"],
            vendor_id=r["vendor_id"],
            snapshot_id=r["snapshot_id"],
            format=r["format"],
            ai_mode=r["ai_mode"],
            built_at=r["built_at"],
            file_name=Path(r["path"]).name,
            sha256=r["sha256"],
            ai_run_ids=_json_list(r["ai_run_ids_json"]),
            omitted=_json_list(r["unapproved_omitted_json"]),
        )
        for r in rows
    ]
    return ReportsOut(reports=infos, artifacts=artifacts)


@router.get("/reports/readiness", response_model=ReadinessOut)
def readiness(
    request: Request,
    report: str = Query(min_length=1, max_length=40),
    period: str = Query(min_length=4, max_length=12),
    vendor: str | None = Query(None, max_length=60),
    conn: sqlite3.Connection = Depends(read_conn),
) -> ReadinessOut:
    # AI section readiness of one report period against its latest stored snapshot (read-only).
    from sed.modules import report as report_def
    from sed.reports.sections import readiness as section_readiness

    report_def(report)
    return ReadinessOut(**section_readiness(conn, request.app.state.paths, report, period, vendor))


@router.post("/reports/build", response_model=JobOut)
def build(body: BuildIn, request: Request) -> JobOut:
    # Start a report build on the background worker; poll GET /api/jobs/{job_id}.
    from sed.modules import report as report_def
    from sed.reports.build import build_report

    _, rdef = report_def(body.report)
    if rdef.needs_vendor and not body.vendor:
        raise PreconditionFailed(f"The {body.report} report needs a vendor")
    paths = request.app.state.paths
    params = body.model_dump()

    def work() -> dict[str, Any]:
        result = build_report(
            paths,
            body.report,
            body.period,
            list(body.formats) if body.formats else None,
            body.ai_mode,
            body.vendor,
            require_complete=body.require_complete,
        )
        for artifact in result["artifacts"]:
            artifact["file_name"] = Path(artifact["path"]).name
            artifact["artifact_id"] = _artifact_id(paths, result["snapshot_id"], artifact)
            artifact.pop("path", None)
        return result

    job = _jobs(request).submit("report_build", params, work)
    return JobOut(**job.as_dict())


def _artifact_id(paths: Any, snapshot_id: str, artifact: dict[str, Any]) -> str | None:
    from sed import db

    conn = db.connect(paths.db, readonly=True)
    try:
        row = conn.execute(
            "SELECT artifact_id FROM report_artifact WHERE snapshot_id = ? AND format = ? AND sha256 = ?",
            (snapshot_id, artifact["format"], artifact["sha256"]),
        ).fetchone()
    finally:
        conn.close()
    return row["artifact_id"] if row else None


@router.get("/jobs/{job_id}", response_model=JobOut)
def job_status(job_id: str, request: Request) -> JobOut:
    # One background job: queued, running, done (with its result) or failed (with the error envelope fields).
    job = _jobs(request).get(job_id)
    if job is None:
        raise PreconditionFailed(f"Unknown job '{job_id}' (jobs are forgotten when sed serve restarts)")
    return JobOut(**job.as_dict())


@router.get("/reports/artifacts/{artifact_id}/file", include_in_schema=False)
def artifact_file(artifact_id: str, request: Request, conn: sqlite3.Connection = Depends(read_conn)) -> FileResponse:
    # Download one recorded artifact; the file must be inside the profile's out folder.
    row = conn.execute("SELECT path FROM report_artifact WHERE artifact_id = ?", (artifact_id,)).fetchone()
    if row is None:
        raise PreconditionFailed(f"Unknown artifact '{artifact_id}'")
    out_dir = request.app.state.paths.out.resolve()
    path = Path(row["path"]).resolve()
    if not path.is_relative_to(out_dir) or not path.is_file():
        raise PreconditionFailed(f"Artifact file for '{artifact_id}' is not available")
    return FileResponse(path, filename=path.name, media_type=MEDIA_TYPES.get(path.suffix.lower()))
