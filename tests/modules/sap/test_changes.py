"""SAP changes and transports on the SAP profile: the ChaRM exports import with their status history, the read models
follow the ground truth, planted change patterns show, and the release-weekend control stays quiet."""

from __future__ import annotations

import csv
from datetime import date, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest

from sed import db
from sed.calendar import as_of_end_utc, iso_utc, parse_period
from sed.ingest.loader import ImportOptions, run_import
from sed.modules.sap.charm import load_charm
from sed.modules.sap.queries import changes
from sed.modules.sap.scope import load_scope
from sed.settings import load_settings
from tests.fixtures.ops_profile import AS_OF


def _load(profile: Any, conn: Any, at_date: date = AS_OF) -> changes.ChangeSet:
    settings = load_settings(profile.paths)
    scope = load_scope(profile.paths).resolve(conn)
    return changes.load(conn, load_charm(profile.paths, scope), as_of_end_utc(at_date, settings.reporting_tz))


@pytest.fixture(scope="module")
def cs(sap_profile: Any):
    conn = db.connect(sap_profile.paths.db, readonly=True)
    try:
        yield _load(sap_profile, conn), conn
    finally:
        conn.close()


def _pattern(truth: dict[str, Any], name: str) -> set[str]:
    return {cid for cid, row in truth["changes"].items() if row["pattern"] == name}


def test_changes_follow_the_ground_truth(cs, sap_truth):
    changeset, _ = cs
    assert set(changeset.changes) == set(sap_truth["changes"])
    for change_id, row in sap_truth["changes"].items():
        change = changeset.changes[change_id]
        assert (change.change_type, change.area, change.landscape, change.stage) == (
            row["change_type"],
            row["area"],
            row["landscape"],
            row["stage_at_as_of"],
        ), change_id


def test_status_history_is_recorded_per_export(sap_profile, ro_conn, sap_truth):
    history = ro_conn.execute(
        "SELECT change_id, seen_at, status_raw FROM sap_change_status ORDER BY change_id, seen_at"
    )
    by_change: dict[str, list[tuple[str, str]]] = {}
    for change_id, seen_at, status in history:
        by_change.setdefault(change_id, []).append((seen_at, status))
    assert set(by_change) == set(sap_truth["changes"])
    for rows in by_change.values():
        statuses = [s for _, s in rows]
        assert all(a != b for a, b in pairwise(statuses)), "no repeated status rows"
    for change_id in _pattern(sap_truth, "CP3"):
        assert by_change[change_id][-1][1] == "To Be Tested"
    # Status at a past moment comes from the history: 50 days before the anchor the CP3 changes were in development.
    past = _load(sap_profile, ro_conn, AS_OF - timedelta(days=50))
    assert {past.changes[c].stage for c in _pattern(sap_truth, "CP3")} == {"in_development"}


def test_latest_transport_import_wins(ro_conn, sap_truth):
    failed = ro_conn.execute(
        "SELECT transport, system_id, return_code FROM sap_transport_import WHERE return_code >= 8"
    ).fetchall()
    cp1 = sap_truth["patterns"]["changes"]["CP1"]
    assert [tuple(r) for r in failed] == [(cp1["transport"], cp1["system"], 8)]  # QA re-imports replaced their RC 8
    assert ro_conn.execute("SELECT COUNT(*) FROM sap_transport_import WHERE imported_at IS NULL").fetchone()[0] == 0


def test_cp1_failed_import_with_incidents(cs, sap_truth):
    changeset, conn = cs
    cp1 = sap_truth["patterns"]["changes"]["CP1"]
    since = iso_utc(changeset.at - timedelta(days=28))
    failed = changes.failed_imports(changeset, since)
    assert [(r["transport"], r["system_id"], r["role"], r["change_id"]) for r in failed] == [
        (cp1["transport"], "HP1", "prod", cp1["change_id"])
    ]
    min_lift = changeset.charm.config.thresholds.incident_min_lift
    after = changes.incidents_after_imports(conn, changeset, since, iso_utc(changeset.at), min_lift=min_lift)
    top = after[0]
    assert (top["change_id"], top["system_id"], top["incidents"]) == (cp1["change_id"], "HP1", cp1["incidents"])
    assert top["lift"] == top["incidents"] - top["incidents_before"] >= 10
    assert all(r["lift"] >= min_lift for r in after)
    everything = changes.incidents_after_imports(conn, changeset, since, iso_utc(changeset.at), min_lift=None)
    assert len(everything) >= len(after) and all(r["incidents"] > 0 for r in everything)
    cp1_tickets = {n for n, t in sap_truth["tickets"].items() if t["pattern"] == "CP1"}
    assert set(top["numbers"]) <= cp1_tickets and len(cp1_tickets) == cp1["incidents"]
    assert changes.summary(conn, changeset, _week())["failed_4w"] == 1


def _week(label: str = "2026-W35") -> Any:
    return parse_period(label, "Europe/Paris")


def test_cp2_urgent_share_rises_in_mm(cs, sap_truth):
    changeset, _ = cs
    window = [_week().previous(k) for k in range(7, -1, -1)]
    previous = [window[0].previous(k) for k in range(8, 0, -1)]
    rows = {r["area"]: r for r in changes.urgent_by_area(changeset, window, previous)}
    expected = sap_truth["patterns"]["changes"]["CP2"]
    mm = rows["mm"]
    assert mm["created"] >= expected["recent"]["changes"] and mm["urgent"] >= expected["recent"]["urgent"]
    assert mm["ratio_pct"] >= 50 and mm["previous_ratio_pct"] <= 15 and mm["delta_pp"] >= 35
    others = [r for code, r in rows.items() if code != "mm" and r["created"] >= 8]
    assert all((r["ratio_pct"] or 0) < 30 for r in others)


