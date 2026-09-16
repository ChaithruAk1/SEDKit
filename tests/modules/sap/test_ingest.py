"""SAP exports through the SAP mappings: display-label headers and XLSX map to the same rows as field names, the open
snapshot goes to its own mapping, custom fields are kept only when listed, and injected PII never reaches the
database."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from sed import db
from sed.ingest.loader import ImportOptions, run_import
from sed.ingest.mapping import load_all_mappings, load_mapping
from sed.modules import mapping_glob_overlaps, mapping_index

LABELS = {
    "number": "Number",
    "opened_at": "Opened",
    "sys_updated_on": "Updated",
    "resolved_at": "Resolved",
    "closed_at": "Closed",
    "state": "State",
    "priority": "Priority",
    "impact": "Impact",
    "urgency": "Urgency",
    "category": "Category",
    "subcategory": "Subcategory",
    "short_description": "Short description",
    "description": "Description",
    "close_code": "Resolution code",
    "close_notes": "Resolution notes",
    "caller_id": "Caller",
    "assigned_to": "Assigned to",
    "assignment_group": "Assignment group",
    "business_service": "Service",
    "cmdb_ci": "Configuration item",
    "made_sla": "Made SLA",
    "reassignment_count": "Reassignment count",
    "reopen_count": "Reopen count",
    "problem_id": "Problem",
    "caused_by": "Caused by Change",
    "parent_incident": "Parent Incident",
    "u_sap_component": "u_sap_component",
}


def _source_rows(tmp: Path) -> tuple[list[str], list[list[str]]]:
    """A small generated SAP month (fresh generator output; the profile's own inbox files were moved on import)."""
    from datetime import date

    from sed.modules.contract import SynthRequest
    from sed.modules.sap.synth import generate
    from sed.paths import Paths

    paths = Paths(profile="synthetic", data_dir=tmp / "gen" / "synthetic")
    paths.ensure()
    generate(paths, SynthRequest(seed=42, as_of=date(2026, 9, 1), scale=0.02))
    with (paths.inbox / "sap_incident_2026-08.csv").open(encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        return header, [row for row in reader][:25]


def _dry_run(paths: Any, file: Path) -> dict[str, Any]:
    result = run_import(
        paths, ImportOptions(files=[file], dry_run=True, allow_unmanifested=True, move_files=False, sample_rows=25)
    )
    assert not result["summary"]["errors"], result
    (entry,) = result["files"]
    return entry


def test_mappings_are_owned_by_sap_and_globs_do_not_overlap_ops(sap_profile):
    index = mapping_index(sap_profile.paths)
    assert {n for n, (owner, _) in index.items() if owner == "sap"} == {
        "sap_business_apps",
        "sap_incidents",
        "sap_incidents_active",
    }
    assert mapping_glob_overlaps(sap_profile.paths) == []
    specs = load_all_mappings(sap_profile.paths)
    assert specs["sap_incidents_active"].load_mode == "active_snapshot"
    assert specs["sap_incidents_active"].fields == specs["sap_incidents"].fields
    assert load_mapping("sap_incidents", sap_profile.paths).raw_keep == ["u_sap_component"]


def test_label_headers_and_xlsx_map_like_field_names(sap_profile_rw, tmp_path):
    header, rows = _source_rows(tmp_path)
    names = tmp_path / "names" / "sap_incident_2026-08.csv"
    labels = tmp_path / "labels" / "sap_incident_2026-08.csv"
    xlsx = tmp_path / "xlsx" / "sap_incident_2026-08.xlsx"
    for path, head in ((names, header), (labels, [LABELS[h] for h in header])):
        path.parent.mkdir(parents=True)
        with path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(head)
            writer.writerows(rows)
    import xlsxwriter

    xlsx.parent.mkdir(parents=True)
    book = xlsxwriter.Workbook(str(xlsx), {"strings_to_numbers": False})
    sheet = book.add_worksheet("Page 1")
    for r, values in enumerate([[LABELS[h] for h in header], *rows]):
        sheet.write_row(r, 0, values)
    book.close()

    by_names = _dry_run(sap_profile_rw.paths, names)
    assert by_names["mapping"] == "sap_incidents" and by_names["rows_valid"] == len(rows) > 0
    for variant in (labels, xlsx):
        entry = _dry_run(sap_profile_rw.paths, variant)
        assert entry["mapping"] == "sap_incidents", variant.name
        assert entry["rows_valid"] == by_names["rows_valid"]
        assert entry["samples"] == by_names["samples"], variant.name


def test_open_snapshot_uses_its_own_mapping(sap_profile):
    conn = db.connect(sap_profile.paths.db, readonly=True)
    try:
        batches = conn.execute(
            "SELECT file_name, mapping_name, load_mode, as_of FROM import_batch WHERE file_name LIKE 'sap_%' "
            "AND status = 'completed' ORDER BY file_name"
        ).fetchall()
    finally:
        conn.close()
    by_file = {r[0]: r[1:] for r in batches}
    assert by_file["sap_business_apps.csv"][:2] == ("sap_business_apps", "full_snapshot")
    assert by_file["sap_incident_active_2026-09-01.csv"] == ("sap_incidents_active", "active_snapshot", "2026-09-01")
    months = [f for f in by_file if f.startswith("sap_incident_2")]
    assert len(months) == 18 and {by_file[f][:2] for f in months} == {("sap_incidents", "delta")}


def test_custom_field_kept_only_on_sap_tickets(sap_profile, sap_truth):
    conn = db.connect(sap_profile.paths.db, readonly=True)
    try:
        kept = conn.execute("SELECT number, raw_keep_json FROM ticket WHERE raw_keep_json IS NOT NULL").fetchall()
        apps = dict(conn.execute("SELECT app_id, name FROM application WHERE app_id LIKE 'APM099%'").fetchall())
    finally:
        conn.close()
    assert kept and {n for n, _ in kept} <= set(sap_truth["tickets"])
    assert apps == {"APM0990001": "SAP ECC", "APM0990002": "SAP S/4HANA"}


def test_injected_pii_never_reaches_the_database(sap_profile, sap_truth):
    marks = ", ".join("?" for _ in sap_truth["tickets"])
    conn = db.connect(sap_profile.paths.db, readonly=True)
    try:
        rows = conn.execute(
            "SELECT short_description, description, close_notes, caller_pid, assigned_to_pid, raw_keep_json "
            f"FROM ticket WHERE number IN ({marks})",
            list(sap_truth["tickets"]),
        ).fetchall()
        owners = [r[0] for r in conn.execute("SELECT it_owner_pid FROM application WHERE app_id LIKE 'APM099%'")]
    finally:
        conn.close()
    assert len(rows) == len(sap_truth["tickets"]) and sap_truth["pii"]
    text = "\n".join(str(v) for row in rows for v in row if v is not None)
    for value in sap_truth["pii"]:
        assert value not in text, value
    assert all(r[3] and r[3].startswith("P-") for r in rows)
    assert all(pid is None or pid.startswith("P-") for pid in owners)
    signed = [r[1] for r in rows if "Best regards" in (r[1] or "")]
    assert not signed, "signature blocks are stripped from SAP descriptions"
