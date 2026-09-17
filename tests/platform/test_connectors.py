"""Connectors against recorded synthetic API responses (no network): ServiceNow and Jira pulls write files the normal
import reads back to the same values, watermarks advance and bound the next pull, SharePoint pages follow nextLink,
Confluence becomes an HTML export folder; secrets never leak; refusals; weekly schedule files."""

from __future__ import annotations

import json
import xml.dom.minidom
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from sed import db
from sed.connectors.http import Client, RecordedTransport, Response
from sed.connectors.pull import pull, status, watermark_key
from sed.connectors.schedule import write_schedule
from sed.errors import PreconditionFailed, ValidationFailed
from sed.ingest.loader import ImportOptions, run_import

FAKE_VALUE = "fixture-not-a-real-secret"
NOW = datetime(2026, 9, 7, 6, 0, tzinfo=UTC)


def _config(paths, **connectors):
    paths.config.mkdir(parents=True, exist_ok=True)
    data = {"defaults": {"page_size": 2, "pause_seconds": 0, "overlap_minutes": 30}, **connectors}
    (paths.config / "connectors.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


def _sn_record(number: str, opened: str, updated: str, state: str = "Resolved") -> dict:
    def both(value, display=None):
        return {"value": value, "display_value": value if display is None else display}

    return {
        "number": both(number),
        "opened_at": both(opened, "07/09/2026 display format"),
        "sys_updated_on": both(updated),
        "state": both("6", state),
        "priority": both("3", "3 - Moderate"),
        "short_description": both("Posting interface timeout on the batch run"),
        "assignment_group": both("abc123", "APP-ERP-L2"),
        "caller_id": both("def456", "Fixture Caller"),
    }


SN_SOURCE = {
    "enabled": True,
    "base_url": "https://sn.example.invalid",
    "auth": "basic",
    "user": "sed_readonly",
    "credential": "servicenow",
    "sources": [
        {
            "key": "incident",
            "table": "incident",
            "file_prefix": "incident",
            "fields": ["number", "opened_at", "sys_updated_on", "state", "priority", "short_description",
                       "assignment_group", "caller_id"],
            "datetime_fields": ["opened_at", "sys_updated_on"],
            "source_tz": "Europe/Paris",
        }
    ],
}  # fmt: skip


def test_servicenow_pull_imports_and_advances_the_watermark(ops_profile_rw, monkeypatch):
    paths = ops_profile_rw.paths
    monkeypatch.setenv("SED_CREDENTIAL_SERVICENOW", FAKE_VALUE)
    _config(paths, servicenow=SN_SOURCE)
    page = {"path": "/api/now/table/incident", "params": {"sysparm_limit": 2}}
    transport = RecordedTransport(
        [
            {**page, "params": {**page["params"], "sysparm_offset": 0}, "status": 429, "headers": {"Retry-After": "1"}},
            {**page, "params": {**page["params"], "sysparm_offset": 0}, "body": {"result": [
                _sn_record("INC9900001", "2026-09-01 06:30:00", "2026-09-02 10:00:00"),
                _sn_record("INC9900002", "2026-09-01 22:15:00", "2026-09-03 08:00:00"),
            ]}},
            {**page, "params": {**page["params"], "sysparm_offset": 2}, "body": {"result": [
                _sn_record("INC9900003", "2026-09-02 12:00:00", "2026-09-04 16:45:00", state="In Progress"),
            ]}},
        ]
    )  # fmt: skip
    slept: list[float] = []
    result = pull(paths, "servicenow", transport=transport, now=NOW, allow_synthetic=True, sleep=slept.append)
    entry = result["sources"][0]
    assert entry["rows"] == 3 and entry["file"] == "incident_pull_20260907T060000.csv" and slept == [1.0]
    assert entry["watermark"] == "2026-09-04T16:45:00+00:00" and result["next"] == "uv run sed import --inbox"
    first_query = transport.calls[0][1]
    assert "sys_updated_on>=" not in first_query["sysparm_query"] and first_query["sysparm_display_value"] == "all"
    auth = transport.headers_seen[0]["Authorization"]
    assert auth.startswith("Basic ") and FAKE_VALUE not in auth and FAKE_VALUE not in json.dumps(result)

    csv_path = paths.inbox / entry["file"]
    text = csv_path.read_text(encoding="utf-8-sig")
    assert (
        text.splitlines()[0]
        == "number,opened_at,sys_updated_on,state,priority,short_description,assignment_group,caller_id"
    )
    assert "INC9900002,2026-09-02 00:15:00" in text  # 22:15 UTC is 00:15 the next day in Paris
    imported = run_import(paths, ImportOptions(files=[csv_path], allow_unmanifested=True, move_files=False))
    assert imported["summary"]["errors"] == 0 and imported["files"][0]["mapping"] == "servicenow_incident"
    conn = db.connect(paths.db, readonly=True)
    try:
        row = conn.execute("SELECT opened_at, state, priority FROM ticket WHERE number = 'INC9900002'").fetchone()
        assert (row["opened_at"], row["state"], row["priority"]) == ("2026-09-01T22:15:00Z", "Resolved", 3)
        assert db.get_meta(conn, watermark_key("servicenow", "incident")) == "2026-09-04T16:45:00+00:00"
    finally:
        conn.close()

    second = RecordedTransport([{**page, "params": {"sysparm_offset": 0}, "body": {"result": []}}])
    again = pull(paths, "servicenow", transport=second, now=NOW, allow_synthetic=True, dry_run=True)
    assert "sys_updated_on>=2026-09-04 16:15:00" in second.calls[0][1]["sysparm_query"]  # watermark - 30 min overlap
    assert again["sources"][0]["file"] is None and again["next"] is None

    listed = status(paths)["connectors"][0]
    assert listed["credential_found_in"] == "environment" and listed["sources"][0]["watermark"].startswith("2026-09-04")
    assert FAKE_VALUE not in json.dumps(status(paths))


def test_jira_pull_repeats_multi_value_columns(ops_profile_rw, monkeypatch):
    paths = ops_profile_rw.paths
    monkeypatch.setenv("SED_CREDENTIAL_JIRA", FAKE_VALUE)
    source = {"key": "issues", "jql": "project = DEMO", "story_points_field": "customfield_10016"}
    _config(
        paths,
        jira={"enabled": True, "base_url": "https://jira.example.invalid", "credential": "jira", "sources": [source]},
    )
    issue = {
        "key": "DEMO-900",
        "fields": {
            "project": {"key": "DEMO", "name": "Demo project"}, "summary": "Invoice export button",
            "issuetype": {"name": "Story"}, "status": {"name": "Done", "statusCategory": {"name": "Done"}},
            "priority": {"name": "Medium"}, "assignee": {"displayName": "Fixture Dev"},
            "created": "2026-08-20T09:00:00.000+0200", "updated": "2026-09-01T10:30:00.000+0200",
            "resolutiondate": "2026-09-01T10:30:00.000+0200", "labels": ["finance", "export"],
            "components": [{"name": "UI"}], "fixVersions": [], "customfield_10016": 3.0,
        },
    }  # fmt: skip
    transport = RecordedTransport(
        [{"path": "/rest/api/2/search", "params": {"startAt": 0}, "body": {"issues": [issue]}}]
    )
    result = pull(paths, "jira", transport=transport, now=NOW, allow_synthetic=True)
    path = paths.inbox / result["sources"][0]["file"]
    header = path.read_text(encoding="utf-8-sig").splitlines()[0].split(",")
    assert header.count("Labels") == 2 and header.count("Component/s") == 1 and header[0] == "Issue key"
    assert transport.headers_seen[0]["Authorization"] == f"Bearer {FAKE_VALUE}"
    assert transport.calls[0][1]["jql"] == "(project = DEMO) ORDER BY updated ASC"
    imported = run_import(paths, ImportOptions(files=[path], allow_unmanifested=True, move_files=False))
    assert imported["summary"]["errors"] == 0
    conn = db.connect(paths.db, readonly=True)
    try:
        row = conn.execute(
            "SELECT labels_json, story_points, resolved FROM work_item WHERE issue_key = 'DEMO-900'"
        ).fetchone()
    finally:
        conn.close()
    assert json.loads(row["labels_json"]) == ["finance", "export"] and row["story_points"] == 3.0
    assert row["resolved"] == "2026-09-01T08:30:00Z"


def test_sharepoint_follows_next_link_and_confluence_writes_an_export_folder(ops_profile_rw, monkeypatch):
    paths = ops_profile_rw.paths
    monkeypatch.setenv("SED_CREDENTIAL_GRAPH", FAKE_VALUE)
    monkeypatch.setenv("SED_CREDENTIAL_CONFLUENCE", FAKE_VALUE)
    _config(
        paths,
        sharepoint={"enabled": True, "base_url": "https://graph.example.invalid/v1.0", "credential": "graph",
                    "sources": [{"key": "contracts", "site_id": "site-1", "list_id": "list-9",
                                 "file_prefix": "contracts",
                                 "columns": {"Contract No": "ContractNo", "End Date": "EndDate"}}]},
        confluence={"enabled": True, "base_url": "https://wiki.example.invalid", "credential": "confluence",
                    "sources": [{"key": "kb", "space": "DEMO"}]},
    )  # fmt: skip
    items = "/v1.0/sites/site-1/lists/list-9/items"
    sharepoint = RecordedTransport(
        [
            {"path": items, "params": {"$top": 2}, "body": {
                "value": [{"fields": {"ContractNo": "C-1", "EndDate": "2027-01-31"}}],
                "@odata.nextLink": f"https://graph.example.invalid{items}?$top=2&$skiptoken=abc"}},
            {"path": items, "params": {"$skiptoken": "abc"}, "body": {"value": [{"fields": {"ContractNo": "C-2"}}]}},
        ]
    )  # fmt: skip
    result = pull(paths, "sharepoint", transport=sharepoint, now=NOW, allow_synthetic=True)
    text = (paths.inbox / result["sources"][0]["file"]).read_text(encoding="utf-8-sig")
    assert text.splitlines() == ["Contract No,End Date", "C-1,2027-01-31", "C-2,"]
    assert result["sources"][0]["watermark"] is None  # lists are full snapshots

    page = {"id": "770001", "title": "Reset a locked account", "version": {"when": "2026-09-02T09:00:00.000+0000"},
            "history": {"createdBy": {"displayName": "Fixture Author"}},
            "metadata": {"labels": {"results": [{"name": "kb"}, {"name": "access"}]}},
            "body": {"storage": {"value": "<p>Open the admin console and unlock the account.</p>"}}}  # fmt: skip
    wiki = RecordedTransport(
        [{"path": "/rest/api/content/search", "params": {"start": 0}, "body": {"results": [page]}}]
    )
    folder = (
        paths.inbox / pull(paths, "confluence", transport=wiki, now=NOW, allow_synthetic=True)["sources"][0]["file"]
    )
    assert (folder / "index.html").is_file() and (folder / "Reset-a-locked-account_770001.html").is_file()
    imported = run_import(paths, ImportOptions(files=[folder], allow_unmanifested=True, move_files=False))
    assert imported["summary"]["errors"] == 0
    conn = db.connect(paths.db, readonly=True)
    try:
        doc = conn.execute("SELECT space_key, title, labels_json FROM doc_page WHERE page_id = '770001'").fetchone()
    finally:
        conn.close()
    assert (doc["space_key"], doc["title"]) == ("DEMO", "Reset a locked account")
    assert json.loads(doc["labels_json"]) == ["kb", "access"]


def test_refusals_and_credential_errors(ops_profile_rw, monkeypatch):
    paths = ops_profile_rw.paths
    with pytest.raises(PreconditionFailed, match="not enabled"):
        pull(paths, "servicenow", allow_synthetic=True)
    _config(paths, servicenow=SN_SOURCE)
    with pytest.raises(PreconditionFailed, match="synthetic one refuses"):
        pull(paths, "servicenow")
    monkeypatch.delenv("SED_CREDENTIAL_SERVICENOW", raising=False)
    with pytest.raises(PreconditionFailed, match="SED_CREDENTIAL_SERVICENOW"):
        pull(paths, "servicenow", allow_synthetic=True, transport=RecordedTransport([]))
    monkeypatch.setenv("SED_CREDENTIAL_SERVICENOW", FAKE_VALUE)
    denied = RecordedTransport([{"path": "/api/now/table/incident", "status": 401}])
    with pytest.raises(PreconditionFailed) as refused:
        pull(paths, "servicenow", allow_synthetic=True, transport=denied)
    assert "refused the credentials" in refused.value.message and FAKE_VALUE not in refused.value.message
    assert "sysparm" not in refused.value.message  # no query strings in messages
    with pytest.raises(ValidationFailed, match="no source 'problem'"):
        pull(paths, "servicenow", source="problem", allow_synthetic=True, transport=RecordedTransport([]))
    with pytest.raises(ValidationFailed, match="Unknown connector"):
        pull(paths, "crm")
    _config(paths, servicenow={**SN_SOURCE, "base_url": "http://sn.example.invalid"})
    with pytest.raises(ValidationFailed, match=r"Invalid connectors\.yaml"):
        pull(paths, "servicenow", allow_synthetic=True)
    client = Client(RecordedTransport([{"path": "/x", "status": 503, "repeat": True}]), "https://h.example.invalid", {})
    client.sleep = lambda _: None
    with pytest.raises(PreconditionFailed, match="returned HTTP 503"):
        client.get("/x", {})
    assert client.pages == 5
    assert Response(200, {}, None).status == 200


def test_schedule_files(ops_profile_rw, monkeypatch):
    paths = ops_profile_rw.paths
    _config(paths, servicenow=SN_SOURCE)
    result = write_schedule(paths, day="tue", time="06:30", analyze=True)
    script = Path(result["script"]).read_text(encoding="utf-8-sig")
    assert "uv run sed pull servicenow --profile synthetic --json" in script
    assert "uv run sed import --inbox --profile synthetic --json" in script and "claude -p" in script
    assert "Do not approve or reject anything." in script and FAKE_VALUE not in script
    task = xml.dom.minidom.parseString(Path(result["task_xml"]).read_text(encoding="utf-16"))
    assert (
        task.getElementsByTagName("Tuesday")
        and "T06:30:00" in task.getElementsByTagName("StartBoundary")[0].firstChild.data
    )
    assert task.getElementsByTagName("LogonType")[0].firstChild.data == "InteractiveToken"
    assert result["register"].startswith('schtasks /Create /TN "SED weekly" /XML') and result["connectors"] == [
        "servicenow"
    ]
    quiet = write_schedule(paths, day="MON", time="07:00", analyze=False)
    assert "claude -p" not in Path(quiet["script"]).read_text(encoding="utf-8-sig")
    with pytest.raises(ValidationFailed):
        write_schedule(paths, day="XYZ", time="07:00", analyze=False)
    with pytest.raises(ValidationFailed):
        write_schedule(paths, day="MON", time="25:00", analyze=False)
