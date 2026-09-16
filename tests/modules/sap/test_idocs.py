"""SAP IDoc health on the SAP profile: IDoc exports import with their status history, the read models follow the ground
truth, planted IDoc patterns show and the quick-reprocessing and cutover controls stay quiet."""

from __future__ import annotations

import csv
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from sed import db
from sed.calendar import as_of_end_utc, iso_utc, parse_period
from sed.ingest.loader import ImportOptions, run_import
from sed.modules.sap.charm import load_charm
from sed.modules.sap.idoc import load_idoc
from sed.modules.sap.queries import changes, idocs
from sed.modules.sap.scope import load_scope
from tests.fixtures.ops_profile import AS_OF

TZ = "Europe/Paris"
WEEK = parse_period("2026-W35", TZ)


def _load(profile: Any, conn: Any, at: datetime) -> tuple[idocs.IdocSet, changes.ChangeSet]:
    scope = load_scope(profile.paths).resolve(conn)
    return (
        idocs.load(conn, load_idoc(profile.paths, scope), at),
        changes.load(conn, load_charm(profile.paths, scope), at),
    )


@pytest.fixture(scope="module")
def env(sap_profile: Any):
    conn = db.connect(sap_profile.paths.db, readonly=True)
    try:
        ids, cs = _load(sap_profile, conn, as_of_end_utc(AS_OF, TZ))
        yield {"conn": conn, "ids": ids, "cs": cs}
    finally:
        conn.close()


@pytest.fixture(scope="module")
def truth(sap_profile: Any) -> dict[str, Any]:
    with (sap_profile.ground_truth / "idoc_truth.csv").open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    by_pattern: dict[str, set[tuple[str, str]]] = {}
    for row in rows:
        by_pattern.setdefault(row["pattern"], set()).add((row["system_id"], row["docnum"]))
    return {"rows": {(r["system_id"], r["docnum"]): r for r in rows}, "by_pattern": by_pattern}


def _errors(ids: idocs.IdocSet, keys: set[tuple[str, str]]) -> list[idocs.ErrorIdoc]:
    return [e for e in ids.errors if (e.system_id, e.docnum) in keys]


def test_history_matches_the_current_status(ro_conn):
    mismatches = ro_conn.execute(
        "SELECT COUNT(*) FROM sap_idoc i WHERE i.status_code <> (SELECT h.status_code FROM sap_idoc_status h "
        "WHERE h.system_id = i.system_id AND h.docnum = i.docnum ORDER BY h.status_at DESC, h.rowid DESC LIMIT 1)"
    ).fetchone()[0]
    assert mismatches == 0
    without = ro_conn.execute(
        "SELECT COUNT(*) FROM sap_idoc i WHERE NOT EXISTS (SELECT 1 FROM sap_idoc_status h "
        "WHERE h.system_id = i.system_id AND h.docnum = i.docnum)"
    ).fetchone()[0]
    assert without == 0
    assert {r[0] for r in ro_conn.execute("SELECT DISTINCT system_id FROM sap_idoc")} == {"EP1", "HP1"}


def test_ip1_invoic_errors_after_the_failed_import(env, truth, sap_truth):
    ids, cs = env["ids"], env["cs"]
    ip1 = _errors(ids, truth["by_pattern"]["IP1"])
    assert len(ip1) == 60 and {(e.system_id, e.message_type, e.first_error_code) for e in ip1} == {
        ("HP1", "INVOIC", "26")
    }
    assert sum(e.is_open for e in ip1) == 25 and all(ids.persistent(e) for e in ip1)
    assert {e.area for e in ip1} == {"fi_co"} and {e.landscape for e in ip1} == {"s4"}
    cp1 = sap_truth["patterns"]["changes"]["CP1"]
    spikes = idocs.spikes_after_imports(ids, cs, WEEK.previous(1).start_iso, iso_utc(ids.at))
    assert [(s["change_id"], s["system_id"]) for s in spikes] == [(cp1["change_id"], "HP1")]
    assert spikes[0]["errors"] >= 60 and spikes[0]["lift"] >= 50
    # 30 hours after the import the IP1 errors seen so far were all still open (reprocessing starts 30 h after each).
    imported = parse_utc_local(cp1["imported_at_local"])
    early, _ = _load_at(env, imported + timedelta(hours=30))
    seen = _errors(early, truth["by_pattern"]["IP1"])
    assert len(seen) >= 40 and all(e.is_open for e in seen)


def parse_utc_local(text: str) -> datetime:
    from zoneinfo import ZoneInfo

    from sed.calendar import UTC

    return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=ZoneInfo(TZ)).astimezone(UTC)


def _load_at(env: dict[str, Any], at: datetime) -> tuple[idocs.IdocSet, changes.ChangeSet]:
    ids = env["ids"]
    return idocs.load(env["conn"], ids.idoc, at), changes.load(env["conn"], env["cs"].charm, at)


def test_ip2_growing_partner_errors(env, truth, sap_profile):
    ids = env["ids"]
    ip2 = _errors(ids, truth["by_pattern"]["IP2"])
    assert len(ip2) == 64 and all(e.is_open and e.partner == "PARTNER_0007" for e in ip2)
    weeks = [WEEK.previous(k) for k in range(5, -1, -1)]
    mine = [e for e in ids.errors if (e.system_id, e.message_type, e.partner) == ("EP1", "ORDERS", "PARTNER_0007")]
    persistent = [
        sum(1 for e in mine if w.start_iso <= e.first_error_at < w.end_iso and ids.persistent(e)) for w in weeks
    ]
    assert persistent == [2, 4, 7, 11, 16, 24]
    partners = idocs.partners(ids, ids.errors)
    assert partners[0]["partner"] == "PARTNER_0007" and partners[0]["errors"] >= 64
    texts = idocs.top_texts(ids.errors)
    assert texts[0]["text"] == "Sold-to party # not maintained for sales area S100/01/00" and texts[0]["errors"] >= 64


