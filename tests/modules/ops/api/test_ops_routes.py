"""Every /api/ops GET on the deterministic ops_profile: 200 with model-valid bodies, filters that narrow, 404 envelopes,
URL-encoded ticket ids, and no writes (no snapshot, no rule refresh) reachable from a GET."""

from __future__ import annotations

from datetime import date
from typing import Any
from urllib.parse import quote

import pytest
from pydantic import BaseModel

from sed.calendar import parse_period
from sed.modules.ops import api_models as m
from tests.fixtures.api import TEST_TOKEN

AS_OF = date(2026, 9, 1)
TZ = "Europe/Paris"
OVERVIEW_KPIS = [
    "inc.backlog",
    "inc.sla.pct",
    "inc.mttr.median_h",
    "inc.p1p2.opened",
    "cost.actual.ytd",
    "cost.budget.ytd",
    "cost.variance.ytd_pct",
    "renewals.90d.count",
    "notice.30d.count",
    "license.idle_cost",
    "review.queue.count",
]


def routes(ids: dict[str, Any]) -> dict[str, tuple[str, type[BaseModel]]]:
    """openapi path -> (concrete URL, response model) for every ops GET."""
    return {
        "/api/ops/filters": ("/api/ops/filters", m.OpsFiltersOut),
        "/api/ops/overview": ("/api/ops/overview", m.OpsOverview),
        "/api/ops/attention": ("/api/ops/attention", m.AttentionOut),
        "/api/ops/tickets": ("/api/ops/tickets", m.TicketPage),
        "/api/ops/tickets/volumes": ("/api/ops/tickets/volumes", m.VolumesOut),
        "/api/ops/tickets/sla": ("/api/ops/tickets/sla", m.SlaOut),
        "/api/ops/tickets/mttr": ("/api/ops/tickets/mttr", m.MttrOut),
        "/api/ops/tickets/backlog": ("/api/ops/tickets/backlog", m.BacklogOut),
        "/api/ops/tickets/{ticket_id}": (f"/api/ops/tickets/{quote(ids['ticket_id'], safe='')}", m.TicketDetail),
        "/api/ops/apps": ("/api/ops/apps", m.AppsOut),
        "/api/ops/apps/{app_id}": (f"/api/ops/apps/{ids['app_id']}", m.App360Out),
        "/api/ops/costs": ("/api/ops/costs", m.CostsOut),
        "/api/ops/contracts/renewals": ("/api/ops/contracts/renewals", m.RenewalsOut),
        "/api/ops/licenses/utilization": ("/api/ops/licenses/utilization", m.LicensesOut),
        "/api/ops/vendors/sla-trend": ("/api/ops/vendors/sla-trend", m.VendorTrendsOut),
    }


def get_ok(client: Any, url: str, model: type[BaseModel] | None = None, **params: Any) -> dict[str, Any]:
    r = client.get(url, params=params or None)
    assert r.status_code == 200, (url, params, r.text[:500])
    body = r.json()
    if model is not None:
        model.model_validate(body)
    return body


def test_every_ops_get_returns_200_and_validates(client, ids):
    table = routes(ids)
    schema = client.app.openapi()
    ops_gets = {p for p, item in schema["paths"].items() if p.startswith("/api/ops/") and "get" in item}
    assert ops_gets == set(table), "every ops GET in the contract is exercised"
    for url, model in table.values():
        get_ok(client, url, model)
        include_drafts = get_ok(client, url, model, include_drafts="true")
        assert include_drafts is not None


