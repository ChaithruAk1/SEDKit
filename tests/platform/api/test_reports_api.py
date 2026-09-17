"""Report routes: the reports of enabled modules with their sections, background builds (token required, job polling,
failures as error fields), artifact downloads limited to recorded files in the out folder, and read-only readiness."""

from __future__ import annotations

import sqlite3

from sed.api.models import JobOut, ReadinessOut, ReportsOut
from tests.fixtures.api import api_client
from tests.platform.api.conftest import assert_envelope


def _wait(client, job_id: str) -> JobOut:
    client.app.state.jobs.wait(job_id, timeout=120)
    return JobOut.model_validate(client.get(f"/api/jobs/{job_id}").json())


def test_reports_build_job_download_and_readiness(ops_profile_rw):
    paths = ops_profile_rw.paths
    client = api_client(paths)
    catalog = ReportsOut.model_validate(client.get("/api/reports").json())
    weekly = next(r for r in catalog.reports if r.key == "weekly")
    assert weekly.sections[:1] == ["headline"] and weekly.formats == ["xlsx", "md", "pptx"] and catalog.artifacts == []
    assert {r.key for r in catalog.reports} >= {"weekly", "monthly", "quarterly", "vendor", "sap-weekly"}

    body = {"report": "weekly", "period": "2026-W35", "formats": ["md", "xlsx"], "ai_mode": "none"}
    assert_envelope(api_client(paths, send_token=False).post("/api/reports/build", json=body), 403, "forbidden")
    started = client.post("/api/reports/build", json=body)
    assert started.status_code == 200, started.text
    job = _wait(client, started.json()["job_id"])
    assert job.status == "done" and job.params["report"] == "weekly"
    artifacts = job.result["artifacts"]
    assert [a["format"] for a in artifacts] == ["md", "xlsx"] and all(a["artifact_id"] for a in artifacts)
    assert all("path" not in a for a in artifacts)  # no local paths in API responses

    md = next(a for a in artifacts if a["format"] == "md")
    download = client.get(f"/api/reports/artifacts/{md['artifact_id']}/file")
    assert download.status_code == 200 and "SYNTHETIC DATA" in download.text
    assert md["file_name"] in download.headers["content-disposition"]
    assert_envelope(client.get("/api/reports/artifacts/art-nope/file"), 412, "precondition")

    conn = sqlite3.connect(str(paths.db))
    try:
        with conn:
            conn.execute(
                "UPDATE report_artifact SET path = ? WHERE artifact_id = ?", (str(paths.db), md["artifact_id"])
            )
    finally:
        conn.close()
    outside = client.get(f"/api/reports/artifacts/{md['artifact_id']}/file")
    assert_envelope(outside, 412, "precondition")  # only files inside the out folder

    listed = ReportsOut.model_validate(client.get("/api/reports").json()).artifacts
    assert {a.format for a in listed} == {"md", "xlsx"} and all(a.ai_mode == "none" for a in listed)
    assert any(o.startswith("headline:") for o in listed[0].omitted)

    ready = ReadinessOut.model_validate(client.get("/api/reports/readiness?report=weekly&period=2026-W35").json())
    assert ready.snapshot_id and ready.sections_required == 5 and not ready.complete
    assert [s.key for s in ready.sections][:1] == ["headline"] and ready.sections[0].approved_status == "missing"

    refused = client.post("/api/reports/build", json={**body, "ai_mode": "approved", "require_complete": True})
    failed = _wait(client, refused.json()["job_id"])
    assert failed.status == "failed" and failed.error["kind"] == "precondition"
    assert len(failed.error["details"]["sections"]) == 5
    assert_envelope(
        client.post("/api/reports/build", json={"report": "vendor", "period": "2026-Q3"}), 412, "precondition"
    )
    assert_envelope(client.post("/api/reports/build", json={**body, "ai_mode": "sometimes"}), 422, "validation")
    assert_envelope(client.get("/api/jobs/job-unknown"), 412, "precondition")
