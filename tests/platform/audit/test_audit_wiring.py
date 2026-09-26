"""Every recorded action, through the real routes and commands: downloads, report builds, clears, uploads, pulls, AI
runs, sign-in settings, SED's own start and stop; `sed audit verify` and doctor; and refusal when the trail is down."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sed import bootstrap
from sed.api import serve
from sed.api.app import create_app
from sed.api.models import JobOut
from sed.audit import store
from sed.audit.record import audit_path
from sed.cli import app as cli_app
from sed.paths import get_paths
from tests.fixtures.api import api_client
from tests.platform.api.conftest import assert_envelope
from tests.platform.audit.test_record import entries

runner = CliRunner()


def run(*args: str) -> tuple[int, dict]:
    result = runner.invoke(cli_app, [*args, "--json"])
    lines = [ln for ln in result.stdout.strip().splitlines() if ln.strip()]
    assert lines, result.output
    return result.exit_code, json.loads(lines[-1])


def _broken_trail(monkeypatch) -> None:
    def broken(path, row, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(store, "append", broken)


@pytest.fixture
def synthetic(data_root, monkeypatch):
    monkeypatch.setenv("USERNAME", "synthetic-user")
    paths = get_paths("synthetic")
    bootstrap.init_profile(paths, write_claude_settings=False)
    return paths


def test_a_ticket_workbook_download_is_recorded_or_refused(ops_profile_rw, monkeypatch):
    monkeypatch.setenv("USERNAME", "synthetic-user")
    paths = ops_profile_rw.paths
    client = api_client(paths)
    response = client.get("/api/ops/tickets-export.xlsx?limit=5&priority=1&priority=2&q=vpn")
    assert response.status_code == 200, response.text[:300]
    (entry,) = [e for e in entries(paths) if e["action"] == "download"]
    assert (entry["actor"], entry["method"], entry["verified"]) == ("windows:synthetic-user", "developer_mode", 0)
    assert entry["detail"]["filters"] == {"q": "vpn", "priority": [1, 2], "sort": "opened_desc"}
    assert entry["detail"]["sha256"] == hashlib.sha256(response.content).hexdigest()
    assert entry["summary"] == f"Downloaded {entry['detail']['rows']} tickets as a workbook."

    for old in (paths.out / "exports").iterdir():
        old.unlink()
    _broken_trail(monkeypatch)
    refused = client.get("/api/ops/tickets-export.xlsx?limit=5")
    assert "audit trail" in assert_envelope(refused, 412, "precondition")["error"]["message"]
    assert list((paths.out / "exports").iterdir()) == []  # nothing left behind to take


def test_a_report_build_and_its_download_are_recorded(ops_profile_rw):
    paths = ops_profile_rw.paths
    client = api_client(paths)
    started = client.post(
        "/api/reports/build", json={"report": "weekly", "period": "2026-W35", "formats": ["md"], "ai_mode": "none"}
    )
    job_id = started.json()["job_id"]
    client.app.state.jobs.wait(job_id, timeout=120)
    job = JobOut.model_validate(client.get(f"/api/jobs/{job_id}").json())
    assert job.status == "done", job.error
    artifact = job.result["artifacts"][0]
    first = client.get(f"/api/reports/artifacts/{artifact['artifact_id']}/file")
    assert first.status_code == 200
    report_file = next(paths.out.rglob(artifact["file_name"]))
    report_file.write_bytes(report_file.read_bytes() + b"\nedited after it was built\n")
    second = client.get(f"/api/reports/artifacts/{artifact['artifact_id']}/file")
    rows = [e for e in entries(paths) if e["action"] in ("report_build", "download")]
    build_started, build_done, download, download_edited = rows
    assert (build_started["outcome"], build_done["outcome"]) == ("started", "done")
    assert build_started["correlation_id"] == build_done["correlation_id"]
    assert build_done["summary"] == "Build the weekly report for 2026-W35: built MD."
    assert download["summary"] == "Downloaded the weekly report for 2026-W35 as MD."
    assert download["detail"]["report_key"] == "weekly" and download["target_id"] == artifact["file_name"]
    assert (
        download["detail"]["sha256"] == hashlib.sha256(first.content).hexdigest()
        and "built_sha256" not in download["detail"]
    )
    # The fingerprint is of the file as handed out, and says so when it changed since it was built.
    assert download_edited["detail"]["sha256"] == hashlib.sha256(second.content).hexdigest()
    assert download_edited["detail"]["built_sha256"] == download["detail"]["sha256"]


def test_clearing_data_is_recorded_from_the_dashboard_and_the_command_line(ops_profile_rw):
    paths = ops_profile_rw.paths
    client = api_client(paths)
    assert client.post("/api/data/clear", json={"source": "jira"}).status_code == 200
    code, out = run("data", "clear", "confluence", "--yes", "--profile", "synthetic", "--data-dir", str(paths.data_dir))
    assert code == 0, out
    rows = [e for e in entries(paths) if e["action"] == "clear"]
    assert [(e["outcome"], e["channel"], e["target_id"]) for e in rows] == [
        ("started", "dashboard", "jira"),
        ("done", "dashboard", "jira"),
        ("started", "command_line", "confluence"),
        ("done", "command_line", "confluence"),
    ]
    assert rows[3]["summary"].endswith("rows deleted.") and rows[3]["detail"]["dashboard_signed_in"] is None


def test_an_upload_and_a_pull_are_recorded_with_their_outcome(synthetic, monkeypatch):
    client = api_client(synthetic)
    monkeypatch.setattr(
        "sed.ingest.upload.save_upload",
        lambda paths, name, data, synthetic_ok=False: {
            "path": str(paths.inbox / name),
            "file": name,
            "bytes": len(data),
        },
    )
    imported = {
        "files": [{"file": "a.csv", "status": "completed", "rows_read": 4}],
        "summary": {"imported": 1, "rows_read": 4, "errors": 0},
    }
    monkeypatch.setattr("sed.sources.import_paths", lambda paths, files, synthetic_ok=False: imported)
    started = client.post(
        "/api/imports/upload?name=a.csv&synthetic_ok=true",
        content=b"x,y\n",
        headers={"Content-Type": "application/octet-stream"},
    )
    client.app.state.jobs.wait(started.json()["job_id"], timeout=60)

    monkeypatch.setattr("sed.sources.pull_refusal", lambda p, c: None)
    pulled = {
        "pull": {"sources": [{"source": "incidents", "rows": 7, "files": ["x.csv"]}], "pages": 1},
        "import": imported,
    }
    monkeypatch.setattr("sed.sources.pull_and_import", lambda p, c, source=None, full=False: pulled)
    started = client.post("/api/sources/servicenow/pull", json={})
    client.app.state.jobs.wait(started.json()["job_id"], timeout=60)

    rows = entries(synthetic)
    assert [(e["action"], e["outcome"]) for e in rows] == [
        ("import", "started"),
        ("import", "done"),
        ("pull", "started"),
        ("pull", "done"),
    ]
    assert rows[1]["summary"] == "Upload and import a.csv: 1 file imported, 4 rows read."
    assert rows[3]["summary"] == "Pull from ServiceNow and import: 7 rows pulled; 1 file imported, 4 rows read."


def test_an_upload_is_refused_when_the_trail_is_down(synthetic, monkeypatch):
    stored = []
    monkeypatch.setattr("sed.ingest.upload.save_upload", lambda *a, **k: stored.append(a) or {})
    _broken_trail(monkeypatch)
    client = api_client(synthetic)
    response = client.post(
        "/api/imports/upload?name=a.csv&synthetic_ok=true",
        content=b"x",
        headers={"Content-Type": "application/octet-stream"},
    )
    assert_envelope(response, 412, "precondition")
    assert stored == []  # the file was never stored


def test_an_ai_run_is_recorded_before_any_packet_is_written(ops_profile_rw, monkeypatch):
    paths = ops_profile_rw.paths
    args = ("ai", "start-run", "sed-triage-batch", "--scope", "new", "--limit", "3", "--profile", "synthetic")
    code, _ = run(*args, "--dry-run", "--data-dir", str(paths.data_dir))
    assert code == 0 and [e for e in entries(paths) if e["action"] == "ai_run"] == []
    code, plan = run(*args, "--data-dir", str(paths.data_dir))
    assert code == 0, plan
    started, done = [e for e in entries(paths) if e["action"] == "ai_run"]
    assert started["summary"] == "Prepare data for Claude (sed-triage-batch)"
    assert done["summary"].startswith("Prepare data for Claude (sed-triage-batch): 3 items in 1 batch handed to Claude")
    assert done["detail"]["run_id"] == plan["run_id"]

    runs_before = sorted(p.name for p in paths.runs.iterdir())
    _broken_trail(monkeypatch)
    code, refused = run(*args, "--data-dir", str(paths.data_dir))
    assert code == 4 and "audit trail" in refused["error"]["message"]
    assert sorted(p.name for p in paths.runs.iterdir()) == runs_before  # no run folder, no packet


def test_sign_in_settings_changes_keep_before_and_after(synthetic, monkeypatch):
    code, _ = run("auth", "allow", "Owner@Example.com")
    assert code == 0
    started, done = entries(synthetic)
    assert started["action"] == "sign_in_settings" and started["summary"] == "Allowed owner@example.com to sign in"
    assert started["outcome"] == "started" and done["outcome"] == "done"
    assert started["changes"] == [{"field": "allow.emails", "before": None, "after": ["owner@example.com"]}]

    local = synthetic.config / "auth.yaml"
    before = local.read_bytes()
    monkeypatch.setattr("sed.auth.settings._replace", lambda target, data: (_ for _ in ()).throw(OSError("in use")))
    code, _ = run("auth", "allow", "second@example.com")
    assert code == 4 and local.read_bytes() == before
    assert [e["outcome"] for e in entries(synthetic)][-2:] == ["started", "failed"]  # the attempt, and that it failed

    monkeypatch.undo()
    _broken_trail(monkeypatch)
    code, _ = run("auth", "allow", "third@example.com")
    assert code == 4 and local.read_bytes() == before  # not kept: the change could not be recorded


def test_sed_start_and_stop_are_recorded_and_starting_needs_the_trail(synthetic, monkeypatch):
    app = create_app(synthetic, token="t")
    stale = synthetic.audit / "dashboard-session.json"
    stale.write_text('{"actor": "someone@example.com"}', encoding="utf-8")  # left by a launch that crashed
    serve.record_start(synthetic, app, 18431, developer_mode=False)
    serve.record_stop(synthetic, 18431)
    first, second = entries(synthetic)
    assert not stale.exists()
    assert first["summary"] == "SED started in developer mode: nobody signs in."
    assert first["detail"]["mode"] == "developer" and second["action"] == "serve_stop"
    _broken_trail(monkeypatch)
    with pytest.raises(Exception, match="audit trail"):
        serve.record_start(synthetic, app, 18431, developer_mode=False)


def test_verify_and_doctor_see_tampering(synthetic):
    run("auth", "allow", "owner@example.com")
    run("auth", "allow", "second@example.com")
    code, result = run("audit", "verify")
    assert code == 0 and result["intact"] and result["entries"] == 4  # each change: its attempt and its outcome
    code, doctor = run("doctor")
    check = next(c for c in doctor["checks"] if c["name"] == "audit_trail_intact")
    assert check["status"] == "ok"

    conn = sqlite3.connect(str(audit_path(synthetic)))
    try:
        conn.execute("DROP TRIGGER audit_entry_never_changed")
        conn.execute("UPDATE audit_entry SET actor = 'someone-else@example.com' WHERE seq = 1")
        conn.commit()
    finally:
        conn.close()
    code, result = run("audit", "verify")
    assert code == 4 and result["intact"] is False and result["first_break"] == 1
    code, doctor = run("doctor")
    check = next(c for c in doctor["checks"] if c["name"] == "audit_trail_intact")
    assert check["status"] == "warn" and "changed outside SED" in check["detail"]


def test_moving_the_data_folder_carries_the_newest_entries(synthetic, tmp_path):
    from sed.audit.record import record
    from sed.auth.actor import command_line_actor
    from sed.relocate import move_data_root

    keep_open = store.connect(audit_path(synthetic))  # a second SED process keeps the newest entries in the -wal file
    try:
        for n in range(3):
            record(synthetic, command_line_actor(), "import", summary=f"Import {n}.csv")
        wal = audit_path(synthetic).with_name("audit.db-wal")
        assert wal.is_file() and wal.stat().st_size > 0
        out = move_data_root(tmp_path / "moved")
    finally:
        keep_open.close()
    assert out["audit_logs"] == [{"profile": "synthetic", "entries": 3}]
    moved = store.verify(tmp_path / "moved" / "synthetic" / "audit" / "audit.db")
    assert (moved["entries"], moved["intact"]) == (3, True)


def test_the_data_move_itself_is_on_record_in_the_new_place(synthetic, tmp_path):
    code, out = run("data", "move", "--to", str(tmp_path / "moved"))
    assert code == 0, out
    moved_trail = tmp_path / "moved" / "synthetic" / "audit" / "audit.db"
    conn = store.connect(moved_trail, readonly=True)
    try:
        rows = [dict(r) for r in conn.execute("SELECT action, outcome, summary FROM audit_entry ORDER BY seq")]
    finally:
        conn.close()
    assert [(r["action"], r["outcome"]) for r in rows] == [("data_move", "started"), ("data_move", "done")]
    assert rows[1]["summary"].endswith("files copied and verified.")


def test_the_column_profile_for_claude_is_on_record(synthetic, tmp_path):
    export = tmp_path / "incident_sample.csv"
    export.write_text("Number,Short description\nINC0001,VPN drops\n", encoding="utf-8")
    code, draft = run("mappings", "draft", str(export))
    assert code == 0, draft
    started, done = [e for e in entries(synthetic) if e["action"] == "ai_run"]
    assert started["summary"] == "Prepare the column profile of incident_sample.csv for Claude (sed-map-export)"
    assert done["detail"]["draft"] == Path(draft["draft_dir"]).name
    assert "VPN drops" not in json.dumps([started, done])


def test_an_inbox_clean_up_that_stops_half_way_says_what_it_deleted(synthetic, monkeypatch):
    import os
    import shutil

    for name in ("2026-01-01_a", "2026-01-02_b"):
        folder = synthetic.processed / name
        folder.mkdir(parents=True)
        os.utime(folder, (0, 0))
    real_rmtree = shutil.rmtree
    calls = []

    def flaky(path, *args, **kwargs):
        calls.append(path)
        if len(calls) == 2:
            raise PermissionError("in use")
        real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", flaky)
    code, _ = run("inbox", "prune", "--older-than", "30d")
    assert code == 4
    started, failed = [e for e in entries(synthetic) if e["action"] == "clear"]
    assert started["detail"]["folders"] == ["2026-01-01_a", "2026-01-02_b"]
    assert failed["outcome"] == "failed" and failed["detail"]["deleted"] == ["2026-01-01_a"]
