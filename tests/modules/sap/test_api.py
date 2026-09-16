"""/api/sap on the SAP profile: model-valid bodies, numbers equal to the L3 read models, filters that narrow, validation
envelopes, navigation, no people or injected PII in the JSON, and no writes from a GET."""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import BaseModel

from sed.calendar import as_of_end_utc, parse_period
from sed.modules.sap import api_models as m
from sed.modules.sap.queries import l3
from sed.modules.sap.scope import load_scope
from sed.settings import load_settings
from tests.fixtures.api import api_client
from tests.fixtures.ops_profile import AS_OF

OVERVIEW_KPIS = [
    "sap.l3.backlog",
    "sap.l3.aged_30d",
    "sap.l3.opened",
    "sap.l3.resolved",
    "sap.l3.sla.pct",
    "sap.l3.mttr.median_h",
    "sap.l3.p1p2.open",
    "sap.findings.count",
    "sap.changes.open",
    "sap.changes.urgent_ratio_8w",
    "sap.transports.failed_4w",
    "sap.idocs.errors_open",
    "sap.idocs.new_persistent",
]
EXPECTED_FINDINGS = {
    ("sap_idoc_risk", "sap_idoc_type", "HP1:INVOIC"),
    ("sap_idoc_risk", "sap_idoc_type", "EP1:ORDERS"),
    ("sap_idoc_risk", "sap_idoc_partner", "EP1:ORDERS:PARTNER_0007"),
    ("sap_idoc_risk", "sap_system", "HP1"),
    ("sap_idoc_risk", "sap_system", "EP1"),
    ("sap_backlog_risk", "sap_area", "ewm"),
    ("sap_change_risk", "sap_area", "pp_qm"),
    ("sap_change_risk", "sap_area", "mm"),
    ("sap_change_risk", "sap_transport", None),
    ("sap_change_risk", "sap_landscape", "ecc"),
}


@pytest.fixture(scope="module")
def client(sap_profile: Any):
    return api_client(sap_profile.paths)


def get_ok(client: Any, url: str, model: type[BaseModel], **params: Any) -> dict[str, Any]:
    r = client.get(url, params=params or None)
    assert r.status_code == 200, (url, params, r.text[:500])
    body = r.json()
    model.model_validate(body)
    return body


def _env(profile: Any) -> tuple[Any, Any, Any]:
    settings = load_settings(profile.paths)
    return load_scope(profile.paths), settings, as_of_end_utc(AS_OF, settings.reporting_tz)


def test_overview_kpis_areas_landscapes_and_findings(client, sap_profile, ro_conn):
    body = get_ok(client, "/api/sap/overview", m.SapOverview)
    assert body["as_of"] == AS_OF.isoformat() and body["period"] == "2026-W35" and body["configured"] is True
    kpis = {k["key"]: k for k in body["kpis"]}
    assert list(kpis) == OVERVIEW_KPIS
    assert all(k["definition"] for k in body["kpis"]), "every SAP KPI has a metric definition"

    scope, settings, at = _env(sap_profile)
    backlog = l3.backlog(ro_conn, scope, at)
    assert kpis["sap.l3.backlog"]["value"] == backlog["total"] > 0
    assert kpis["sap.l3.aged_30d"]["value"] == backlog["aging"]["d31_90"] + backlog["aging"]["d90p"]
    week = parse_period("2026-W35", settings.reporting_tz, settings.fiscal_year_start)
    weekly = l3.week_kpis(ro_conn, scope, week, [week.previous(k) for k in range(4, 0, -1)], "made_sla")
    assert kpis["sap.l3.opened"]["value"] == weekly["opened"]
    assert kpis["sap.l3.opened"]["compare"] == weekly["opened_avg"]
    assert kpis["sap.findings.count"]["value"] == len(body["findings"]) == 12

    assert [a["area"] for a in body["areas"]][:9] == [a.code for a in scope.config.areas]
    assert sum(a["open"] for a in body["areas"]) == backlog["total"]
    assert {x["landscape"] for x in body["landscapes"]} == {"ecc", "s4"}
    assert sum(x["open"] for x in body["landscapes"]) == backlog["total"]
    seen = {
        (f["kind"], f["subject_type"], None if f["subject_type"] == "sap_transport" else f["subject_id"])
        for f in body["findings"]
    }
    assert seen == EXPECTED_FINDINGS
    assert all(f["system_detected"] and f["origin"] == "rule" for f in body["findings"])


