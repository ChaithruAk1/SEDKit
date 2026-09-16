"""Hand-computed fixture: every expected value below was worked out by hand (see comments)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from sed import analytics, bootstrap, db, metrics
from sed.calendar import parse_period
from sed.paths import get_paths
from sed.settings import Thresholds

TZ = "Europe/Paris"
W35 = parse_period("2026-W35", TZ)  # Mon 2026-08-24 00:00 CEST (08-23T22:00Z) .. Mon 08-31 00:00 CEST (08-30T22:00Z)


@pytest.fixture
def conn(data_root: Path):
    paths = get_paths("synthetic")
    bootstrap.init_profile(paths, new_salt=True, write_claude_settings=False)
    c = db.connect(paths.db)
    with db.write_tx(c):
        c.execute("INSERT INTO vendor (vendor_id, name) VALUES ('V1', 'Vendor One')")
        c.executemany(
            "INSERT INTO application (app_id, name, app_family) VALUES (?, ?, ?)",
            [("A1", "App One", "Finance"), ("A2", "App Two", "HR")],
        )

        def ticket(num, opened, resolved=None, priority=3, made_sla=None, stale=0, app="A1", vendor=None, **kw):
            c.execute(
                "INSERT INTO ticket (ticket_id, kind, number, app_id, vendor_id, priority, opened_at, resolved_at, "
                "sys_updated_on, is_open, made_sla, stale_open, assigned_to_pid, reassignment_count, reopen_count) "
                "VALUES (?, 'incident', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'P-x', ?, ?)",
                (
                    f"incident:{num}",
                    num,
                    app,
                    vendor,
                    priority,
                    opened,
                    resolved,
                    resolved or opened,
                    0 if resolved else 1,
                    made_sla,
                    stale,
                    kw.get("reassign", 0),
                    kw.get("reopen", 0),
                ),
            )

        ticket("A", "2026-08-24T08:00:00Z", "2026-08-24T10:00:00Z", 2, 1)  # 2 h, in W35
        ticket("B", "2026-08-20T08:00:00Z", "2026-08-25T08:00:00Z", 3, 0)  # 120 h, opened W34 resolved W35
        ticket("C", "2026-08-25T08:00:00Z", "2026-08-26T08:00:00Z", 4, 1)  # 24 h
        ticket("D", "2026-08-10T08:00:00Z", None, 3)  # open, 20.58 days at W35 end, past 40 h target
        ticket("E", "2026-08-30T21:30:00Z", None, 1)  # Sun 23:30 local -> W35; P1 open
        ticket("F", "2026-08-30T22:30:00Z", None, 3)  # Mon 00:30 local -> W36 (not in W35)
        ticket("G", "2026-07-01T08:00:00Z", None, 3, stale=1)  # stale: excluded from backlog
        ticket(
            "H", "2026-08-23T21:30:00Z", "2026-08-23T23:00:00Z", 4, 1
        )  # opened Sun 23:30 (W34), resolved Mon 01:00 (W35)
        c.executemany(
            "INSERT INTO task_sla (ticket_id, sla_name, sla_type, has_breached, start_time) VALUES (?, 'res', "
            "'resolution', ?, '')",
            [("incident:A", 0), ("incident:B", 1), ("incident:C", 1), ("incident:H", 0)],
        )
        contracts = [
            (
                "C1",
                "2026-10-15",
                30,
                "2026-09-15",
                1,
                "active",
                0,
            ),  # notice in 15 d (auto-renew) -> critical; end in 45 d
            ("C2", "2026-12-31", 90, "2026-10-02", 0, "active", 0),  # notice in 32 d -> high; end in 122 d -> medium
            ("C3", "2027-06-30", 60, "2027-05-01", 0, "active", 0),  # nothing
            ("C4", "2026-11-30", 60, "2026-10-01", 0, "non_renewing", 0),  # excluded
            ("C5", "2026-09-30", 30, "2026-08-31", 0, "active", 1),  # deleted -> excluded
            ("C6", "2026-07-31", 30, "2026-07-01", 0, "expired", 0),  # past
        ]
        c.executemany(
            "INSERT INTO contract (contract_id, contract_number, vendor_id, product, end_date, notice_period_days, "
            "notice_deadline, auto_renew, renewal_status, is_deleted, annual_value_base) VALUES (?, ?, 'V1', "
            "'Suite', ?, ?, ?, ?, ?, ?, 1000)",
            [(cid, cid, *rest) for cid, *rest in contracts],
        )
        c.executemany(
            "INSERT INTO license (license_id, app_id, product, entitled_qty, unit_cost_base) VALUES (?, ?, ?, ?, ?)",
            [
                ("L1", "A1", "Suite", 100, 50),
                ("L2", "A1", "Pro", 10, 1000),
                ("L3", "A1", "Std", 50, 10),
                ("L4", "A2", "HR", 60, 1000),
            ],
        )
        c.executemany(
            "INSERT INTO license_usage (license_id, as_of_date, assigned_qty, active_qty_90d) VALUES (?, ?, ?, ?)",
            [
                ("L1", "2026-07-31", 95, 90),
                ("L1", "2026-08-31", 90, 40),
                ("L2", "2026-08-31", 12, 9),
                ("L3", "2026-08-31", 45, 45),
                ("L4", "2026-08-31", 55, 50),
            ],
        )
        for period, cat, actual, budget in (
            ("2026-07", "Hosting", 16000, 10000),
            ("2026-08", "Hosting", 16000, 10000),
            ("2026-07", "Licenses", 10500, 10000),
            ("2026-08", "Licenses", 10500, 10000),
        ):
            for line_type, amount, as_of in (("actual", actual, None), ("budget", budget, "2026-01-01")):
                c.execute(
                    "INSERT INTO cost_line (cost_line_id, app_id, period, line_type, cost_category, amount, "
                    "currency, amount_base, as_of) "
                    "VALUES (?, 'A1', ?, ?, ?, ?, 'EUR', ?, ?)",
                    (f"{period}-{cat}-{line_type}", period, line_type, cat, amount, amount, as_of),
                )
        # Vendor V1: 20 incidents resolved mid-month Feb..Jul (the 6 complete months before as-of 2026-08-31);
        # met 19,19,19,15,15,15 -> 95,95,95,75,75,75 -> delta -20 pp
        for m_idx, month in enumerate(["02", "03", "04", "05", "06", "07"]):
            met = 19 if m_idx < 3 else 15
            for i in range(20):
                ticket(
                    f"V{month}{i:02d}",
                    f"2026-{month}-10T08:00:00Z",
                    f"2026-{month}-11T08:00:00Z",
                    3,
                    1 if i < met else 0,
                    app="A1",
                    vendor="V1",
                )
    yield c
    c.close()


def test_volume_bucketing_in_reporting_timezone(conn):
    f = metrics.Filters(priorities=[1, 2, 3, 4])
    rows = {r["period"]: r for r in metrics.volume_trend(conn, f, [W35.previous(), W35])}
    # Opened in W35: A, C, E (H opened Sun 23:30 local = W34; F opened Mon 00:30 local = W36).
    assert rows["2026-W35"]["opened"] == 3
    # Resolved in W35: A, B, C, H.
    assert rows["2026-W35"]["resolved"] == 4


def test_sla_sources(conn):
    core_only = metrics.Filters(app_ids=["A1"])
    s_task = metrics.sla(conn, core_only, W35, source="task_sla")
    assert (s_task["met"], s_task["total"], s_task["pct"]) == (2, 4, 50.0)  # task_sla: B and C breached
    s_made = metrics.sla(conn, core_only, W35, source="made_sla")
    assert (s_made["met"], s_made["total"], s_made["pct"]) == (3, 4, 75.0)  # made_sla: only B missed
    s_target = metrics.sla(conn, core_only, W35, source="targets")
    # targets: A 2 h <= 8 (P2), C 24 h and H 1.5 h <= 120 (P4) met; B 120 h > 40 (P3) missed
    assert (s_target["met"], s_target["total"], s_target["pct"]) == (3, 4, 75.0)
    assert metrics.sla_source(conn) == "task_sla"
    assert s_task["by_priority"][4] == {"total": 2, "met": 1, "pct": 50.0}


@pytest.mark.parametrize("drives", [False, True])
def test_scope_predicate_matches_the_equivalent_filter(conn, drives):
    by_app = metrics.Filters(app_ids=["A1"])
    ids = [r[0] for r in conn.execute("SELECT ticket_id FROM ticket WHERE app_id = 'A1'")]
    scoped = metrics.Filters(
        scope_sql="{t}.ticket_id IN (SELECT value FROM json_each(?))",
        scope_params=(json.dumps(ids),),
        scope_drives=drives,
    )
    assert scoped.where()[0].startswith("+t.kind = ?" if drives else "t.kind = ?")
    for source in ("task_sla", "made_sla", "targets"):
        assert metrics.sla(conn, scoped, W35, source) == metrics.sla(conn, by_app, W35, source)
    assert metrics.backlog(conn, scoped, W35.end_utc) == metrics.backlog(conn, by_app, W35.end_utc)
    assert metrics.mttr(conn, scoped, W35) == metrics.mttr(conn, by_app, W35)
    assert metrics.volume_trend(conn, scoped, [W35]) == metrics.volume_trend(conn, by_app, [W35])
    plan = [
        r[3]
        for r in conn.execute("EXPLAIN QUERY PLAN SELECT 1 FROM ticket t WHERE " + scoped.where()[0], scoped.where()[1])
    ]
    assert any("(ticket_id=?)" in step for step in plan) == drives, plan


def test_batched_period_metrics_match_single_periods(conn):
    f = metrics.Filters(app_ids=["A1"])
    periods = [W35.previous(2), W35.previous(), W35, parse_period("2026-W36", TZ)]
    for source in ("task_sla", "made_sla", "targets"):
        assert metrics.sla_periods(conn, f, periods, source) == [metrics.sla(conn, f, p, source) for p in periods]
    assert metrics.mttr_periods(conn, f, periods) == [metrics.mttr(conn, f, p) for p in periods]
    assert metrics.sla_periods(conn, f, [], "task_sla") == [] and metrics.mttr_periods(conn, f, []) == []
    # Overlapping periods count a ticket in each of them.
    month = parse_period("2026-08", TZ)
    assert metrics.sla_periods(conn, f, [month, W35], "made_sla")[1] == metrics.sla(conn, f, W35, "made_sla")


def test_group_flow_counts_arrivals_and_closures_per_period(conn):
    with db.write_tx(conn):
        conn.execute("UPDATE ticket SET assignment_group = 'G-B' WHERE number IN ('A', 'C')")
        conn.execute("UPDATE ticket SET assignment_group = 'G-A' WHERE number = 'B'")
    f = metrics.Filters(app_ids=["A1"])
    rows = metrics.group_flow(conn, f, [W35.previous(), W35])
    # W34: B arrives (G-A), H arrives Sun 23:30 local (no group). W35: A and C arrive and resolve (G-B); B resolves
    # (G-A); E arrives and H resolves (no group).
    assert rows == [
        {"period": "2026-W34", "group": None, "arrived": 1, "closed": 0},
        {"period": "2026-W34", "group": "G-A", "arrived": 1, "closed": 0},
        {"period": "2026-W35", "group": None, "arrived": 1, "closed": 1},
        {"period": "2026-W35", "group": "G-A", "arrived": 0, "closed": 1},
        {"period": "2026-W35", "group": "G-B", "arrived": 2, "closed": 2},
    ]


def test_sla_by_group_merges_back_to_the_total(conn):
    with db.write_tx(conn):
        conn.execute("UPDATE ticket SET assignment_group = 'G-B' WHERE number IN ('A', 'C')")
    f = metrics.Filters(app_ids=["A1"])
    by_group = metrics.sla_by_group(conn, f, W35, "task_sla")
    assert set(by_group) == {None, "G-B"}
    assert (by_group["G-B"]["met"], by_group["G-B"]["total"]) == (1, 2)  # A met, C breached
    assert metrics.merge_sla(list(by_group.values()), "task_sla") == metrics.sla(conn, f, W35, "task_sla")


def test_mttr(conn):
    m = metrics.mttr(conn, metrics.Filters(app_ids=["A1"]), W35)
    # Hours: H 1.5, A 2, C 24, B 120 -> median 13.0
    assert m["count"] == 4 and m["median_h"] == pytest.approx(13.0) and m["mean_h"] == pytest.approx(36.875, abs=0.006)


def test_backlog_and_aging(conn):
    b = metrics.backlog(conn, metrics.Filters(app_ids=["A1"]), W35.end_utc)
    assert b["total"] == 2  # D and E; F opened after the end; G stale
    assert b["aging"] == {"0-7d": 1, "8-30d": 1, "31-90d": 0, ">90d": 0}
    assert metrics.backlog(conn, metrics.Filters(app_ids=["A1"]), W35.end_utc, exclude_stale=False)["total"] == 3


def test_attention(conn):
    att = metrics.attention(conn, metrics.Filters(app_ids=["A1"]), W35.end_utc, Thresholds())
    by_number = {i["number"]: i["reasons"] for i in att["items"]}
    assert set(by_number) == {"D", "E"}
    assert by_number["E"] == "P1 open"
    assert by_number["D"] == "past SLA target"
    assert att["items"][0]["number"] == "E"  # P1 first


def test_renewals_and_notice(conn):
    rows = {r["contract_id"]: r for r in metrics.renewals(conn, date(2026, 8, 31), 90)}
    assert set(rows) == {"C1", "C2"}
    assert (rows["C1"]["days_to_end"], rows["C1"]["days_to_notice"]) == (45, 15)
    assert (rows["C2"]["days_to_end"], rows["C2"]["days_to_notice"]) == (122, 32)


def test_license_utilization_and_idle_cost(conn):
    rows = {r["license_id"]: r for r in metrics.license_utilization(conn, date(2026, 8, 31))}
    assert rows["L1"]["utilization"] == 0.4 and rows["L1"]["idle_cost_base"] == 3000.0  # (100-40) x 50; latest snapshot
    assert rows["L2"]["assigned_ratio"] == 1.2 and rows["L2"]["utilization"] == 0.9
    assert rows["L3"]["idle_cost_base"] == 50.0


def test_cost_vs_budget(conn):
    rows = {r["key"]: r for r in metrics.cost_vs_budget(conn, ["2026-07", "2026-08"], "app_category")}
    assert rows["App One / Hosting"]["variance_pct"] == 60.0
    assert rows["App One / Licenses"]["variance_pct"] == 5.0


def test_vendor_sla_trend(conn):
    trend = metrics.vendor_sla_trend(conn, date(2026, 8, 31), TZ, months=6)
    v1 = next(v for v in trend if v["vendor_id"] == "V1")
    assert [s["sla_pct"] for s in v1["series"]] == [95.0, 95.0, 95.0, 75.0, 75.0, 75.0]
    assert v1["delta_pp"] == -20.0


def test_rule_findings_exact(conn):
    paths = get_paths("synthetic")
    found = {f["stable_key"]: f for f in analytics.compute_rule_findings(conn, paths, date(2026, 8, 31))}
    assert found["renewal_risk:notice:C1"]["severity"] == "critical"
    assert found["renewal_risk:notice:C2"]["severity"] == "high"
    assert found["renewal_risk:end:C1"]["severity"] == "high"
    assert found["renewal_risk:end:C2"]["severity"] == "medium"
    assert found["license_risk:utilization:L1"]["severity"] == "medium"  # idle 3,000 < 50,000
    assert found["license_risk:utilization:L2"]["severity"] == "high"  # over-assigned
    assert found["vendor_risk:sla_decline:V1"]["severity"] == "high"  # -20 <= 2 x -5
    assert found["cost_risk:variance:App One / Hosting"]["severity"] == "high"
    assert "rationalization:quiet_app:A2" in found  # no tickets, 60,000/yr license cost
    assert not any("C3" in k or "C4" in k or "C5" in k or "C6" in k or "L3" in k for k in found)
    assert "rationalization:quiet_app:A1" not in found


def test_rule_refresh_lifecycle(conn):
    paths = get_paths("synthetic")
    stats = analytics.refresh_rule_findings(conn, paths, date(2026, 8, 31))
    assert stats["inserted"] >= 9
    again = analytics.refresh_rule_findings(conn, paths, date(2026, 8, 31))
    assert again["inserted"] == 0 and again["updated"] == stats["inserted"]
    key = "license_risk:utilization:L1"
    with db.write_tx(conn):
        conn.execute("UPDATE finding SET status = 'acknowledged' WHERE stable_key = ? AND status = 'active'", (key,))
    hidden = analytics.refresh_rule_findings(conn, paths, date(2026, 8, 31))
    assert hidden["hidden"] == 1
    assert key not in {f["stable_key"] for f in analytics.published_rule_findings(conn, date(2026, 8, 31))}
    # Evidence changes materially (utilization drops sharply) -> re-activated.
    with db.write_tx(conn):
        conn.execute(
            "UPDATE license_usage SET active_qty_90d = 5 WHERE license_id = 'L1' AND as_of_date = '2026-08-31'"
        )
    back = analytics.refresh_rule_findings(conn, paths, date(2026, 8, 31))
    assert back["reactivated"] == 1
    # Condition disappears -> superseded.
    with db.write_tx(conn):
        conn.execute("UPDATE contract SET renewal_status = 'terminated' WHERE contract_id = 'C2'")
    gone = analytics.refresh_rule_findings(conn, paths, date(2026, 8, 31))
    assert gone["superseded"] == 2
