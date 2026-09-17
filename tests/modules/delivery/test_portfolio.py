"""Delivery module on the synthetic delivery profile: imports, planted patterns in the read models and rule findings
(double slip, overdue high risk, scope growth, late forecast), the healthy control, the API and the CLI."""

from __future__ import annotations

import json
from datetime import date

from sed import db
from sed.api.models import FindingOut
from sed.modules.delivery.api_models import DeliveryPortfolioOut, DeliveryProjectOut
from sed.modules.delivery.queries import portfolio as P
from tests.fixtures.api import api_client

AS_OF = date(2026, 9, 1)


def _truth(profile) -> dict:
    return json.loads((profile.ground_truth / "patterns.json").read_text(encoding="utf-8"))


def test_imports_and_read_models_show_the_planted_patterns(delivery_profile):
    paths = delivery_profile.paths
    truth = _truth(delivery_profile)
    conn = db.connect(paths.db, readonly=True)
    try:
        counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                  for t in ("delivery_project", "delivery_milestone", "delivery_raid")}  # fmt: skip
        items = {i["project"]["project_id"]: i for i in P.portfolio(conn, paths, AS_OF)}
        early = {i["project"]["project_id"]: i for i in P.portfolio(conn, paths, date(2026, 8, 20))}
    finally:
        conn.close()
    assert counts == {"delivery_project": 4, "delivery_milestone": 4 * 8 * 3, "delivery_raid": 9}

    dp1 = items[truth["DP1"]["project_id"]]
    uat = next(t for t in dp1["plan"]["tasks"] if t["task_id"] == truth["DP1"]["task_id"])
    assert (uat["slip_days"], uat["replans"]) == (truth["DP1"]["slip_days"], truth["DP1"]["replans"])
    assert dp1["plan"]["versions"] == 3 and dp1["project"]["jira_keys"] == ["EINV"]
    assert early[truth["DP1"]["project_id"]]["plan"]["versions"] == 1  # later plan versions are ignored

    dp2 = items[truth["DP2"]["project_id"]]
    risk = next(r for r in dp2["raid"] if r["raid_id"] == truth["DP2"]["raid_id"])
    assert risk["open"] and risk["days_overdue"] == truth["DP2"]["days_overdue"]
    closed = next(r for r in dp2["raid"] if r["raid_id"] == truth["control"]["closed_on_time"])
    assert not closed["open"] and closed["days_overdue"] == 0

    dp3 = items[truth["DP3"]["project_id"]]["progress"]
    assert dp3.scope_growth_pct is not None and dp3.scope_growth_pct >= 30
    target = date.fromisoformat(items[truth["DP4"]["project_id"]]["project"]["target_date"])
    assert dp3.forecast_finish is None or dp3.forecast_finish > target

    control = items[truth["control"]["project_id"]]
    assert control["health"] == {"rag": "green", "reasons": []} or control["health"]["rag"] == "amber"
    assert control["progress"].forecast_finish is not None
    assert control["progress"].forecast_finish <= date.fromisoformat(control["project"]["target_date"])
    assert all((t["slip_days"] or 0) == 0 for t in control["plan"]["tasks"])
    assert items["PRJ-101"]["health"]["rag"] == "red" and items["PRJ-102"]["health"]["rag"] == "red"
    assert items["PRJ-101"]["documents"]["requirements"] == 5 and items["PRJ-101"]["documents"]["adrs"] == 3


def test_rule_findings_fire_for_patterns_and_stay_quiet_for_the_control(delivery_profile):
    conn = db.connect(delivery_profile.paths.db, readonly=True)
    try:
        rows = conn.execute(
            "SELECT stable_key, subject_id, severity FROM finding WHERE kind = 'delivery_risk' AND status = 'active'"
        ).fetchall()
    finally:
        conn.close()
    keys = {r["stable_key"] for r in rows}
    assert "delivery_risk:slip:PRJ-101:M4" in keys and "delivery_risk:slip:PRJ-101:M5" in keys
    assert "delivery_risk:raid_overdue:R-103-02" in keys
    assert "delivery_risk:scope_growth:PRJ-102" in keys and "delivery_risk:forecast_late:PRJ-102" in keys
    assert not any(r["subject_id"] == "PRJ-104" for r in rows)
    assert not any(k.startswith("delivery_risk:slip:PRJ-103") for k in keys)  # 5-day slip stays below the threshold


def test_api_and_cli(delivery_profile):
    client = api_client(delivery_profile.paths)
    body = DeliveryPortfolioOut.model_validate(client.get("/api/delivery/portfolio").json())
    assert body.as_of == "2026-09-01" and body.counts["projects"] == 4 and body.counts["red"] >= 2
    rows = {r.project_id: r for r in body.projects}
    assert rows["PRJ-101"].worst_slip_days == 35 and rows["PRJ-101"].reported_rag == "amber"
    assert rows["PRJ-103"].overdue_raid == 1 and rows["PRJ-104"].computed_rag in ("green", "amber")
    assert all(isinstance(FindingOut.model_validate(f.model_dump()), FindingOut) for f in body.findings)

    detail = DeliveryProjectOut.model_validate(client.get("/api/delivery/projects/PRJ-102").json())
    assert detail.plan_versions == 3 and len(detail.progress.weekly) == 12
    assert detail.documents.requirements == 5 and {f.subject_id for f in detail.findings} == {"PRJ-102"}
    assert client.get("/api/delivery/projects/PRJ-999").status_code == 412
