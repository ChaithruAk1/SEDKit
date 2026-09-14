from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
import xlsxwriter

from sed import bootstrap, db
from sed.errors import PreconditionFailed
from sed.ingest.loader import ImportOptions, reresolve, run_import
from sed.paths import get_paths

INC_HEADER = [
    "number",
    "opened_at",
    "sys_updated_on",
    "resolved_at",
    "closed_at",
    "state",
    "priority",
    "short_description",
    "description",
    "caller_id",
    "assigned_to",
    "assignment_group",
    "business_service",
    "cmdb_ci",
    "made_sla",
]


@pytest.fixture
def profile(data_root: Path):
    paths = get_paths("synthetic")
    bootstrap.init_profile(paths, new_salt=True, write_claude_settings=False)
    return paths


def write_csv(path: Path, header: list[str], rows: list[list[object]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)
    return path


def imp(paths, *files: Path, **kw) -> dict:
    opts = ImportOptions(files=list(files), allow_unmanifested=True, move_files=False, **kw)
    return run_import(paths, opts)


def inc(number: str, updated: str, state: str = "In Progress", resolved: str = "", desc: str = "Printer jam") -> list:
    return [
        number,
        "2026-08-20 09:00:00",
        updated,
        resolved,
        "",
        state,
        "3 - Moderate",
        "Short",
        desc,
        "Ana Sousa",
        "Lars Nilsson",
        "IT-FIN-L2",
        "",
        "",
        "true",
    ]


def q(paths, sql: str, *args):
    conn = db.connect(paths.db, readonly=True)
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def test_delta_upsert_same_file_noop_and_freshness_guard(profile, tmp_path: Path):
    f1 = write_csv(
        tmp_path / "incident_2026-08.csv",
        INC_HEADER,
        [inc("INC0000001", "2026-08-20 10:00:00"), inc("INC0000002", "2026-08-20 10:00:00")],
    )
    r = imp(profile, f1)
    assert r["files"][0]["inserted"] == 2
    assert imp(profile, f1)["skipped"][0]["reason"].startswith("already imported")

    newer = write_csv(
        tmp_path / "incident_2026-08b.csv",
        INC_HEADER,
        [
            inc("INC0000001", "2026-08-21 10:00:00", "Resolved", "2026-08-21 09:00:00"),
            inc("INC0000002", "2026-08-20 10:00:00"),
        ],
    )
    r = imp(profile, newer)["files"][0]
    assert (r["inserted"], r["updated"], r["unchanged"]) == (0, 1, 1)

    older = write_csv(
        tmp_path / "incident_2026-08_old.csv", INC_HEADER, [inc("INC0000001", "2026-08-20 11:00:00", "In Progress")]
    )
    r = imp(profile, older)["files"][0]
    assert r["updated"] == 0 and r["unchanged"] == 1
    state, is_open = q(profile, "SELECT state, is_open FROM ticket WHERE number = 'INC0000001'")[0]
    assert state == "Resolved" and is_open == 0


def test_pii_is_pseudonymized_on_import(profile, tmp_path: Path):
    f = write_csv(
        tmp_path / "incident_x.csv",
        INC_HEADER,
        [inc("INC0000009", "2026-08-20 10:00:00", desc="Ana Sousa says mail ana.sousa@example.org")],
    )
    imp(profile, f)
    caller, desc = q(profile, "SELECT caller_pid, description FROM ticket")[0]
    assert caller.startswith("P-")
    assert "Sousa" not in desc and "example.org" not in desc
    assert q(profile, "SELECT COUNT(*) FROM person_key")[0][0] > 0


VENDOR_HEADER = ["Vendor ID", "Vendor Name", "Type", "Tier", "SLA Target %"]


def vendor_xlsx(path: Path, rows: list[list[object]]) -> Path:
    wb = xlsxwriter.Workbook(str(path))
    ws = wb.add_worksheet()
    ws.write_row(0, 0, VENDOR_HEADER)
    for i, r in enumerate(rows, start=1):
        ws.write_row(i, 0, r)
    wb.close()
    return path


def test_full_snapshot_soft_delete_and_guard(profile, tmp_path: Path):
    rows = [[f"V{i:03d}", f"Vendor {i}", "SaaS", "Tactical", "95%"] for i in range(1, 11)]
    imp(profile, vendor_xlsx(tmp_path / "Vendor_Master_1.xlsx", rows))
    r = imp(profile, vendor_xlsx(tmp_path / "Vendor_Master_2.xlsx", rows[:9]))["files"][0]
    assert r["status"] == "completed" and r["dq"]["soft_deleted"] == 1
    assert q(profile, "SELECT is_deleted FROM vendor WHERE vendor_id = 'V010'")[0][0] == 1

    r = imp(profile, vendor_xlsx(tmp_path / "Vendor_Master_3.xlsx", rows[:3]))["files"][0]
    assert r["status"] == "error" and "disappear" in r["error"]["message"]
    assert q(profile, "SELECT COUNT(*) FROM vendor WHERE is_deleted = 0")[0][0] == 9

    r = imp(profile, vendor_xlsx(tmp_path / "Vendor_Master_4.xlsx", rows[:3]), force=True)["files"][0]
    assert r["status"] == "completed"
    assert q(profile, "SELECT COUNT(*) FROM vendor WHERE is_deleted = 0")[0][0] == 3
    # Resurrection: a vendor that comes back is undeleted.
    imp(profile, vendor_xlsx(tmp_path / "Vendor_Master_5.xlsx", rows[:4]))
    assert q(profile, "SELECT is_deleted FROM vendor WHERE vendor_id = 'V004'")[0][0] == 0


def test_append_snapshot_replaces_only_its_as_of(profile, tmp_path: Path):
    lic_header = [
        "License ID",
        "Application",
        "Vendor",
        "Contract No",
        "Product",
        "Metric",
        "Entitled Qty",
        "Unit Cost (EUR)",
    ]
    write_csv(tmp_path / "License_Inventory.csv", lic_header, [["LIC-1", "", "", "", "Suite", "Named user", 100, 50]])
    imp(profile, tmp_path / "License_Inventory.csv")
    usage_header = ["License ID", "Assigned", "Active (90d)"]
    imp(profile, write_csv(tmp_path / "License_Usage_2026-07.csv", usage_header, [["LIC-1", 90, 80], ["LIC-X", 1, 1]]))
    imp(profile, write_csv(tmp_path / "License_Usage_2026-08.csv", usage_header, [["LIC-1", 91, 81]]))
    imp(profile, write_csv(tmp_path / "License_Usage_2026-08_fix.csv", usage_header, [["LIC-1", 60, 50]]), as_of=None)
    rows = q(profile, "SELECT as_of_date, active_qty_90d FROM license_usage ORDER BY as_of_date")
    assert [tuple(r) for r in rows] == [("2026-07-31", 80.0), ("2026-08-31", 50.0)]
    rejects = q(profile, "SELECT reason FROM row_reject")
    assert any("unknown license_id" in r[0] for r in rejects)


def test_active_snapshot_flags_and_clears_stale(profile, tmp_path: Path):
    imp(
        profile,
        write_csv(
            tmp_path / "incident_2026-08.csv",
            INC_HEADER,
            [inc("INC0000011", "2026-08-25 10:00:00"), inc("INC0000012", "2026-08-25 10:00:00")],
        ),
    )
    r = imp(
        profile,
        write_csv(tmp_path / "incident_active_2026-09-01.csv", INC_HEADER, [inc("INC0000012", "2026-08-25 10:00:00")]),
    )["files"][0]
    assert r["dq"]["stale_open_flagged"] == 1
    assert q(profile, "SELECT number FROM ticket WHERE stale_open = 1")[0][0] == "INC0000011"
    imp(
        profile,
        write_csv(
            tmp_path / "incident_2026-09.csv",
            INC_HEADER,
            [inc("INC0000011", "2026-09-02 10:00:00", "Resolved", "2026-09-02 09:00:00")],
        ),
    )
    assert tuple(q(profile, "SELECT stale_open, is_open FROM ticket WHERE number = 'INC0000011'")[0]) == (0, 0)


def test_reresolve_after_alias_assignment(profile, tmp_path: Path):
    imp(
        profile,
        vendor_xlsx(tmp_path / "Vendor_Master.xlsx", [["V001", "Nordwind Managed Services", "MS", "Strategic", "95%"]]),
    )
    header = [
        "Contract No",
        "Vendor",
        "Application",
        "End Date",
        "Notice Period (days)",
        "Auto Renew",
        "Annual Value",
        "Currency",
    ]
    imp(
        profile,
        write_csv(
            tmp_path / "contracts_register.csv",
            header,
            [
                ["C-1", "Nordwind Managed Services GmbH", "", "2027-01-31", 90, "Y", "12.000,50", "EUR"],
                ["C-2", "NORDWIND MS", "", "2027-03-31", 60, "N", "5000", "USD"],
            ],
        ),
    )
    rows = dict(q(profile, "SELECT contract_id, vendor_id FROM contract"))
    assert rows == {"C-1": "V001", "C-2": None}  # legal suffix resolves automatically; the abbreviation does not
    unmapped = q(profile, "SELECT raw_value, suggestion FROM unmapped_value WHERE kind = 'vendor'")
    assert unmapped[0][0] == "NORDWIND MS" and unmapped[0][1] == "Nordwind Managed Services"
    conn = db.connect(profile.db)
    try:
        from sed.ingest.resolve import Resolver

        res = Resolver(conn)
        res.add_alias("vendor", "NORDWIND MS", "V001", "manual")
        with db.write_tx(conn):
            res.flush(None)
    finally:
        conn.close()
    counts = reresolve(profile)
    assert counts["contract"] == 1 and counts["unmapped_marked_resolved"] == 1
    assert dict(q(profile, "SELECT contract_id, vendor_id FROM contract")) == {"C-1": "V001", "C-2": "V001"}
    value_base, currency = q(profile, "SELECT annual_value_base, currency FROM contract WHERE contract_id = 'C-2'")[0]
    assert currency == "USD" and value_base == pytest.approx(5000 * 0.92)


def test_missing_required_column_rejects_file(profile, tmp_path: Path):
    bad = write_csv(tmp_path / "incident_bad.csv", ["opened_at", "state"], [["2026-08-20 09:00:00", "New"]])
    r = imp(profile, bad)
    assert r["summary"]["errors"] == 1
    assert "number" in r["files"][0]["error"]["message"] or "fit" in r["files"][0]["error"]["message"]


def test_dry_run_writes_nothing(profile, tmp_path: Path):
    f = write_csv(tmp_path / "incident_dry.csv", INC_HEADER, [inc("INC0000021", "2026-08-20 10:00:00")])
    r = imp(profile, f, dry_run=True)
    assert r["files"][0]["status"] == "dry_run" and r["files"][0]["samples"][0]["caller_pid"].startswith("P-")
    assert q(profile, "SELECT COUNT(*) FROM ticket")[0][0] == 0
    assert q(profile, "SELECT COUNT(*) FROM import_batch")[0][0] == 0


def test_data_class_guards(data_root: Path, tmp_path: Path):
    synthetic = get_paths("synthetic")
    bootstrap.init_profile(synthetic, new_salt=True, write_claude_settings=False)
    f = write_csv(synthetic.inbox / "incident_2026-08.csv", INC_HEADER, [inc("INC0000031", "2026-08-20 10:00:00")])
    with pytest.raises(PreconditionFailed, match="manifest"):
        run_import(synthetic, ImportOptions(inbox=True))
    real = get_paths("real")
    bootstrap.init_profile(real, new_salt=True, write_claude_settings=False)
    real.inbox.mkdir(parents=True, exist_ok=True)
    write_csv(real.inbox / "incident_2026-08.csv", INC_HEADER, [inc("INC0000032", "2026-08-20 10:00:00")])
    (real.inbox / "_manifest.json").write_text(json.dumps({"data_class": "synthetic", "files": {f.name: "x"}}), "utf-8")
    with pytest.raises(PreconditionFailed, match="SYNTHETIC"):
        run_import(real, ImportOptions(inbox=True))


def test_wrong_salt_blocks_import(profile, tmp_path: Path):
    profile.salt_file.write_text("a" * 64, encoding="utf-8")
    f = write_csv(tmp_path / "incident_s.csv", INC_HEADER, [inc("INC0000041", "2026-08-20 10:00:00")])
    with pytest.raises(PreconditionFailed, match="salt"):
        imp(profile, f)
