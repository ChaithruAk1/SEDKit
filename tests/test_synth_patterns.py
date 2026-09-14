"""End to end on a small synthetic profile: synth -> import -> rule findings -> weekly report.

Planted patterns are generated at absolute counts, so they are detectable at --scale 0.02. Ground truth is read only
by this test (Python), never exposed to agents.
"""

from __future__ import annotations

import json
import shutil
from datetime import date, timedelta
from pathlib import Path

import pytest
from python_calamine import CalamineWorkbook

from sed import bootstrap, db
from sed.analytics import published_rule_findings, refresh_rule_findings
from sed.calendar import parse_period
from sed.ingest.loader import ImportOptions, run_import
from sed.metrics import Filters, group_flow
from sed.paths import Paths, get_paths
from sed.reports.build import build_report
from sed.synth.generate import SynthOptions, generate

AS_OF = date(2026, 9, 1)
SKIP_LEAK_TABLES = {"person_display", "person_key"}  # local-only by design


@pytest.fixture(scope="module")
def pipeline(tmp_path_factory: pytest.TempPathFactory):
    root = tmp_path_factory.mktemp("patterns")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("SED_DATA_ROOT", str(root / "sed-data"))
        mp.setenv("SED_CLAUDE_SETTINGS_LOCAL", str(root / "settings.local.json"))
        mp.delenv("SED_PROFILE", raising=False)
        paths = get_paths("synthetic")
        bootstrap.init_profile(paths, new_salt=True, write_claude_settings=False)
        generate(paths, SynthOptions(seed=42, as_of=AS_OF, scale=0.02))
        result = run_import(paths, ImportOptions(inbox=True))
        conn = db.connect(paths.db)
        try:
            refresh = refresh_rule_findings(conn, paths, AS_OF)
        finally:
            conn.close()
        report = build_report(paths, "weekly", "2026-W35", ["xlsx", "md"], "none")
    truth = json.loads((paths.ground_truth / "patterns.json").read_text(encoding="utf-8"))
    return {"paths": paths, "import": result, "refresh": refresh, "report": report, "truth": truth}


@pytest.fixture
def conn(pipeline):
    c = db.connect(pipeline["paths"].db)
    yield c
    c.close()


def _findings(conn) -> list[dict]:
    return published_rule_findings(conn, AS_OF)


def _id_for(conn, table: str, id_col: str, name: str) -> str:
    row = conn.execute(f"SELECT {id_col} FROM {table} WHERE name = ?", (name,)).fetchone()
    assert row, f"{table} {name!r} missing"
    return row[0]


def test_import_is_clean(pipeline):
    summary = pipeline["import"]["summary"]
    assert summary["errors"] == 0, [f for f in pipeline["import"]["files"] if f["status"] == "error"]
    assert summary["imported"] >= 40
    dq_warned = {f["file"] for f in pipeline["import"]["files"] if f.get("dq", {}).get("severity") == "warn"}
    # The deliberately dirty files import with documented DQ warnings rather than failing.
    assert any(name.startswith("Budget_FY") for name in dq_warned)
    assert any(name.startswith("IT_Cost_Actuals") for name in dq_warned)


def test_license_findings_match_truth(pipeline, conn):
    truth = pipeline["truth"]
    flagged = {f["subject_id"] for f in _findings(conn) if f["kind"] == "license_risk"}
    expected = {entry["license"] for entry in truth["P3"]}
    assert flagged == expected
    assert truth["controls"]["seasonal_license"] not in flagged


def test_renewal_findings_cover_p4(pipeline, conn):
    truth = pipeline["truth"]
    findings = [f for f in _findings(conn) if f["kind"] == "renewal_risk"]
    flagged = {f["subject_id"] for f in findings}
    for contract in truth["P4"]:
        assert contract["contract"] in flagged, contract
    critical = {f["subject_id"] for f in findings if f["severity"] == "critical"}
    auto_deadline = {c["contract"] for c in truth["P4"] if c["tag"] == "P4-auto-renew-deadline"}
    assert len(auto_deadline) == 2 and auto_deadline <= critical
    assert truth["controls"]["non_renewing_contract"] not in flagged


def test_vendor_cost_and_quiet_app_findings(pipeline, conn):
    truth = pipeline["truth"]
    findings = _findings(conn)
    vendor_flagged = {f["subject_id"] for f in findings if f["kind"] == "vendor_risk"}
    nordwind = _id_for(conn, "vendor", "vendor_id", truth["P2"]["vendor"])
    keel = _id_for(conn, "vendor", "vendor_id", truth["controls"]["noisy_stable_vendor"])
    assert nordwind in vendor_flagged and keel not in vendor_flagged
    assert vendor_flagged == {nordwind}

    cost_flagged = {f["subject_id"] for f in findings if f["kind"] == "cost_risk"}
    assert cost_flagged == {f"{truth['P8']['app']} / {truth['P8']['category']}"}

    quiet = {f["subject_id"] for f in findings if f["kind"] == "rationalization"}
    assert quiet == {_id_for(conn, "application", "app_id", truth["P11"]["app"])}