def test_overview_defaults_to_last_full_week(client, ops_profile, ro_conn):
    body = get_ok(client, "/api/ops/overview", m.OpsOverview)
    assert body["period"] == ops_profile.periods["week"] == "2026-W35"
    assert body["as_of"] == AS_OF.isoformat() and body["data_as_of_last_import"] == AS_OF.isoformat()
    assert [k["key"] for k in body["kpis"]] == OVERVIEW_KPIS
    assert all(k["definition"] for k in body["kpis"])
    assert len(body["top_risks"]) <= 5 and body["top_risks"], "the fixture has published rule findings"
    assert all(f["system_detected"] for f in body["top_risks"] if f["origin"] == "rule")
    assert body["freshness"] and body["stale_open"] >= 0

    # Ticket KPIs equal the weekly report's inputs for the same week (sed.metrics on the same connection).
    from sed import metrics

    week = parse_period("2026-W35", TZ)
    kpis = {k["key"]: k for k in body["kpis"]}
    f = metrics.Filters()
    assert kpis["inc.backlog"]["value"] == metrics.backlog(ro_conn, f, week.end_utc)["total"]
    assert kpis["inc.backlog"]["compare"] == metrics.backlog(ro_conn, f, week.start_utc)["total"]
    assert kpis["inc.sla.pct"]["value"] == metrics.sla(ro_conn, f, week)["pct"]
    assert kpis["inc.mttr.median_h"]["value"] == metrics.mttr(ro_conn, f, week)["median_h"]
    p1p2 = ro_conn.execute(
        "SELECT COUNT(*) FROM ticket WHERE kind = 'incident' AND priority <= 2 AND opened_at >= ? AND opened_at < ?",
        (week.start_iso, week.end_iso),
    ).fetchone()[0]
    assert kpis["inc.p1p2.opened"]["value"] == p1p2
    renewals = metrics.renewals(ro_conn, AS_OF, 90)
    assert kpis["renewals.90d.count"]["value"] == sum(
        1 for r in renewals if r["days_to_end"] is not None and 0 <= r["days_to_end"] <= 90
    )
    assert kpis["notice.30d.count"]["value"] == sum(
        1 for r in renewals if r["days_to_notice"] is not None and 0 <= r["days_to_notice"] <= 30
    )


def test_overview_period_filter(client):
    body = get_ok(client, "/api/ops/overview", m.OpsOverview, period="2026-W30")
    assert body["period"] == "2026-W30"
    monthly = get_ok(client, "/api/ops/overview", m.OpsOverview, period="2026-08")
    assert monthly["period"] == "2026-08"
    bad = client.get("/api/ops/overview", params={"period": "2026-13"})
    assert bad.status_code == 422 and bad.json()["error"]["kind"] == "validation"


def test_attention_rows_link_to_ticket_detail(client):
    body = get_ok(client, "/api/ops/attention", m.AttentionOut, limit=5)
    assert body["count"] >= len(body["items"]) and len(body["items"]) <= 5
    assert body["items"], "the fixture has open incidents needing attention"
    assert sum(body["by_reason"].values()) >= body["count"]
    first = body["items"][0]
    assert first["reasons"]
    detail = get_ok(client, f"/api/ops/tickets/{quote(first['ticket_id'], safe='')}", m.TicketDetail)
    assert detail["number"] == first["number"] and detail["is_open"] is True


def test_ticket_detail_with_url_encoded_colon(client, ids):
    ticket_id = ids["ticket_id"]
    assert ":" in ticket_id
    encoded = get_ok(client, f"/api/ops/tickets/{quote(ticket_id, safe='')}", m.TicketDetail)
    plain = get_ok(client, f"/api/ops/tickets/{ticket_id}", m.TicketDetail)
    assert encoded["ticket_id"] == plain["ticket_id"] == ticket_id
    assert isinstance(encoded["labels"], list)


@pytest.mark.parametrize(
    "url",
    ["/api/ops/tickets/incident%3AINC0000000", "/api/ops/tickets/nope", "/api/ops/apps/APM0000000"],
)
def test_unknown_ids_give_404_envelope(client, url):
    r = client.get(url)
    assert r.status_code == 404
    body = r.json()
    assert body["ok"] is False and body["error"]["kind"] == "not_found" and body["error"]["message"]


