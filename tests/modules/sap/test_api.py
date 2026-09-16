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
]


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
    assert kpis["sap.findings.count"]["value"] == len(body["findings"]) == 2

    assert [a["area"] for a in body["areas"]][:9] == [a.code for a in scope.config.areas]
    assert sum(a["open"] for a in body["areas"]) == backlog["total"]
    assert {x["landscape"] for x in body["landscapes"]} == {"ecc", "s4"}
    assert sum(x["open"] for x in body["landscapes"]) == backlog["total"]
    assert {(f["kind"], f["subject_type"], f["subject_id"]) for f in body["findings"]} == {
        ("sap_backlog_risk", "sap_area", "ewm")
    }
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
    assert [(i["id"], i["path"]) for i in sap] == [("sap.overview", "/sap"), ("sap.tickets", "/sap/tickets")]
    modules = {mod["key"]: mod for mod in client.get("/api/modules").json()["modules"]}
    assert modules["sap"]["enabled"] is True


def test_responses_carry_no_people_or_injected_pii(client, sap_truth, ro_conn):
    texts = [
        client.get("/api/sap/overview").text,
        client.get("/api/sap/l3").text,
        client.get("/api/sap/l3", params={"area": "unassigned"}).text,
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
