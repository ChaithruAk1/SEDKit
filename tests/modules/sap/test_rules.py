"""SAP rule findings (sap_backlog_risk): the planted EWM growth and aged backlog fire, the SD surge control and the
background areas stay silent, thresholds come from config/sap/risk_rules.yaml, and a SAP refresh never touches ops
findings."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import pytest

from sed import db, modules, rule_findings
from sed.errors import ValidationFailed
from sed.modules.sap import rules
from tests.fixtures.ops_profile import AS_OF
from tests.modules.sap.conftest import write_sap_config

KINDS = ("sap_backlog_risk",)
GROWTH, AGED = "sap_backlog_risk:growth:ewm", "sap_backlog_risk:aged:ewm"


def _published(paths: Any, kinds: tuple[str, ...] = KINDS) -> dict[str, dict[str, Any]]:
    conn = db.connect(paths.db, readonly=True)
    try:
        return {f["stable_key"]: f for f in rule_findings.published(conn, AS_OF, kinds)}
    finally:
        conn.close()


def _evidence(finding: dict[str, Any]) -> dict[str, Any]:
    return {e["fact_key"]: e["value"] for e in json.loads(finding["payload_json"])["evidence"]}


def _refresh(paths: Any, as_of: date = AS_OF, **kw: Any) -> dict[str, Any]:
    conn = db.connect(paths.db)
    try:
        return rule_findings.refresh(conn, paths, as_of, "sap", **kw)
    finally:
        conn.close()


def test_planted_ewm_risks_fire_and_nothing_else(sap_profile):
    found = _published(sap_profile.paths)
    assert set(found) == {GROWTH, AGED}
    growth = found[GROWTH]
    assert (growth["kind"], growth["subject_type"], growth["subject_id"]) == ("sap_backlog_risk", "sap_area", "ewm")
    evidence = _evidence(growth)
    assert evidence["sap.area.ewm.weeks_growing"] >= 6 and evidence["sap.area.ewm.net_growth"] >= 30
    assert evidence["sap.area.ewm.window_end"] == "2026-W35"
    assert growth["severity"] == "high"  # net growth of at least twice the threshold
    assert growth["title"].startswith(f"SAP EWM backlog growing: +{evidence['sap.area.ewm.net_growth']} tickets")
    aged = _evidence(found[AGED])
    assert aged["sap.area.ewm.aged_30d"] >= 10 and aged["sap.area.ewm.open"] >= aged["sap.area.ewm.aged_30d"]


def test_compute_matches_the_persisted_findings(sap_profile, ro_conn):
    computed = rules.compute(ro_conn, sap_profile.paths, AS_OF)
    kinds = tuple(modules.get("sap").finding_kinds)
    assert {f["stable_key"] for f in computed} == set(_published(sap_profile.paths, kinds))
    assert db.get_meta(ro_conn, rule_findings.state_key("sap")) == AS_OF.isoformat()


def test_sap_findings_do_not_change_ops_findings(sap_profile, ops_profile):
    ops_kinds = tuple(modules.get("ops").finding_kinds)
    assert not set(ops_kinds) & set(KINDS)

    def rows(paths: Any) -> set[tuple[Any, ...]]:
        return {(k, f["status"], f["severity"], f["title"]) for k, f in _published(paths, ops_kinds).items()}

    assert rows(sap_profile.paths) == rows(ops_profile.paths) != set()


def test_refresh_supersedes_only_sap_kinds(sap_profile_rw):
    paths = sap_profile_rw.paths
    ops_kinds = tuple(modules.get("ops").finding_kinds)
    ops_before = set(_published(paths, ops_kinds))
    write_sap_config(
        paths,
        "risk_rules.yaml",
        {"rules": {"area_backlog_growth": {"enabled": False}, "area_aged_backlog": {"enabled": False}}},
    )
    stats = _refresh(paths)
    assert stats["superseded"] == 2 and stats["inserted"] == 0
    assert _published(paths) == {}
    assert set(_published(paths, ops_kinds)) == ops_before
    conn = db.connect(paths.db, readonly=True)
    try:
        statuses = dict(
            conn.execute("SELECT stable_key, status FROM finding WHERE kind = 'sap_backlog_risk'").fetchall()
        )
    finally:
        conn.close()
    assert statuses == {GROWTH: "superseded", AGED: "superseded"}


def test_thresholds_come_from_the_config(sap_profile_rw):
    paths = sap_profile_rw.paths
    net = _evidence(_published(paths)[GROWTH])["sap.area.ewm.net_growth"]
    write_sap_config(paths, "risk_rules.yaml", {"rules": {"area_backlog_growth": {"min_net_growth": net // 2 + 1}}})
    _refresh(paths)
    found = _published(paths)
    assert found[GROWTH]["severity"] == "medium"  # above the threshold but below twice it
    write_sap_config(
        paths,
        "risk_rules.yaml",
        {"rules": {"area_backlog_growth": {"min_weeks_growing": 9}, "area_aged_backlog": {"min_tickets": 10_000}}},
    )
    _refresh(paths)
    assert _published(paths) == {}


def test_acknowledged_finding_stays_hidden_until_evidence_changes(sap_profile_rw):
    paths = sap_profile_rw.paths
    conn = db.connect(paths.db)
    try:
        with db.write_tx(conn):
            conn.execute(
                "UPDATE finding SET status = 'acknowledged' WHERE stable_key = ? AND status = 'active'", (AGED,)
            )
    finally:
        conn.close()
    assert _refresh(paths)["hidden"] == 1
    assert set(_published(paths)) == {GROWTH}


def test_unconfigured_scope_computes_nothing(sap_profile_rw, ro_conn):
    paths = sap_profile_rw.paths
    write_sap_config(paths, "scope.yaml", {"groups": [], "categories": [], "custom_fields": []})
    assert rules.compute(ro_conn, paths, AS_OF) == []


def test_invalid_rules_file_is_a_validation_error(sap_profile_rw, ro_conn):
    paths = sap_profile_rw.paths
    write_sap_config(paths, "risk_rules.yaml", {"rules": {"area_backlog_growth": {"window_weeks": 1}}})
    with pytest.raises(ValidationFailed) as info:
        rules.compute(ro_conn, paths, AS_OF)
    assert info.value.message == "Invalid sap/risk_rules.yaml"
    assert "area_backlog_growth.window_weeks" in json.dumps(info.value.details)


def test_findings_for_an_older_as_of_are_computed_read_only(sap_profile_rw):
    paths = sap_profile_rw.paths
    conn = db.connect(paths.db)
    try:
        before = conn.execute("SELECT COUNT(*), MAX(created_at) FROM finding").fetchone()
        early = rule_findings.as_of_findings(conn, paths, date(2026, 7, 6), "sap")  # before the EWM growth started
        mid = rule_findings.as_of_findings(conn, paths, date(2026, 8, 17), "sap")
        after = conn.execute("SELECT COUNT(*), MAX(created_at) FROM finding").fetchone()
        state = db.get_meta(conn, rule_findings.state_key("sap"))
    finally:
        conn.close()
    assert GROWTH not in {f["stable_key"] for f in early}
    assert GROWTH in {f["stable_key"] for f in mid}  # W26-W33: six growth weeks (W28-W33) are enough
    assert next(f for f in mid if f["stable_key"] == GROWTH)["finding_id"]  # the persisted id is reused
    assert tuple(before) == tuple(after) and state == AS_OF.isoformat()


CHANGE_KINDS = ("sap_change_risk",)


def test_planted_change_risks_fire_and_nothing_else(sap_profile, sap_truth):
    found = _published(sap_profile.paths, CHANGE_KINDS)
    assert set(found) == set(sap_truth["patterns"]["changes"]["expected_findings"])
    cp1 = sap_truth["patterns"]["changes"]["CP1"]
    failed = found[f"sap_change_risk:failed_import:{cp1['transport']}:HP1"]
    assert (failed["subject_type"], failed["subject_id"], failed["severity"]) == (
        "sap_transport",
        cp1["transport"],
        "high",
    )
    assert _evidence(failed) == {
        f"sap.transport.{cp1['transport']}.return_code": 8,
        f"sap.transport.{cp1['transport']}.incidents_after": cp1["incidents"],
    }
    assert "14 SAP incidents within 72 h" in failed["title"]
    urgent = _evidence(found["sap_change_risk:urgent_ratio:mm"])
    assert urgent["sap.changes.mm.urgent_ratio_pct"] >= 50 and urgent["sap.changes.mm.urgent_ratio_previous_pct"] <= 15
    assert _evidence(found["sap_change_risk:stuck:pp_qm"])["sap.changes.pp_qm.stuck"] == 4
    assert found["sap_change_risk:stuck:pp_qm"]["severity"] == "medium"  # 45 days against 30: under twice the limit
    assert _evidence(found["sap_change_risk:waiting:ecc"])["sap.transports.ecc.waiting"] == 6


def test_change_rule_thresholds_and_switches(sap_profile_rw):
    paths = sap_profile_rw.paths
    write_sap_config(
        paths,
        "risk_rules.yaml",
        {
            "rules": {
                "change_stuck": {"min_changes": 5},
                "urgent_ratio": {"enabled": False},
                "failed_production_import": {"lookback_days": 1},
                "waiting_for_production": {"min_transports": 7},
            }
        },
    )
    _refresh(paths)
    assert _published(paths, CHANGE_KINDS) == {}
    write_sap_config(paths, "charm.yaml", {"thresholds": {"stuck_days": {"in_test": 60}}})
    write_sap_config(paths, "risk_rules.yaml", {"rules": {"waiting_for_production": {"min_transports": 1}}})
    _refresh(paths)
    found = set(_published(paths, CHANGE_KINDS))
    assert "sap_change_risk:stuck:pp_qm" not in found  # 45 days in test is within a 60-day limit
    assert {"sap_change_risk:urgent_ratio:mm", "sap_change_risk:waiting:ecc"} <= found


IDOC_KINDS = ("sap_idoc_risk",)


def test_planted_idoc_risks_fire_and_the_controls_stay_silent(sap_profile, sap_truth):
    found = _published(sap_profile.paths, IDOC_KINDS)
    assert set(found) == set(sap_truth["patterns"]["idocs"]["expected_findings"])
    growth = found["sap_idoc_risk:growth:EP1:ORDERS:PARTNER_0007"]
    assert _evidence(growth) == {
        "sap.idocs.EP1:ORDERS:PARTNER_0007.persistent_week": 24,
        "sap.idocs.EP1:ORDERS:PARTNER_0007.persistent_avg4w": 9.5,
    }
    cp1 = sap_truth["patterns"]["changes"]["CP1"]["change_id"]
    spike = found[f"sap_idoc_risk:spike:HP1:{cp1}"]
    assert spike["severity"] == "high" and "(0 before)" in spike["title"]
    assert not any("MATMAS" in key for key in found)  # IN1: reprocessed within the grace time
    assert found["sap_idoc_risk:backlog:EP1:ORDERS"]["severity"] == "high"  # 64 errors: twice the threshold


def test_idoc_rule_thresholds_and_grace(sap_profile_rw):
    paths = sap_profile_rw.paths
    write_sap_config(
        paths,
        "risk_rules.yaml",
        {
            "rules": {
                "idoc_error_backlog": {"min_errors": 100},
                "idoc_error_growth": {"enabled": False},
                "idoc_aged_errors": {"min_errors": 100},
                "idoc_spike_after_import": {"min_lift": 100},
            }
        },
    )
    _refresh(paths)
    assert _published(paths, IDOC_KINDS) == {}
    # A grace time longer than the IN1 reprocessing still leaves it quiet; a short one turns it into persistent errors.
    write_sap_config(paths, "risk_rules.yaml", {"rules": {"idoc_error_growth": {"min_errors_week": 70}}})
    write_sap_config(paths, "idoc.yaml", {"thresholds": {"reprocess_grace_hours": 2}})
    _refresh(paths)
    found = set(_published(paths, IDOC_KINDS))
    assert "sap_idoc_risk:growth:EP1:MATMAS:EP1CLNT100" in found