def test_app_filter_narrows(client, ids, ro_conn):
    app_id = ids["app_id"]
    everything = get_ok(client, "/api/ops/tickets", m.TicketPage)
    narrowed = get_ok(client, "/api/ops/tickets", m.TicketPage, app=app_id, page_size=200)
    assert 0 < narrowed["total"] < everything["total"]
    assert {t["app_id"] for t in narrowed["items"]} == {app_id}
    expected = ro_conn.execute("SELECT COUNT(*) FROM ticket WHERE app_id = ?", (app_id,)).fetchone()[0]
    assert narrowed["total"] == expected

    grid = get_ok(client, "/api/ops/apps", m.AppsOut, app=app_id)
    assert [a["app_id"] for a in grid["items"]] == [app_id]

    all_volumes = get_ok(client, "/api/ops/tickets/volumes", m.VolumesOut)["items"]
    app_volumes = get_ok(client, "/api/ops/tickets/volumes", m.VolumesOut, app=app_id)["items"]
    assert sum(v["opened"] for v in app_volumes) < sum(v["opened"] for v in all_volumes)

    second = client.get("/api/ops/filters").json()["apps"][1]["app_id"]
    two = get_ok(client, "/api/ops/tickets", m.TicketPage, app=[app_id, second])
    single_second = get_ok(client, "/api/ops/tickets", m.TicketPage, app=second)
    assert two["total"] == narrowed["total"] + single_second["total"]


def test_family_filter_narrows(client):
    filters = get_ok(client, "/api/ops/filters", m.OpsFiltersOut)
    family = "Finance"
    assert family in filters["families"]
    members = {a["app_id"] for a in filters["apps"] if a["family"] == family}
    grid = get_ok(client, "/api/ops/apps", m.AppsOut, family=family)
    assert {a["app_id"] for a in grid["items"]} == members
    tickets = get_ok(client, "/api/ops/tickets", m.TicketPage, family=family, page_size=200)
    assert tickets["total"] > 0 and {t["app_id"] for t in tickets["items"]} <= members
    costs_all = get_ok(client, "/api/ops/costs", m.CostsOut)
    costs_family = get_ok(client, "/api/ops/costs", m.CostsOut, family=family)
    assert costs_family["total_actual"] < costs_all["total_actual"]
    assert {r["key"] for r in costs_family["rows"]} <= members


def test_vendor_filter_narrows(client, ops_profile, ro_conn):
    vendor = ops_profile.ids["vendor_p2"]
    name = ro_conn.execute("SELECT name FROM vendor WHERE vendor_id = ?", (vendor,)).fetchone()[0]
    trend = get_ok(client, "/api/ops/vendors/sla-trend", m.VendorTrendsOut, vendor=vendor)
    assert [v["vendor_id"] for v in trend["items"]] == [vendor]
    assert trend["items"][0]["vendor"] == name
    assert trend["items"][0]["delta_pp"] is not None and trend["items"][0]["delta_pp"] <= -5, "P2 SLA decline"

    tickets = get_ok(client, "/api/ops/tickets", m.TicketPage, vendor=vendor, page_size=200)
    assert tickets["total"] > 0 and {t["vendor_name"] for t in tickets["items"]} == {name}

    renewals_all = get_ok(client, "/api/ops/contracts/renewals", m.RenewalsOut, days=1095)["items"]
    renewals_vendor = get_ok(client, "/api/ops/contracts/renewals", m.RenewalsOut, days=1095, vendor=vendor)["items"]
    assert len(renewals_vendor) < len(renewals_all)
    assert {r["contract_id"] for r in renewals_vendor} <= {r["contract_id"] for r in renewals_all}
    contract_vendors = dict(ro_conn.execute("SELECT contract_id, vendor_id FROM contract").fetchall())
    assert all(contract_vendors[r["contract_id"]] == vendor for r in renewals_vendor)

    licenses_all = get_ok(client, "/api/ops/licenses/utilization", m.LicensesOut)
    licenses_vendor = get_ok(client, "/api/ops/licenses/utilization", m.LicensesOut, vendor=vendor)
    assert len(licenses_vendor["items"]) < len(licenses_all["items"])


