"""sap-weekly on the SAP profile: every format builds cleanly, facts agree with the L3 read models and the API, the
risks table carries the planted findings, open weeks are labelled "to date", provenance lists only SAP suppressions,
and no injected PII reaches an output."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from sed import db, modules
from sed.calendar import parse_period
from sed.errors import PreconditionFailed, ValidationFailed
from sed.modules.sap.queries import l3
from sed.modules.sap.scope import load_scope
from sed.reports.build import build_report
from sed.reports.snapshot import Snapshot, create_snapshot
from sed.settings import load_settings
from tests.fixtures.api import api_client
from tests.platform.reports.test_pptx_builder import deck_problems, open_deck, slide_text

PERIOD = "2026-W35"


def _snapshot(profile: Any, report: str = "sap-weekly", period: str = PERIOD) -> Snapshot:
    conn = db.connect(profile.paths.db)
    try:
        return create_snapshot(conn, profile.paths, report, period)
    finally:
        conn.close()


def _value(snapshot: Snapshot, key: str) -> Any:
    return snapshot.facts[key]["value"]


def test_every_format_builds(sap_profile_rw, sap_truth):
    result = build_report(sap_profile_rw.paths, "sap-weekly", PERIOD, None, "none")
    artifacts = {a["format"]: Path(a["path"]) for a in result["artifacts"]}
    assert list(artifacts) == ["xlsx", "md", "pptx"]
    assert all(
        p.is_file() and "_SYNTHETIC" in p.name and p.name.startswith("sap-weekly_2026-W35") for p in artifacts.values()
    )

    assert deck_problems(artifacts["pptx"]) == []
    deck = open_deck(artifacts["pptx"])
    deck_text = "\n".join(slide_text(s) for s in deck.slides)
    assert deck.slides[-1].shapes.title.text_frame.text.startswith("Provenance")
    assert "SAP EWM backlog growing" in deck_text and "SAP scope: 9 SAP groups" in deck_text

    from python_calamine import CalamineWorkbook

    book = CalamineWorkbook.from_path(str(artifacts["xlsx"]))
    assert {"Summary", "Volume trend", "Areas", "Backlog aging", "Landscapes", "Area flow", "SLA", "Needs attention",
            "Risks", "Definitions", "Provenance"} <= set(book.sheet_names)  # fmt: skip
    cells = "\n".join(
        str(v) for name in book.sheet_names for row in book.get_sheet_by_name(name).to_python() for v in row
    )
    assert "sap_backlog_risk" in cells and "sap.l3.backlog" in cells

    md = artifacts["md"].read_text(encoding="utf-8")
    assert md.startswith("> **SYNTHETIC DATA**")
    assert "**Weekly SAP Operations Review – 2026-W35**" in md and "[high] SAP EWM backlog growing" in md
    assert "{{" not in md and len(md.split()) <= 250 + 1
    for text in (deck_text, cells, md):
        for value in sap_truth["pii"]:
            assert value not in text, value


def test_facts_agree_with_the_read_models_and_the_api(sap_profile_rw):
    paths = sap_profile_rw.paths
    snap = _snapshot(sap_profile_rw)
    settings = load_settings(paths)
    scope = load_scope(paths)
    week = parse_period(PERIOD, settings.reporting_tz, settings.fiscal_year_start)
    conn = db.connect(paths.db, readonly=True)
    try:
        backlog = l3.backlog(conn, scope, week.end_utc)
        kpis = l3.week_kpis(conn, scope, week, [week.previous(k) for k in range(4, 0, -1)], snap.sla_source)
        areas = l3.area_summary(conn, scope, week, week.end_utc, snap.sla_source)
    finally:
        conn.close()
    assert _value(snap, "sap.l3.backlog") == backlog["total"] > 0
    assert _value(snap, "sap.l3.aged_30d") == backlog["aging"]["d31_90"] + backlog["aging"]["d90p"]
    assert _value(snap, "sap.l3.opened") == kpis["opened"] and _value(snap, "sap.l3.opened.avg4w") == kpis["opened_avg"]
    assert _value(snap, "sap.l3.resolved") == kpis["resolved"]
    assert _value(snap, "sap.l3.sla.pct") == kpis["sla_pct"]
    assert _value(snap, "sap.l3.mttr.median_h") == kpis["mttr_median_h"]
    assert _value(snap, "sap.l3.sla.source") == snap.sla_source
    rows = snap.tables["sap_areas"]["rows"]
    assert rows == [{k: r[k] for k in rows[0]} for r in areas]
    assert sum(r["total"] for r in snap.tables["sap_backlog_aging_by_area"]["rows"]) == backlog["total"]
    assert len(snap.tables["sap_l3_trend_12w"]["rows"]) == 12
    assert _value(snap, "sap.findings.count") == len(snap.tables["sap_findings"]["rows"]) == 6
    assert (
        _value(snap, "sap.scope.note")
        == "SAP scope: 9 SAP groups, 1 category name, 1 custom field (config/sap/scope.yaml)"
    )
    assert "(to date)" not in snap.facts["sap.l3.opened"]["label"]

    api = {k["key"]: k for k in api_client(paths).get("/api/sap/overview").json()["kpis"]}
    for key in ("sap.l3.opened", "sap.l3.resolved", "sap.l3.sla.pct", "sap.l3.mttr.median_h"):
        assert api[key]["value"] == _value(snap, key), key


def test_open_week_is_labelled_to_date(sap_profile_rw):
    snap = _snapshot(sap_profile_rw, period="2026-W36")
    assert snap.facts["sap.l3.opened"]["label"].endswith("(to date)")
    assert _value(snap, "sap.l3.opened.delta_vs_avg4w_pct") is None
    assert snap.facts["sap.l3.backlog"]["label"] == "Open SAP backlog at the as-of date"


def test_provenance_lists_only_sap_suppressions(sap_profile_rw):
    paths = sap_profile_rw.paths
    ops_kinds = tuple(modules.get("ops").finding_kinds)
    conn = db.connect(paths.db)
    try:
        marks = ", ".join("?" for _ in ops_kinds)
        ops_key = conn.execute(
            f"SELECT stable_key FROM finding WHERE origin = 'rule' AND status = 'active' AND kind IN ({marks}) "
            "ORDER BY stable_key LIMIT 1",
            ops_kinds,
        ).fetchone()[0]
        with db.write_tx(conn):
            conn.execute(
                "UPDATE finding SET status = 'acknowledged' WHERE status = 'active' AND stable_key IN (?, ?)",
                (ops_key, "sap_backlog_risk:aged:ewm"),
            )
    finally:
        conn.close()
    sap = _snapshot(sap_profile_rw)
    assert [f["stable_key"] for f in sap.suppressed_findings] == ["sap_backlog_risk:aged:ewm"]
    assert _value(sap, "sap.findings.count") == 5
    ops = _snapshot(sap_profile_rw, report="weekly")
    assert ops_key in {f["stable_key"] for f in ops.suppressed_findings}
    assert not any(f["kind"] == "sap_backlog_risk" for f in ops.suppressed_findings)


def test_needs_a_week_period(sap_profile_rw):
    with pytest.raises(ValidationFailed, match="needs a week period"):
        _snapshot(sap_profile_rw, period="2026-08")


def test_refused_when_the_module_is_disabled(sap_profile_rw):
    config = sap_profile_rw.paths.config
    config.mkdir(parents=True, exist_ok=True)
    (config / "modules.yaml").write_text("enabled: [ops]\n", encoding="utf-8")
    with pytest.raises(PreconditionFailed, match="Module 'sap' is disabled"):
        build_report(sap_profile_rw.paths, "sap-weekly", PERIOD, ["md"], "none")


def test_change_facts_and_tables(sap_profile_rw, sap_truth):
    from sed.modules.sap.charm import load_charm
    from sed.modules.sap.queries import changes

    paths = sap_profile_rw.paths
    snap = _snapshot(sap_profile_rw)
    settings = load_settings(paths)
    week = parse_period(PERIOD, settings.reporting_tz, settings.fiscal_year_start)
    conn = db.connect(paths.db, readonly=True)
    try:
        scope = load_scope(paths).resolve(conn)
        cs = changes.load(conn, load_charm(paths, scope), week.end_utc)
        summary = changes.summary(conn, cs, week)
    finally:
        conn.close()
    assert _value(snap, "sap.changes.open") == summary["open"]
    assert _value(snap, "sap.changes.urgent_ratio_8w") == summary["urgent_ratio_8w"]
    assert _value(snap, "sap.changes.stuck") == summary["stuck"] == len(snap.tables["sap_changes_stuck"]["rows"]) == 4
    assert _value(snap, "sap.transports.waiting") == len(snap.tables["sap_transports_waiting"]["rows"]) == 6
    assert _value(snap, "sap.changes.without_jira") == len(snap.tables["sap_changes_without_jira"]["rows"]) == 5
    cp1 = sap_truth["patterns"]["changes"]["CP1"]
    assert [r["transport"] for r in snap.tables["sap_transports_failed"]["rows"]] == [cp1["transport"]]
    after = snap.tables["sap_incidents_after_imports"]["rows"]
    assert after[0]["change_id"] == cp1["change_id"] and after[0]["incidents"] == cp1["incidents"]
    assert len(snap.tables["sap_prod_imports_12w"]["rows"]) == 12
    assert sum(r["total"] for r in snap.tables["sap_change_stages"]["rows"]) == summary["open"]
    mm = next(r for r in snap.tables["sap_urgent_by_area"]["rows"] if r["label"] == "MM")
    assert mm["delta_pp"] >= 35

    result = build_report(paths, "sap-weekly", PERIOD, ["xlsx", "md"], "none")
    files = {a["format"]: Path(a["path"]) for a in result["artifacts"]}
    from python_calamine import CalamineWorkbook

    sheets = set(CalamineWorkbook.from_path(str(files["xlsx"])).sheet_names)
    assert {"Change stages", "Urgent changes", "Production imports", "Failed imports", "Incidents after imports",
            "Stuck changes", "Waiting for production", "Changes without Jira"} <= sheets  # fmt: skip
    md = files["md"].read_text(encoding="utf-8")
    assert "- **Changes:** " in md and "transports waiting for production" in md