def test_l3_default_view(client, sap_profile, ro_conn):
    body = get_ok(client, "/api/sap/l3", m.SapL3Out)
    scope, _, at = _env(sap_profile)
    assert body["area"] is None and body["landscape"] is None
    assert [o["value"] for o in body["areas"]] == l3.area_order(scope)
    assert [o["value"] for o in body["landscapes"]] == ["ecc", "s4", "unknown"]
    assert body["backlog_total"] == l3.backlog(ro_conn, scope, at)["total"]
    assert body["backlog_total"] == sum(body["aging"].values()) == sum(r["total"] for r in body["by_area"])
    assert len(body["trend"]) == 12 and body["trend"][-1]["period"] == "2026-W35"
    assert len({r["period"] for r in body["flow"]}) <= 8
    assert body["attention_count"] >= len(body["attention"]) > 0
    ewm = [r for r in body["flow"] if r["area"] == "ewm"]
    assert sum(r["net"] > 0 for r in ewm) >= 6


def test_l3_area_and_landscape_filters_narrow(client):
    full = get_ok(client, "/api/sap/l3", m.SapL3Out)
    ewm = get_ok(client, "/api/sap/l3", m.SapL3Out, area="ewm")
    assert ewm["area"] == "ewm"
    assert [r["area"] for r in ewm["by_area"]] == ["ewm"]
    assert ewm["backlog_total"] == next(r["total"] for r in full["by_area"] if r["area"] == "ewm")
    assert {r["area"] for r in ewm["attention"]} == {"ewm"}
    assert all(e["opened"] <= f["opened"] for e, f in zip(ewm["trend"], full["trend"], strict=True))

    by_landscape = {x["landscape"]: x["open"] for x in full["by_landscape"]}
    for code in ("ecc", "s4"):
        narrowed = get_ok(client, "/api/sap/l3", m.SapL3Out, landscape=code)
        assert narrowed["backlog_total"] == by_landscape[code]
        assert {r["app"] for r in narrowed["attention"]} <= {"SAP ECC", "SAP S/4HANA"}
    unknown = get_ok(client, "/api/sap/l3", m.SapL3Out, landscape="unknown")
    assert unknown["backlog_total"] == 0 and unknown["attention"] == []
    unassigned = get_ok(client, "/api/sap/l3", m.SapL3Out, area="unassigned")
    assert {r["assignment_group"] for r in unassigned["attention"]} <= {"IT-SERVICE-DESK-L1"}


def test_l3_period_filter_moves_the_measurement_point(client):
    body = get_ok(client, "/api/sap/l3", m.SapL3Out, period="2026-W30")
    assert body["at"] == "2026-07-26T22:00:00Z"  # backlog and attention at the end of the selected week
    assert body["trend"][-1]["period"] == "2026-W30" and len(body["trend"]) == 12  # series end with that week too
    later = get_ok(client, "/api/sap/l3", m.SapL3Out, period="2026-W35")
    assert later["backlog_total"] > body["backlog_total"]  # EWM tickets pile up between W30 and W35


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"area": "hr"}, "Unknown SAP area 'hr'"),
        ({"landscape": "bw"}, "Unknown SAP landscape 'bw'"),
        ({"weeks": 3}, None),
        ({"weeks": 53}, None),
        ({"area": "x" * 33}, None),
    ],
)
def test_l3_validation_errors_use_the_envelope(client, params, message):
    r = client.get("/api/sap/l3", params=params)
    assert r.status_code == 422, r.text
    error = r.json()["error"]
    assert error["kind"] == "validation"
    if message:
        assert error["message"] == message


def test_attention_rows_open_the_ops_ticket_detail(client):
    from urllib.parse import quote

    rows = get_ok(client, "/api/sap/l3", m.SapL3Out, area="ewm")["attention"]
    assert rows and all(r["ticket_id"] == f"incident:{r['number']}" for r in rows)
    detail = client.get(f"/api/ops/tickets/{quote(rows[0]['ticket_id'], safe='')}")
    assert detail.status_code == 200 and detail.json()["number"] == rows[0]["number"]


def test_nav_lists_the_sap_pages(client):
    items = client.get("/api/nav").json()["items"]
    sap = [i for i in items if i["id"].startswith("sap.")]
    assert [(i["id"], i["path"]) for i in sap] == [
        ("sap.overview", "/sap"),
        ("sap.tickets", "/sap/tickets"),
        ("sap.changes", "/sap/changes"),
        ("sap.idocs", "/sap/idocs"),
    ]
    modules = {mod["key"]: mod for mod in client.get("/api/modules").json()["modules"]}
    assert modules["sap"]["enabled"] is True


