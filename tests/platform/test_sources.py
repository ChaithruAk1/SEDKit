"""Every source by API or by file (W8): the sources overview (which connector source feeds which export, readiness,
last import), document library pulls (listing, pre-authenticated downloads, allowed hosts, watermark), pull-and-import
of exactly the pulled files, an uploaded copy of a pulled file importing as the same data, and the API routes (token,
jobs, refusals)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import yaml

from sed import db
from sed.api.models import JobOut, SourcesOut
from sed.connectors.http import Client, RecordedTransport
from sed.errors import PreconditionFailed
from sed.sources import overview, pull_and_import, upload_and_import
from tests.fixtures.api import api_client
from tests.platform.api.conftest import assert_envelope

FAKE_VALUE = "fixture-not-a-real-secret"
NOW = datetime(2026, 9, 12, 6, 0, tzinfo=UTC)
LISTING = "/v1.0/drives/drive-7/root:/Delivery/Plans:/children"
DOWNLOAD = "https://tenant.sharepoint.example/download/plan-101?tempauth=fixture"
PLAN_CSV = (
    "Project ID,ID,Status Date,Name,Milestone,Start,Finish,Baseline Finish,Actual Finish,% Complete\n"
    "PRJ-101,901,2026-09-10,Pilot sign-off,Yes,2026-09-01,2026-10-15,2026-10-01,,40\n"
    "PRJ-101,902,2026-09-10,Data migration dry run,No,2026-09-05,2026-09-30,2026-09-30,,10\n"
)
LIBRARY = {
    "enabled": True,
    "base_url": "https://graph.example.invalid/v1.0",
    "credential": "graph",
    "libraries": [
        {
            "key": "plans",
            "drive_id": "drive-7",
            "folder": "Delivery/Plans",
            "patterns": ["delivery_plan_*.csv"],
            "download_hosts": [".sharepoint.example"],
        }
    ],
}


def _config(paths, **connectors):
    paths.config.mkdir(parents=True, exist_ok=True)
    data = {"defaults": {"page_size": 50, "pause_seconds": 0, "overlap_minutes": 0}, **connectors}
    (paths.config / "connectors.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


def _item(name: str, modified: str, url: str = DOWNLOAD) -> dict:
    return {
        "name": name,
        "file": {},
        "size": 120,
        "lastModifiedDateTime": modified,
        "@microsoft.graph.downloadUrl": url,
    }


def _library_transport(extra_items: list[dict] | None = None) -> RecordedTransport:
    items = [
        _item("delivery_plan_PRJ-101_2026-09-10.csv", "2026-09-10T08:00:00Z"),
        _item("notes.docx", "2026-09-11T08:00:00Z", "https://tenant.sharepoint.example/download/notes"),
        {"name": "Archive", "folder": {"childCount": 3}, "lastModifiedDateTime": "2026-09-11T08:00:00Z"},
        *(extra_items or []),
    ]
    return RecordedTransport(
        [
            {"path": LISTING, "params": {"$top": 50}, "body": {"value": items}},
            {"download": DOWNLOAD, "text": PLAN_CSV},
        ]
    )


def _milestones(paths, task_ids: tuple[str, ...]) -> list[tuple]:
    conn = db.connect(paths.db, readonly=True)
    try:
        marks = ", ".join("?" for _ in task_ids)
        return [
            tuple(r)
            for r in conn.execute(
                f"SELECT task_id, status_date, name, is_milestone, finish_date, baseline_finish, percent_complete "
                f"FROM delivery_milestone WHERE project_id = 'PRJ-101' AND task_id IN ({marks}) ORDER BY task_id",
                task_ids,
            )
        ]
    finally:
        conn.close()


def test_overview_matches_connector_sources_to_exports(delivery_profile_rw, monkeypatch):
    paths = delivery_profile_rw.paths
    monkeypatch.setenv("SED_CREDENTIAL_GRAPH", FAKE_VALUE)
    monkeypatch.delenv("SED_CREDENTIAL_SERVICENOW", raising=False)
    _config(
        paths,
        sharepoint=LIBRARY,
        servicenow={"enabled": False, "base_url": "https://sn.example.invalid", "credential": "servicenow",
                    "sources": [{"key": "incident", "table": "incident", "file_prefix": "incident",
                                 "fields": ["number"]}]},
    )  # fmt: skip
    result = SourcesOut.model_validate(overview(paths))
    files = {f.mapping: f for f in result.files}
    assert files["servicenow_incident"].connector_sources == ["servicenow/incident"]
    assert files["delivery_plan"].connector_sources == ["sharepoint/plans"]
    assert files["delivery_plan"].module == "delivery" and files["delivery_plan"].last_imported_at
    assert files["delivery_raid"].connector_sources == []  # file only until a source is configured
    connectors = {c.connector: c for c in result.connectors}
    assert connectors["servicenow"].reason == "not enabled in connectors.yaml"
    assert connectors["sharepoint"].credential_found_in == "environment"
    assert connectors["sharepoint"].reason == "connectors pull real systems: use the real profile"
    assert [s.kind for s in connectors["sharepoint"].sources] == ["library"]
    assert result.upload.needs_confirmation and ".zip" in result.upload.suffixes
    assert FAKE_VALUE not in result.model_dump_json()


def test_library_pull_imports_only_the_pulled_files_and_an_upload_of_them_matches(delivery_profile_rw, monkeypatch):
    paths = delivery_profile_rw.paths
    monkeypatch.setenv("SED_CREDENTIAL_GRAPH", FAKE_VALUE)
    _config(paths, sharepoint=LIBRARY)
    stray = paths.inbox / "delivery_plan_unrelated.csv"
    paths.inbox.mkdir(parents=True, exist_ok=True)
    stray.write_text(PLAN_CSV.replace("PRJ-101,901", "PRJ-101,999"), encoding="utf-8")

    transport = _library_transport()
    result = pull_and_import(paths, "sharepoint", transport=transport, now=NOW, allow_synthetic=True)
    source = result["pull"]["sources"][0]
    assert source["files"] == ["delivery_plan_PRJ-101_2026-09-10.csv"] and source["rows"] == 1
    assert source["watermark"] == "2026-09-10T08:00:00+00:00"
    assert result["import"]["summary"]["imported"] == 1 and result["import"]["summary"]["errors"] == 0
    download_headers = [
        h for (url, _), h in zip(transport.calls, transport.headers_seen, strict=True) if url == DOWNLOAD
    ]
    assert download_headers == [{}]  # the pre-authenticated link never gets the bearer token
    rows = _milestones(paths, ("901", "902"))
    assert rows == [
        ("901", "2026-09-10", "Pilot sign-off", 1, "2026-10-15", "2026-10-01", 40.0),
        ("902", "2026-09-10", "Data migration dry run", 0, "2026-09-30", "2026-09-30", 10.0),
    ]
    assert stray.is_file() and not _milestones(paths, ("999",))  # other inbox files are left alone

    # The same export uploaded by hand is the same data: recognised by content and not imported twice.
    uploaded = upload_and_import(paths, "Plan copy.csv", PLAN_CSV.encode("utf-8"), synthetic_ok=True)
    assert uploaded["import"]["summary"]["imported"] == 0
    assert uploaded["import"]["skipped"] == [{"file": "Plan_copy.csv", "reason": "already imported (same sha256)"}]
    assert _milestones(paths, ("901", "902")) == rows

    # Next pull: nothing changed after the watermark, nothing downloaded.
    again = pull_and_import(paths, "sharepoint", transport=_library_transport(), now=NOW, allow_synthetic=True)
    assert again["pull"]["sources"][0]["files"] == [] and again["import"] is None


def test_library_download_hosts_are_enforced(delivery_profile_rw, monkeypatch):
    paths = delivery_profile_rw.paths
    monkeypatch.setenv("SED_CREDENTIAL_GRAPH", FAKE_VALUE)
    _config(paths, sharepoint=LIBRARY)
    evil = "https://attacker.example/steal?file=plan"
    transport = RecordedTransport(
        [
            {"path": LISTING, "params": {"$top": 50},
             "body": {"value": [_item("delivery_plan_PRJ-101_2026-09-11.csv", "2026-09-11T08:00:00Z", evil)]}},
        ]
    )  # fmt: skip
    with pytest.raises(PreconditionFailed, match="not an allowed https host"):
        pull_and_import(paths, "sharepoint", transport=transport, now=NOW, allow_synthetic=True)
    conn = db.connect(paths.db, readonly=True)
    try:
        assert db.get_meta(conn, "connector.sharepoint.plans.watermark") is None
    finally:
        conn.close()
    client = Client(RecordedTransport([]), "https://graph.example.invalid", {})
    for url in ("http://tenant.sharepoint.example/x", "https://sharepoint.example.evil/x"):
        with pytest.raises(PreconditionFailed):
            client.download(url, [".sharepoint.example"], 1000)


def test_api_sources_upload_and_pull(delivery_profile_rw, monkeypatch):
    paths = delivery_profile_rw.paths
    client = api_client(paths)
    body = SourcesOut.model_validate(client.get("/api/sources").json())
    assert body.profile == paths.profile and any(f.mapping == "delivery_plan" for f in body.files)

    csv_bytes = PLAN_CSV.replace("2026-09-10", "2026-09-09").encode("utf-8")
    headers = {"Content-Type": "application/octet-stream"}
    no_token = api_client(paths, send_token=False)
    assert_envelope(no_token.post("/api/imports/upload?name=delivery_plan_x.csv", content=csv_bytes), 403, "forbidden")
    refused = client.post("/api/imports/upload?name=delivery_plan_x.csv", content=csv_bytes, headers=headers)
    assert "synthetic profile" in assert_envelope(refused, 412, "precondition")["error"]["message"]
    assert_envelope(client.post("/api/imports/upload?name=run.ps1&synthetic_ok=true", content=b"x"), 422, "validation")

    started = client.post(
        "/api/imports/upload?name=delivery_plan_PRJ-101_2026-09-09.csv&synthetic_ok=true",
        content=csv_bytes,
        headers=headers,
    )
    assert started.status_code == 200, started.text
    job = JobOut.model_validate(started.json())
    client.app.state.jobs.wait(job.job_id, timeout=120)
    done = JobOut.model_validate(client.get(f"/api/jobs/{job.job_id}").json())
    assert done.status == "done", done.error
    assert done.result["upload"]["file"] == "delivery_plan_PRJ-101_2026-09-09.csv"
    assert done.result["import"]["summary"]["imported"] == 1
    assert [r[1] for r in _milestones(paths, ("901",))] == ["2026-09-09"]

    assert_envelope(client.post("/api/sources/crm/pull", json={}), 422, "validation")
    refusal = client.post("/api/sources/sharepoint/pull", json={})
    assert "not enabled" in assert_envelope(refusal, 412, "precondition")["error"]["message"]

    calls = []
    monkeypatch.setattr("sed.sources.pull_refusal", lambda p, c: None)
    monkeypatch.setattr(
        "sed.sources.pull_and_import",
        lambda p, c, source=None, full=False: (
            calls.append((c, source, full)) or {"pull": {"sources": []}, "import": None}
        ),
    )
    started = client.post("/api/sources/sharepoint/pull", json={"source": "plans", "full": True})
    job = JobOut.model_validate(started.json())
    client.app.state.jobs.wait(job.job_id, timeout=60)
    assert JobOut.model_validate(client.get(f"/api/jobs/{job.job_id}").json()).status == "done"
    assert calls == [("sharepoint", "plans", True)]


def test_sources_cli(delivery_profile_rw):
    import json

    from typer.testing import CliRunner

    from sed.cli import app

    result = CliRunner().invoke(app, ["sources", "--data-dir", str(delivery_profile_rw.paths.data_dir), "--json"])
    assert result.exit_code == 0, result.stdout
    body = json.loads(result.stdout.strip().splitlines()[-1])
    assert {"connectors", "files", "upload"} <= set(body)


def test_snapshot_sources_pull_whole_tables_and_incremental_ones_are_flagged(delivery_profile_rw, monkeypatch):
    from sed.connectors.pull import pull

    paths = delivery_profile_rw.paths
    monkeypatch.setenv("SED_CREDENTIAL_SERVICENOW", FAKE_VALUE)
    apps = {"key": "apps", "table": "cmdb_ci_business_app", "file_prefix": "cmdb_ci_business_app", "fields": ["number"]}
    base = {"enabled": True, "base_url": "https://sn.example.invalid", "credential": "servicenow"}
    _config(paths, servicenow={**base, "sources": [apps]})
    source = SourcesOut.model_validate(overview(paths)).connectors[0].sources[0]
    assert source.mappings == ["cmdb_ci_business_app"] and "set `snapshot: true`" in (source.warning or "")

    _config(paths, servicenow={**base, "sources": [{**apps, "snapshot": True}]})
    assert SourcesOut.model_validate(overview(paths)).connectors[0].sources[0].warning is None
    conn = db.connect(paths.db)
    try:
        with db.write_tx(conn):
            db.set_meta(conn, "connector.servicenow.apps.watermark", "2026-09-01T00:00:00+00:00")
    finally:
        conn.close()
    table = "/api/now/table/cmdb_ci_business_app"
    rows = [
        {"number": {"value": f"APM{i}", "display_value": f"APM{i}"}, "sys_updated_on": {"value": ""}} for i in (1, 2)
    ]
    transport = RecordedTransport([{"path": table, "params": {"sysparm_offset": 0}, "body": {"result": rows}}])
    pulled = pull(paths, "servicenow", transport=transport, now=NOW, allow_synthetic=True, dry_run=True)
    assert pulled["sources"][0]["from"] is None  # the watermark is ignored: the whole table
    assert "sys_updated_on>=" not in transport.calls[0][1]["sysparm_query"]

    capped = RecordedTransport([{"path": table, "params": {"sysparm_offset": 0}, "body": {"result": rows}}])
    _config(paths, servicenow={**base, "sources": [{**apps, "snapshot": True}]})
    data = yaml.safe_load((paths.config / "connectors.yaml").read_text(encoding="utf-8"))
    data["defaults"]["max_rows"] = 1
    (paths.config / "connectors.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(PreconditionFailed, match="snapshot source and reached max_rows"):
        pull(paths, "servicenow", transport=capped, now=NOW, allow_synthetic=True)


def test_every_export_is_in_the_source_matrix(delivery_profile):
    from sed.ingest.mapping import mapping_names
    from tests.conftest import REPO

    text = (REPO / "docs" / "sources.md").read_text(encoding="utf-8")
    missing = [name for name in mapping_names(delivery_profile.paths) if f"| `{name}` |" not in text]
    assert not missing, f"docs/sources.md has no row for: {missing}"
