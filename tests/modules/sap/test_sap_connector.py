"""`sed pull sap` against recorded synthetic SAP Gateway OData responses (no network): ChaRM changes and per-system
IDocs become the exports the sap mappings read, import to the same rows, and bound the next pull with $filter."""

from __future__ import annotations

from datetime import UTC, datetime

import yaml

from sed import db
from sed.connectors.http import RecordedTransport
from sed.connectors.pull import pull, status
from sed.connectors.sources import odata_time
from sed.ingest.loader import ImportOptions, run_import

FAKE_VALUE = "fixture-not-a-real-secret"
NOW = datetime(2026, 9, 7, 6, 0, tzinfo=UTC)
CHANGES = "/sap/opu/odata/sap/ZSED_CHARM_SRV/ChangeDocuments"
IDOCS = "/sap/opu/odata/sap/ZSED_IDOC_SRV/IdocStatus"


def _ms(text: str) -> str:
    return f"/Date({int(datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp() * 1000)})/"


def _config(paths) -> None:
    config = {
        "defaults": {"page_size": 50, "pause_seconds": 0},
        "sap": {
            "enabled": True,
            "base_url": "https://solman.example.invalid",
            "auth": "basic",
            "user": "SED_READONLY",
            "credential": "sap-solman",
            "sources": [
                {
                    "key": "charm_changes",
                    "service_path": CHANGES,
                    "file_prefix": "sap_charm_changes",
                    "updated_property": "ChangedAt",
                    "datetime_columns": ["created_at", "changed_at"],
                    "columns": {"change_id": "ObjectId", "transaction_type": "ProcessType", "title": "Description",
                                "status": "UserStatus", "priority": "Priority", "component": "Component",
                                "created_at": "CreatedAt", "changed_at": "ChangedAt"},
                },
                {
                    "key": "idocs_hp1",
                    "service_path": IDOCS,
                    "base_url": "https://hp1.example.invalid",
                    "credential": "sap-hp1",
                    "file_prefix": "sap_idocs",
                    "updated_property": "StatusChangedAt",
                    "datetime_columns": ["created_at", "status_at"],
                    "constants": {"system_id": "HP1"},
                    "columns": {"docnum": "Docnum", "direction": "Direction", "message_type": "MessageType",
                                "partner_number": "PartnerNumber", "status_code": "Status", "status_text": "StatusText",
                                "created_at": "CreatedAt", "status_at": "StatusChangedAt"},
                },
            ],
        },
    }  # fmt: skip
    paths.config.mkdir(parents=True, exist_ok=True)
    (paths.config / "connectors.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")


def test_sap_odata_pull_imports_changes_and_idocs(sap_profile_rw, monkeypatch):
    paths = sap_profile_rw.paths
    _config(paths)
    monkeypatch.setenv("SED_CREDENTIAL_SAP_SOLMAN", FAKE_VALUE)
    monkeypatch.setenv("SED_CREDENTIAL_SAP_HP1", FAKE_VALUE + "-hp1")
    change = {
        "ObjectId": "8000099901", "ProcessType": "SMMJ", "Description": "Pricing condition change for sales org",
        "UserStatus": "In Development", "Priority": "3 - Medium", "Component": "SD-BF-PR",
        "CreatedAt": _ms("2026-09-01T08:00:00"), "ChangedAt": _ms("2026-09-03T14:30:00"),
    }  # fmt: skip
    idoc = {
        "Docnum": "0000000099000001", "Direction": "2", "MessageType": "ORDERS", "PartnerNumber": "CUST0042",
        "Status": "51", "StatusText": "Application document not posted", "CreatedAt": "2026-09-04T06:00:00",
        "StatusChangedAt": "2026-09-04T06:05:00",
    }  # fmt: skip
    transport = RecordedTransport(
        [
            {"path": CHANGES, "params": {"$skip": 0, "$format": "json"}, "body": {"d": {"results": [change]}}},
            {"path": IDOCS, "params": {"$skip": 0}, "body": {"d": {"results": [idoc]}}},
        ]
    )
    result = pull(paths, "sap", transport=transport, now=NOW, allow_synthetic=True)
    files = {s["source"]: s["file"] for s in result["sources"]}
    assert files == {
        "charm_changes": "sap_charm_changes_pull_charm_changes_20260907T060000.csv",
        "idocs_hp1": "sap_idocs_pull_idocs_hp1_20260907T060000.csv",
    }
    assert transport.calls[0][1]["$orderby"] == "ChangedAt asc" and "$filter" not in transport.calls[0][1]
    assert transport.headers_seen[0] != transport.headers_seen[1]  # the HP1 gateway uses its own account
    idoc_text = (paths.inbox / files["idocs_hp1"]).read_text(encoding="utf-8-sig")
    assert idoc_text.splitlines()[0].endswith(",system_id") and idoc_text.splitlines()[1].endswith(",HP1")
    assert "2026-09-04 08:05:00" in idoc_text  # UTC converted to Europe/Paris

    for name in files.values():
        imported = run_import(
            paths, ImportOptions(files=[paths.inbox / name], allow_unmanifested=True, move_files=False)
        )
        assert imported["summary"]["errors"] == 0, imported["files"]
    conn = db.connect(paths.db, readonly=True)
    try:
        row = conn.execute("SELECT status_raw, changed_at FROM sap_change WHERE change_id = '8000099901'").fetchone()
        assert (row["status_raw"], row["changed_at"]) == ("In Development", "2026-09-03T14:30:00Z")
        idoc_row = conn.execute(
            "SELECT system_id, status_code FROM sap_idoc WHERE docnum = '0000000099000001'"
        ).fetchone()
        assert (idoc_row["system_id"], idoc_row["status_code"]) == ("HP1", "51")
    finally:
        conn.close()

    second = RecordedTransport([{"path": CHANGES, "body": {"d": {"results": []}}, "repeat": True},
                                {"path": IDOCS, "body": {"d": {"results": []}}, "repeat": True}])  # fmt: skip
    pull(paths, "sap", source="charm_changes", transport=second, now=NOW, allow_synthetic=True, dry_run=True)
    assert second.calls[0][1]["$filter"] == "ChangedAt ge datetime'2026-09-03T13:30:00'"  # watermark - 60 min
    listed = next(c for c in status(paths)["connectors"] if c["connector"] == "sap")
    assert listed["connector"] == "sap" and listed["sources"][1]["credential_found_in"] == "environment"


def test_odata_time_formats():
    assert odata_time("/Date(1788000000000)/") == datetime.fromtimestamp(1788000000, tz=UTC)
    assert odata_time("2026-09-04T06:05:00Z") == datetime(2026, 9, 4, 6, 5, tzinfo=UTC)
    assert odata_time("2026-09-04T06:05:00") == datetime(2026, 9, 4, 6, 5, tzinfo=UTC)
    assert odata_time("") is None and odata_time("not a date") is None