def test_responses_carry_no_people_or_injected_pii(client, sap_truth, ro_conn):
    texts = [
        client.get("/api/sap/overview").text,
        client.get("/api/sap/l3").text,
        client.get("/api/sap/l3", params={"area": "unassigned"}).text,
        client.get("/api/sap/changes").text,
        client.get("/api/sap/idocs").text,
    ]
    for text in texts:
        assert "_pid" not in text and "caller" not in text
        for value in sap_truth["pii"]:
            assert value not in text, value
    body = json.loads(texts[1])
    assert body["attention"] and all(set(row) == set(m.SapAttentionRow.model_fields) for row in body["attention"])


def test_disabled_module_routes_are_not_served(sap_profile_rw):
    config = sap_profile_rw.paths.config
    config.mkdir(parents=True, exist_ok=True)
    (config / "modules.yaml").write_text("enabled: [ops]\n", encoding="utf-8")
    client = api_client(sap_profile_rw.paths)
    assert client.get("/api/sap/overview").status_code == 404
    assert not [i for i in client.get("/api/nav").json()["items"] if i["id"].startswith("sap.")]


def test_changes_view_agrees_with_the_read_models(client, sap_profile, ro_conn, sap_truth):
    from datetime import timedelta

    from sed.calendar import iso_utc
    from sed.modules.sap.charm import load_charm
    from sed.modules.sap.queries import changes

    body = get_ok(client, "/api/sap/changes", m.SapChangesOut)
    assert body["period"] == "2026-W35" and body["area"] is None
    kpis = {k["key"]: k for k in body["kpis"]}
    assert list(kpis) == [
        "sap.changes.open",
        "sap.changes.urgent_ratio_8w",
        "sap.changes.stuck",
        "sap.changes.without_jira",
        "sap.changes.prod_imports",
        "sap.transports.failed_4w",
        "sap.transports.waiting",
    ]
    assert all(k["definition"] for k in body["kpis"])
    scope, settings, at = _env(sap_profile)
    resolved = scope.resolve(ro_conn)
    cs = changes.load(ro_conn, load_charm(sap_profile.paths, resolved), at)
    week = parse_period("2026-W35", settings.reporting_tz, settings.fiscal_year_start)
    summary = changes.summary(ro_conn, cs, week)
    assert kpis["sap.changes.open"]["value"] == summary["open"] == sum(r["total"] for r in body["stages"])
    assert kpis["sap.changes.urgent_ratio_8w"]["value"] == summary["urgent_ratio_8w"]
    assert kpis["sap.changes.urgent_ratio_8w"]["compare"] == summary["urgent_ratio_previous_8w"]
    assert kpis["sap.changes.stuck"]["value"] == len(body["stuck"]) == 4
    assert kpis["sap.transports.waiting"]["value"] == len(body["waiting"]) == 6
    assert kpis["sap.changes.without_jira"]["value"] == body["without_jira_count"] == 5
    assert kpis["sap.transports.failed_4w"]["value"] == 1
    assert len(body["production_imports"]) == 12 and body["production_imports"][-1]["period"] == "2026-W35"
    cp1 = sap_truth["patterns"]["changes"]["CP1"]
    assert body["incidents_after_imports"][0]["change_id"] == cp1["change_id"]
    assert body["incidents_after_imports"][0]["incidents"] == cp1["incidents"]
    assert {r["transport"] for r in body["failed"]} == {cp1["transport"]}
    overview = {k["key"]: k["value"] for k in client.get("/api/sap/overview").json()["kpis"]}
    for key in ("sap.changes.open", "sap.changes.urgent_ratio_8w", "sap.transports.failed_4w"):
        assert overview[key] == kpis[key]["value"], key
    assert iso_utc(at - timedelta(days=28)) < cp1_imported(ro_conn, cp1["transport"])


def cp1_imported(conn, transport):
    return conn.execute(
        "SELECT imported_at FROM sap_transport_import WHERE transport = ? AND system_id = 'HP1'", (transport,)
    ).fetchone()[0]


def test_changes_view_filters_narrow(client):
    full = get_ok(client, "/api/sap/changes", m.SapChangesOut)
    mm = get_ok(client, "/api/sap/changes", m.SapChangesOut, area="mm")
    assert mm["stuck"] == [] and mm["waiting"] == [] and mm["failed"] == []
    assert {r["area"] for r in mm["incidents_after_imports"]} <= {"mm"}
    assert mm["urgent_by_area"] == full["urgent_by_area"]  # the area comparison always covers every area
    ecc = get_ok(client, "/api/sap/changes", m.SapChangesOut, landscape="ecc")
    assert {r["landscape"] for r in ecc["waiting"]} == {"ecc"} and ecc["failed"] == []
    s4 = get_ok(client, "/api/sap/changes", m.SapChangesOut, landscape="s4")
    assert s4["waiting"] == [] and {r["system_id"] for r in s4["failed"]} == {"HP1"}
    opened = {k["key"]: k["value"] for k in full["kpis"]}["sap.changes.open"]
    parts = [{k["key"]: k["value"] for k in v["kpis"]}["sap.changes.open"] for v in (ecc, s4)]
    unknown = get_ok(client, "/api/sap/changes", m.SapChangesOut, landscape="unknown")
    assert sum(parts) + {k["key"]: k["value"] for k in unknown["kpis"]}["sap.changes.open"] == opened
    for params in ({"area": "hr"}, {"landscape": "bw"}, {"weeks": 7}, {"weeks": 53}):
        assert client.get("/api/sap/changes", params=params).status_code == 422, params