def test_p9_group_backlog_grows(pipeline, conn):
    truth = pipeline["truth"]
    start = date.fromisoformat(truth["P9"]["from"])
    periods = []
    day = start + timedelta(days=(7 - start.weekday()) % 7)  # first full ISO week
    while day + timedelta(days=7) <= AS_OF:
        iso = day.isocalendar()
        periods.append(parse_period(f"{iso.year}-W{iso.week:02d}", "Europe/Paris"))
        day += timedelta(days=7)
    rows = [r for r in group_flow(conn, Filters(kind="incident"), periods) if r["group"] == truth["P9"]["group"]]
    arrived = sum(r["arrived"] for r in rows)
    closed = sum(r["closed"] for r in rows)
    assert len(periods) >= 8 and arrived >= closed * 1.10, (arrived, closed)


def test_stale_open_matches_missed_resolutions(pipeline, conn):
    expected = set(pipeline["truth"]["stale_open_expected"])
    flagged = {r[0] for r in conn.execute("SELECT number FROM ticket WHERE stale_open = 1")}
    assert expected and flagged == expected


def _leaks(haystack: str, needles: list[str]) -> list[str]:
    return [n for n in needles if n in haystack]


def test_zero_pii_leaks(pipeline, conn):
    paths: Paths = pipeline["paths"]
    injections = json.loads((paths.ground_truth / "pii_injections.json").read_text(encoding="utf-8"))
    assert len(injections) > 500
    leaks: dict[str, list[str]] = {}
    tables = [
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE '%fts%'")
    ]
    for table in tables:
        if table in SKIP_LEAK_TABLES:
            continue
        cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})") if r["type"].upper() in {"TEXT", ""}]
        if not cols:
            continue
        concat = " || char(31) || ".join(f"COALESCE({c}, '')" for c in cols)
        text = "\x1f".join(r[0] for r in conn.execute(f"SELECT {concat} FROM {table}"))
        found = _leaks(text, injections)
        if found:
            leaks[table] = found[:5]
    for artifact in pipeline["report"]["artifacts"]:
        path = Path(artifact["path"])
        if path.suffix == ".md":
            text = path.read_text(encoding="utf-8")
        else:
            book = CalamineWorkbook.from_path(str(path))
            text = "\x1f".join(
                str(cell)
                for name in book.sheet_names
                for row in book.get_sheet_by_name(name).to_python()
                for cell in row
            )
        found = _leaks(text, injections)
        if found:
            leaks[path.name] = found[:5]
    assert leaks == {}


def test_weekly_report_artifacts(pipeline):
    report = pipeline["report"]
    names = sorted(Path(a["path"]).name for a in report["artifacts"])
    assert names == ["weekly_2026-W35_SYNTHETIC.md", "weekly_2026-W35_SYNTHETIC.xlsx"]
    book = CalamineWorkbook.from_path(next(a["path"] for a in report["artifacts"] if a["format"] == "xlsx"))
    assert {"Summary", "Definitions", "Provenance", "Renewals", "Licenses"} <= set(book.sheet_names)
    md = Path(next(a["path"] for a in report["artifacts"] if a["format"] == "md")).read_text(encoding="utf-8")
    assert "SYNTHETIC" in md and len(md.split()) <= 300


def test_reimport_is_a_no_op(pipeline, conn):
    paths: Paths = pipeline["paths"]
    again = run_import(paths, ImportOptions(inbox=True))
    assert again["summary"]["imported"] == 0

    processed = sorted(p for day_dir in paths.processed.iterdir() for p in day_dir.iterdir())  # incl. Confluence dirs
    replay = run_import(paths, ImportOptions(files=processed, move_files=False))
    assert replay["summary"]["imported"] == 0 and replay["summary"]["skipped"] == len(processed)

    # Same rows, different bytes (new sha): the upsert must still change nothing.
    source = next(
        p
        for p in processed
        if p.name.startswith("incident_") and p.suffix == ".csv" and not p.name.startswith("incident_active_")
    )
    copy = paths.data_dir / f"{source.stem}_resaved.csv"
    shutil.copyfile(source, copy)
    with copy.open("a", encoding="utf-8", newline="") as fh:
        fh.write("\n")
    before = conn.execute("SELECT COUNT(*), MAX(sys_updated_on) FROM ticket").fetchone()
    result = run_import(paths, ImportOptions(files=[copy], move_files=False, allow_unmanifested=True))
    file_result = result["files"][0]
    assert file_result["status"] == "completed", file_result
    assert file_result["inserted"] == 0 and file_result["updated"] == 0 and file_result["unchanged"] > 0
    assert tuple(conn.execute("SELECT COUNT(*), MAX(sys_updated_on) FROM ticket").fetchone()) == tuple(before)