def test_group_filter_narrows_ticket_views(client, ro_conn):
    group = ro_conn.execute(
        "SELECT assignment_group FROM ticket WHERE kind = 'incident' AND assignment_group IS NOT NULL "
        "GROUP BY assignment_group ORDER BY COUNT(*) DESC LIMIT 1"
    ).fetchone()[0]
    backlog_all = get_ok(client, "/api/ops/tickets/backlog", m.BacklogOut)
    backlog_group = get_ok(client, "/api/ops/tickets/backlog", m.BacklogOut, group=group)
    assert backlog_group["total"] <= backlog_all["total"]
    assert {g["group"] for g in backlog_group["by_group"]} <= {group}
    assert {f["group"] for f in backlog_group["flow"]} == {group}
    sla = get_ok(client, "/api/ops/tickets/sla", m.SlaOut, group=group)
    sla_all = get_ok(client, "/api/ops/tickets/sla", m.SlaOut)
    assert sum(i["total"] for i in sla["items"]) < sum(i["total"] for i in sla_all["items"])


def test_period_filter_narrows(client):
    august = parse_period("2026-08", TZ)
    tickets = get_ok(client, "/api/ops/tickets", m.TicketPage, period="2026-08", page_size=200, sort="opened_asc")
    everything = get_ok(client, "/api/ops/tickets", m.TicketPage)
    assert 0 < tickets["total"] < everything["total"]
    assert all(august.start_iso <= t["opened_at"] < august.end_iso for t in tickets["items"])

    costs = get_ok(client, "/api/ops/costs", m.CostsOut, period="2026-Q3")
    assert costs["months"] == ["2026-07", "2026-08"], "complete months of Q3 on or before as-of"
    default_costs = get_ok(client, "/api/ops/costs", m.CostsOut)
    assert default_costs["months"] == ["2026-06", "2026-07", "2026-08"]

    weeks = get_ok(client, "/api/ops/tickets/volumes", m.VolumesOut, period="2026-W30", n=4)
    assert [v["period"] for v in weeks["items"]] == ["2026-W27", "2026-W28", "2026-W29", "2026-W30"]
    months = get_ok(client, "/api/ops/tickets/mttr", m.MttrOut, granularity="month", n=3, period="2026-Q2")
    assert [v["period"] for v in months["items"]] == ["2026-04", "2026-05", "2026-06"]

    backlog = get_ok(client, "/api/ops/tickets/backlog", m.BacklogOut, period="2026-W30")
    assert backlog["at"] == parse_period("2026-W30", TZ).end_iso


def test_trend_defaults_end_at_last_full_period(client):
    volumes = get_ok(client, "/api/ops/tickets/volumes", m.VolumesOut)
    assert len(volumes["items"]) == 12 and volumes["items"][-1]["period"] == "2026-W35"
    assert all(v["net"] == v["opened"] - v["resolved"] for v in volumes["items"])
    monthly = get_ok(client, "/api/ops/tickets/sla", m.SlaOut, granularity="month", n=6)
    assert [i["period"] for i in monthly["items"]][-1] == "2026-08" and monthly["granularity"] == "month"
    assert monthly["sla_source"] in {"task_sla", "made_sla", "targets"}
    assert sum(p["total"] for p in monthly["by_priority"]) == sum(i["total"] for i in monthly["items"])
    requests = get_ok(client, "/api/ops/tickets/volumes", m.VolumesOut, kind="sc_req_item")
    assert sum(v["opened"] for v in requests["items"]) > 0
    bad = client.get("/api/ops/tickets/volumes", params={"granularity": "day"})
    assert bad.status_code == 422