def test_idocs_view_agrees_with_the_read_models(client, sap_profile, ro_conn, sap_truth):
    from sed.modules.sap.idoc import load_idoc
    from sed.modules.sap.queries import idocs

    body = get_ok(client, "/api/sap/idocs", m.SapIdocsOut)
    kpis = {k["key"]: k for k in body["kpis"]}
    assert list(kpis) == [
        "sap.idocs.errors_open",
        "sap.idocs.errors_aged",
        "sap.idocs.new_persistent",
        "sap.idocs.reprocess_median_h",
        "sap.idocs.reprocessed_in_grace_pct",
    ]
    assert all(k["definition"] for k in body["kpis"])
    scope, settings, at = _env(sap_profile)
    ids = idocs.load(ro_conn, load_idoc(sap_profile.paths, scope), at)
    week = parse_period("2026-W35", settings.reporting_tz, settings.fiscal_year_start)
    summary = idocs.summary(ids, week)
    assert kpis["sap.idocs.errors_open"]["value"] == summary["errors_open"] == body["open_errors_count"]
    assert sum(body["aging"].values()) == summary["errors_open"] == sum(r["errors"] for r in body["by_type"])
    assert kpis["sap.idocs.new_persistent"]["value"] == summary["new_persistent_week"]
    assert kpis["sap.idocs.new_persistent"]["compare"] == summary["new_persistent_avg4w"]
    assert len(body["weekly"]) == 12 and body["weekly"][-1]["period"] == "2026-W35"
    cp1 = sap_truth["patterns"]["changes"]["CP1"]["change_id"]
    assert [(s["change_id"], s["system_id"]) for s in body["spikes"]] == [(cp1, "HP1")]
    assert body["partners"][0]["partner"] == "PARTNER_0007"
    assert [o["value"] for o in body["systems"]][:6] == ["ED1", "EQ1", "EP1", "HD1", "HQ1", "HP1"]
    overview = {k["key"]: k["value"] for k in client.get("/api/sap/overview").json()["kpis"]}
    assert overview["sap.idocs.errors_open"] == summary["errors_open"]


def test_idocs_view_filters_and_validation(client):
    full = get_ok(client, "/api/sap/idocs", m.SapIdocsOut)
    ep1 = get_ok(client, "/api/sap/idocs", m.SapIdocsOut, system="EP1")
    hp1 = get_ok(client, "/api/sap/idocs", m.SapIdocsOut, system="HP1")
    assert ep1["open_errors_count"] + hp1["open_errors_count"] == full["open_errors_count"]
    assert {r["system_id"] for r in ep1["by_type"]} == {"EP1"} and ep1["spikes"] == []
    ecc = get_ok(client, "/api/sap/idocs", m.SapIdocsOut, landscape="ecc")
    assert ecc["open_errors_count"] == ep1["open_errors_count"]
    inbound = get_ok(client, "/api/sap/idocs", m.SapIdocsOut, direction="inbound")
    assert {r["direction"] for r in inbound["by_type"]} == {"inbound"}
    fi = get_ok(client, "/api/sap/idocs", m.SapIdocsOut, area="fi_co")
    assert {r["message_type"] for r in fi["by_type"]} <= {"INVOIC"}
    for params in ({"system": "ZZ1"}, {"direction": "both"}, {"area": "hr"}, {"weeks": 3}):
        r = client.get("/api/sap/idocs", params=params)
        assert r.status_code == 422 and r.json()["error"]["kind"] == "validation", params


def test_idoc_spikes_follow_the_error_filters(client):
    outbound = get_ok(client, "/api/sap/idocs", m.SapIdocsOut, direction="outbound")
    inbound = get_ok(client, "/api/sap/idocs", m.SapIdocsOut, direction="inbound")
    assert [s["system_id"] for s in outbound["spikes"]] == ["HP1"] and outbound["spikes"][0]["transport"]
    assert inbound["spikes"] == []  # the IP1 errors after the CP1 import are outbound INVOIC IDocs