def test_cp3_stuck_cp4_waiting_cp5_without_jira(cs, sap_truth):
    changeset, _ = cs
    selected = changes.select(changeset)
    stuck = changes.stuck(changeset, selected)
    assert {r["change_id"] for r in stuck} == _pattern(sap_truth, "CP3")
    assert all(r["stage"] == "in_test" and 45 <= r["days_in_status"] < 47 and r["threshold_days"] == 30 for r in stuck)
    waiting = changes.waiting_for_production(changeset)
    assert {r["transport"] for r in waiting} == set(sap_truth["patterns"]["changes"]["CP4"]["transports"])
    assert {(r["landscape"], r["qa_system"]) for r in waiting} == {("ecc", "EQ1")}
    assert all(25 <= r["days_waiting"] < 27 for r in waiting)
    assert {r["change_id"] for r in changes.without_jira(changeset, selected)} == _pattern(sap_truth, "CP5")


def test_cn1_release_weekend_is_quiet(cs, sap_truth):
    changeset, conn = cs
    cn1 = _pattern(sap_truth, "CN1")
    release = date.fromisoformat(sap_truth["patterns"]["changes"]["CN1"]["release_day"])
    week = _week(f"{release.isocalendar().year}-W{release.isocalendar().week:02d}")
    prod = [i for i in changeset.imports if i.change_id in cn1 and i.role == "prod"]
    assert len(prod) == 30 and all(not changeset.failed(i) for i in prod)
    assert {i.imported_at[:10] for i in prod} == {release.isoformat()}
    assert changes.production_imports(changeset, [week])[0]["imports"] >= 30
    after = changes.incidents_after_imports(conn, changeset, week.start_iso, iso_utc(changeset.at))
    assert not [r for r in after if r["change_id"] in cn1]


def test_jira_links_are_read_both_ways(cs, sap_truth, ro_conn):
    changeset, _ = cs
    expected = {
        cid
        for cid, row in sap_truth["changes"].items()
        if row["jira"] == "1" and row["change_type"] in ("normal", "urgent", "defect_correction")
    }
    linked = {cid for cid, c in changeset.changes.items() if c.jira_keys}
    assert expected <= linked
    refs = dict(ro_conn.execute("SELECT change_id, external_ref FROM sap_change").fetchall())
    assert any(refs[cid] for cid in expected) and any(not refs[cid] for cid in expected)  # both link directions used


def test_stage_matrix_and_filters_add_up(cs):
    changeset, _ = cs
    selected = changes.select(changeset)
    matrix = changes.stage_matrix(selected)
    assert sum(r["total"] for r in matrix) == sum(1 for c in selected if c.is_open)
    assert all(r["total"] == sum(v for k, v in r.items() if k not in ("stage", "label", "total")) for r in matrix)
    by_area = sum(len(changes.select(changeset, area=a)) for a in {c.area for c in selected})
    by_landscape = sum(len(changes.select(changeset, landscape=x)) for x in {c.landscape for c in selected})
    assert by_area == by_landscape == len(selected)


def _write(path: Path, header: list[str], rows: list[list[Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(rows)
    return path


def test_late_and_repeated_exports_keep_history_consistent(sap_profile_rw, sap_truth, tmp_path):
    from sed.modules.sap.synth_changes import CHANGE_HEADER

    paths = sap_profile_rw.paths
    change_id = sorted(_pattern(sap_truth, "CP3"))[0]

    def state() -> tuple[Any, list[tuple[str, str]]]:
        conn = db.connect(paths.db, readonly=True)
        try:
            row = conn.execute(
                "SELECT status_raw, changed_at FROM sap_change WHERE change_id = ?", (change_id,)
            ).fetchone()
            hist = conn.execute(
                "SELECT seen_at, status_raw FROM sap_change_status WHERE change_id = ? ORDER BY seen_at", (change_id,)
            ).fetchall()
            return tuple(row), [tuple(h) for h in hist]
        finally:
            conn.close()

    def export(name: str, status: str, changed: str) -> None:
        row = [change_id, "SMMJ", "Inspection plan changes for plant P200", status, "3 - Medium", "PP-SFC",
               "ECC Maintenance", "2026-06-23 10:00:00", changed, "", "", "", "", ""]  # fmt: skip
        file = _write(tmp_path / name, CHANGE_HEADER, [row])
        result = run_import(paths, ImportOptions(files=[file], allow_unmanifested=True, move_files=False))
        assert result["summary"]["errors"] == 0, result["files"]

    before = state()
    export("sap_charm_changes_2026-07-01.csv", "In Development", "2026-07-01 09:00:00")  # older than the stored row
    assert state() == before
    export("sap_charm_changes_2026-09-02.csv", "Successfully Tested", "2026-09-02 09:00:00")
    row, history = state()
    assert row == ("Successfully Tested", "2026-09-02T07:00:00Z")
    assert history[:-1] == before[1] and history[-1] == ("2026-09-02T07:00:00Z", "Successfully Tested")
    export("sap_charm_changes_2026-09-03.csv", "successfully  tested", "2026-09-03 09:00:00")  # same status, new edit
    assert state()[1] == history
    # A status change exported with the same changed-on moment (coarse export times) is still recorded and read.
    export("sap_charm_changes_2026-09-03b.csv", "Authorized for Production", "2026-09-03 09:00:00")
    row, history = state()
    assert row[0] == "Authorized for Production" and history[-1] == (
        "2026-09-03T07:00:00Z",
        "Authorized for Production",
    )
    conn = db.connect(paths.db, readonly=True)
    try:
        later = _load(sap_profile_rw, conn, date(2026, 9, 3))
    finally:
        conn.close()
    assert later.changes[change_id].stage == "ready_for_production"