def test_apps_grid_and_app_360(client, ops_profile, ro_conn):
    grid = get_ok(client, "/api/ops/apps", m.AppsOut)["items"]
    live = ro_conn.execute("SELECT COUNT(*) FROM application WHERE is_deleted = 0").fetchone()[0]
    assert len(grid) == live
    risky = [a for a in grid if a["open_risks"]]
    assert risky, "rule findings relate to applications"

    import json

    truth = json.loads((ops_profile.ground_truth / "patterns.json").read_text(encoding="utf-8"))
    quiet_app = ro_conn.execute("SELECT app_id FROM application WHERE name = ?", (truth["P11"]["app"],)).fetchone()[0]
    view = get_ok(client, f"/api/ops/apps/{quiet_app}", m.App360Out)
    assert view["app"]["app_id"] == quiet_app
    assert any(f["kind"] == "rationalization" and f["subject_id"] == quiet_app for f in view["findings"])
    row = next(a for a in grid if a["app_id"] == quiet_app)
    assert row["open_risks"] == len(view["findings"])
    assert len(view["volumes"]) == 12 and view["volumes"][-1]["period"] == "2026-08"
    assert len(view["cost"]) == 12 and len(view["open_tickets"]) <= 20
    kpis = {k["key"] for k in view["kpis"]}
    assert {"inc.backlog", "inc.sla.pct.3m", "cost.actual.ytd", "license.utilization"} <= kpis
    contracts = ro_conn.execute(
        "SELECT COUNT(*) FROM contract WHERE app_id = ? AND is_deleted = 0", (quiet_app,)
    ).fetchone()[0]
    assert len(view["contracts"]) == contracts

    busiest = ro_conn.execute(
        "SELECT app_id FROM ticket WHERE is_open = 1 AND stale_open = 0 AND kind != 'change_request' "
        "AND app_id IS NOT NULL GROUP BY app_id ORDER BY COUNT(*) DESC LIMIT 1"
    ).fetchone()[0]
    busy = get_ok(client, f"/api/ops/apps/{busiest}", m.App360Out)
    assert busy["open_tickets"] and all(t["is_open"] and t["app_id"] == busiest for t in busy["open_tickets"])


def test_costs_renewals_and_licenses(client, ops_profile):
    import json

    truth = json.loads((ops_profile.ground_truth / "patterns.json").read_text(encoding="utf-8"))
    for group_by in ("app", "vendor", "category", "app_category"):
        body = get_ok(client, "/api/ops/costs", m.CostsOut, group_by=group_by)
        assert body["group_by"] == group_by and body["rows"]
        actuals = [r["actual"] for r in body["rows"]]
        assert actuals == sorted(actuals, reverse=True)
        assert body["total_actual"] == pytest.approx(sum(actuals), abs=0.05 * len(actuals))

    renewals = get_ok(client, "/api/ops/contracts/renewals", m.RenewalsOut)
    assert renewals["days"] == 180 and renewals["as_of"] == AS_OF.isoformat()
    listed = {r["contract_id"] for r in renewals["items"]}
    assert {c["contract"] for c in truth["P4"]} <= listed
    assert truth["controls"]["non_renewing_contract"] not in listed

    licenses = get_ok(client, "/api/ops/licenses/utilization", m.LicensesOut)
    assert licenses["idle_cost_total"] == pytest.approx(sum(r["idle_cost_base"] or 0 for r in licenses["items"]))
    under = {r["license_id"] for r in licenses["items"] if r["utilization"] is not None and r["utilization"] < 0.7}
    assert {entry["license"] for entry in truth["P3"] if entry.get("utilization", 0) < 0.7} <= under


def test_gets_never_write(client, ids, monkeypatch):
    """No GET may reach a snapshot, a rule refresh or findings_as_of (they write); the session teardown digest
    additionally proves the database did not change."""
    from sed import analytics
    from sed.reports import snapshot

    def forbidden(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("a GET reached a writing function")

    monkeypatch.setattr(analytics, "refresh_rule_findings", forbidden)
    monkeypatch.setattr(analytics, "findings_as_of", forbidden)
    monkeypatch.setattr(snapshot, "create_snapshot", forbidden)
    for url, model in routes(ids).values():
        get_ok(client, url, model)


def test_unsafe_methods_on_ops_routes_need_the_token(ops_profile):
    from tests.fixtures.api import api_client

    anonymous = api_client(ops_profile.paths, send_token=False)
    assert anonymous.post("/api/ops/overview").status_code == 403
    with_token = api_client(ops_profile.paths, token=TEST_TOKEN)
    assert with_token.post("/api/ops/overview").status_code == 405