def test_in1_quick_reprocessing_is_not_persistent(env, truth):
    ids = env["ids"]
    in1 = _errors(ids, truth["by_pattern"]["IN1"])
    assert len(in1) == 80 and {e.message_type for e in in1} == {"MATMAS"}
    assert not any(e.is_open or ids.persistent(e) for e in in1)
    hours = [(parse(e.reprocessed_at) - parse(e.first_error_at)).total_seconds() / 3600 for e in in1]
    assert all(3 <= h <= 11 for h in hours)
    row = next(r for r in idocs.weekly(env["conn"], ids, ids.errors, [WEEK]))
    assert row["new_errors"] >= 80 + 60 + 24 and row["persistent"] < row["new_errors"] - 70
    stats = idocs.reprocess_stats(ids, in1, WEEK)
    assert stats["reprocessed"] == 80 and stats["within_grace_pct"] == 100.0


def parse(text: str | None) -> datetime:
    from sed.calendar import parse_utc

    assert text is not None
    return parse_utc(text)


def test_in2_cutover_adds_volume_not_errors(env, truth, sap_truth):
    ids = env["ids"]
    in2 = truth["by_pattern"]["IN2"]
    assert len(in2) == 600 and not _errors(ids, in2)
    day = date.fromisoformat(sap_truth["patterns"]["idocs"]["IN2"]["day"])
    week = parse_period(f"{day.isocalendar().year}-W{day.isocalendar().week:02d}", TZ)
    rows = idocs.weekly(env["conn"], ids, ids.errors, [week.previous(1), week], system="HP1")
    assert rows[1]["idocs"] - rows[0]["idocs"] >= 500 and rows[1]["persistent"] <= 3


def test_breakdowns_add_up(env):
    ids = env["ids"]
    open_errors = [e for e in ids.errors if e.is_open]
    assert sum(idocs.aging(ids, ids.errors).values()) == len(open_errors)
    assert sum(r["errors"] for r in idocs.backlog_by_type(ids, ids.errors)) == len(open_errors)
    summary = idocs.summary(ids, WEEK)
    assert summary["errors_open"] == len(open_errors) and summary["unknown_status"] == 0
    per_system = [idocs.summary(ids, WEEK, system=s)["errors_open"] for s in ("EP1", "HP1")]
    assert sum(per_system) == summary["errors_open"]
    per_direction = [idocs.summary(ids, WEEK, direction=d)["errors_open"] for d in ("inbound", "outbound")]
    assert sum(per_direction) == summary["errors_open"]
    by_type = Counter((e.system_id, e.message_type) for e in open_errors)
    assert by_type[("EP1", "ORDERS")] >= 64 and by_type[("HP1", "INVOIC")] >= 25
    rows = idocs.open_errors(ids, ids.errors, limit=5)
    assert [r["first_error_at"] for r in rows] == sorted(r["first_error_at"] for r in rows)


def _write(path: Path, header: list[str], rows: list[list[Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(rows)
    return path


def test_late_exports_keep_idoc_history_consistent(sap_profile_rw, truth, tmp_path):
    from sed.modules.sap.synth_idocs import HEADER

    paths = sap_profile_rw.paths
    system_id, docnum = sorted(truth["by_pattern"]["IP2"])[0]

    def state() -> tuple[Any, list[tuple[str, str]]]:
        conn = db.connect(paths.db, readonly=True)
        try:
            row = conn.execute(
                "SELECT status_code, status_at FROM sap_idoc WHERE system_id = ? AND docnum = ?", (system_id, docnum)
            ).fetchone()
            hist = conn.execute(
                "SELECT status_at, status_code FROM sap_idoc_status WHERE system_id = ? AND docnum = ? "
                "ORDER BY status_at",
                (system_id, docnum),
            ).fetchall()
            return tuple(row), [tuple(h) for h in hist]
        finally:
            conn.close()

    def export(name: str, code: str, at: str) -> None:
        row = [
            system_id,
            docnum,
            "2",
            "ORDERS",
            "ORDERS05",
            "KU",
            "PARTNER_0007",
            code,
            "text",
            "2026-07-20 08:00:00",
            at,
        ]
        file = _write(tmp_path / name, HEADER, [row])
        assert (
            run_import(paths, ImportOptions(files=[file], allow_unmanifested=True, move_files=False))["summary"][
                "errors"
            ]
            == 0
        )

    before = state()
    export("sap_idocs_2026-07-01.csv", "64", "2026-07-01 08:00:00")
    assert state() == before
    export("sap_idocs_2026-09-02.csv", "53", "2026-09-02 10:00:00")
    row, history = state()
    assert row == ("53", "2026-09-02T08:00:00Z") and history[-1] == ("2026-09-02T08:00:00Z", "53")
    later = as_of_end_utc(date(2026, 9, 2), TZ)
    conn = db.connect(paths.db, readonly=True)
    try:
        ids = idocs.load(conn, load_idoc(paths, load_scope(paths)), later)
    finally:
        conn.close()
    fixed = next(e for e in ids.errors if (e.system_id, e.docnum) == (system_id, docnum))
    assert fixed.reprocessed_at == "2026-09-02T08:00:00Z" and not fixed.is_open and ids.persistent(fixed)
