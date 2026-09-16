"""SAP L3 read models on the SAP profile: the scope selects exactly the generated SAP tickets, areas and landscapes
follow the ground truth, planted patterns show, the negative control stays quiet and breakdowns add up to the totals."""

from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from typing import Any

import pytest

from sed.calendar import as_of_end_utc, iso_utc, parse_period
from sed.modules.sap.queries import l3
from sed.modules.sap.scope import UNASSIGNED, UNKNOWN, load_scope
from sed.settings import load_settings
from tests.fixtures.ops_profile import AS_OF

WEEK = "2026-W35"  # the last complete week before AS_OF


@pytest.fixture(scope="module")
def env(sap_profile: Any) -> dict[str, Any]:
    settings = load_settings(sap_profile.paths)
    week = parse_period(WEEK, settings.reporting_tz, settings.fiscal_year_start)
    return {
        "scope": load_scope(sap_profile.paths),
        "settings": settings,
        "week": week,
        "window": [week.previous(k) for k in range(7, -1, -1)],  # the 8-week rule window, oldest first
        "at": as_of_end_utc(AS_OF, settings.reporting_tz),
    }


def _sap_rows(conn: Any, scope: Any, columns: str = "t.number", **kw: Any) -> list[Any]:
    sql, params = scope.ticket_sql(**kw)
    return conn.execute(
        f"SELECT {columns} FROM ticket t WHERE t.kind = 'incident' AND {sql.format(t='t')}", params
    ).fetchall()


def test_scope_selects_exactly_the_generated_sap_tickets(ro_conn, env, sap_truth, ops_profile):
    from sed import db

    numbers = [r[0] for r in _sap_rows(ro_conn, env["scope"])]
    assert len(numbers) == len(set(numbers)) == len(sap_truth["tickets"])
    assert set(numbers) == set(sap_truth["tickets"])
    ops = db.connect(ops_profile.paths.db, readonly=True)
    try:
        ops_tickets = ops.execute("SELECT COUNT(*) FROM ticket").fetchone()[0]
        ops_stale = ops.execute("SELECT COUNT(*) FROM ticket WHERE stale_open = 1").fetchone()[0]
    finally:
        ops.close()
    assert ro_conn.execute("SELECT COUNT(*) FROM ticket").fetchone()[0] == ops_tickets + len(numbers)
    # The SAP open-incident snapshot flags only tickets SAP files loaded: the ops stale flags are untouched.
    assert ro_conn.execute("SELECT COUNT(*) FROM ticket WHERE stale_open = 1").fetchone()[0] == ops_stale


def test_area_and_landscape_follow_the_ground_truth(ro_conn, env, sap_truth):
    scope = env["scope"]
    app_landscape = {app: x.code for x in scope.config.landscapes for app in x.apps}
    rows = _sap_rows(ro_conn, scope, "t.number, t.assignment_group, t.app_id")
    assert all(app_id in app_landscape for _, _, app_id in rows), "every SAP ticket resolves to an SAP application"
    for number, group, app_id in rows:
        truth = sap_truth["tickets"][number]
        assert scope.area_of(group) == truth["area"], number
        assert app_landscape[app_id] == truth["landscape"], number
    for code in [*l3.area_order(scope)]:
        expected = sum(1 for t in sap_truth["tickets"].values() if t["area"] == code)
        assert len(_sap_rows(ro_conn, scope, area=code)) == expected, code
    for code in l3.landscape_order(scope):
        expected = sum(1 for t in sap_truth["tickets"].values() if t["landscape"] == code)
        assert len(_sap_rows(ro_conn, scope, landscape=code)) == expected, code
    assert _sap_rows(ro_conn, scope, landscape=UNKNOWN) == []


def test_category_and_custom_field_tickets_are_unassigned(ro_conn, env, sap_truth):
    marked = {n for n, t in sap_truth["tickets"].items() if t["pattern"] in ("SC1", "SC2")}
    assert marked and {r[0] for r in _sap_rows(ro_conn, env["scope"], area=UNASSIGNED)} == marked
    raw = dict(ro_conn.execute("SELECT number, raw_keep_json FROM ticket WHERE raw_keep_json IS NOT NULL").fetchall())
    sc2 = {n for n, t in sap_truth["tickets"].items() if t["pattern"] == "SC2"}
    assert sc2 and all('"u_sap_component"' in raw[n] for n in sc2)


def test_sp1_ewm_backlog_growth_is_visible(ro_conn, env, sap_truth):
    rows = [r for r in l3.flow_by_area(ro_conn, env["scope"], env["window"]) if r["area"] == "ewm"]
    assert [r["period"] for r in rows] == [p.label for p in env["window"]]
    assert sum(r["net"] > 0 for r in rows) >= 6
    assert sum(r["net"] for r in rows) >= 30
    backlog = {r["area"]: r for r in l3.backlog(ro_conn, env["scope"], env["at"])["by_area"]}
    assert backlog["ewm"]["total"] >= sap_truth["patterns"]["SP1"]["open_at_as_of"]
    assert backlog["ewm"]["d31_90"] + backlog["ewm"]["d90p"] >= 10


def test_sn1_sd_surge_is_matched_by_closures(ro_conn, env, sap_truth):
    rows = [r for r in l3.flow_by_area(ro_conn, env["scope"], env["window"]) if r["area"] == "sd"]
    arrived = sum(r["arrived"] for r in rows)
    assert arrived >= sap_truth["patterns"]["SN1"]["tickets"]  # the surge sits inside the rule window
    assert sum(r["net"] for r in rows) < 15
    assert sum(r["net"] > 0 for r in rows) < 6


def test_sp2_month_end_close_failures_on_business_days_1_and_2(ro_conn, env, sap_truth):
    days = set(sap_truth["patterns"]["SP2"]["days"])
    rows = _sap_rows(ro_conn, env["scope"], "t.opened_at, t.short_description", area="fi_co")
    per_day = Counter(opened[:10] for opened, short in rows if short.startswith("Period-end close job failed"))
    assert set(per_day) == days and set(per_day.values()) == {6}
    other = Counter(opened[:10] for opened, _ in rows if opened[:10] not in days)
    assert max(other.values(), default=0) < 6


def test_backlog_breakdowns_add_up(ro_conn, env):
    scope, at = env["scope"], env["at"]
    backlog = l3.backlog(ro_conn, scope, at)
    assert backlog["total"] == sum(backlog["aging"].values()) == sum(r["total"] for r in backlog["by_area"])
    assert [r["area"] for r in backlog["by_area"]] == [
        c for c in l3.area_order(scope) if c in {r["area"] for r in backlog["by_area"]}
    ]
    for row in backlog["by_area"]:
        assert row["total"] == row["d0_7"] + row["d8_30"] + row["d31_90"] + row["d90p"]
        assert l3.backlog(ro_conn, scope, at, area=row["area"])["total"] == row["total"]
    landscapes = l3.backlog_by_landscape(ro_conn, scope, at)
    assert [x["landscape"] for x in landscapes] == ["ecc", "s4"]  # "unknown" only when it holds tickets
    assert sum(x["open"] for x in landscapes) == backlog["total"]
    ewm = l3.backlog_by_landscape(ro_conn, scope, at, area="ewm")
    assert sum(x["open"] for x in ewm) == l3.backlog(ro_conn, scope, at, area="ewm")["total"]


def test_backlog_matches_hand_written_sql(ro_conn, env, sap_truth):
    at = iso_utc(env["at"])
    marks = ", ".join("?" for _ in sap_truth["tickets"])
    expected = ro_conn.execute(
        f"SELECT COUNT(*) FROM ticket WHERE kind = 'incident' AND number IN ({marks}) AND opened_at < ? "
        "AND (resolved_at IS NULL OR resolved_at >= ?) AND (closed_at IS NULL OR closed_at >= ?) AND stale_open = 0",
        [*sap_truth["tickets"], at, at, at],
    ).fetchone()[0]
    assert l3.backlog(ro_conn, env["scope"], env["at"])["total"] == expected > 0


def test_trend_matches_hand_written_sql(ro_conn, env, sap_truth):
    week = env["week"]
    marks = ", ".join("?" for _ in sap_truth["tickets"])
    opened, resolved = ro_conn.execute(
        f"SELECT SUM(opened_at >= ? AND opened_at < ?), SUM(resolved_at >= ? AND resolved_at < ?) FROM ticket "
        f"WHERE kind = 'incident' AND number IN ({marks})",
        [week.start_iso, week.end_iso, week.start_iso, week.end_iso, *sap_truth["tickets"]],
    ).fetchone()
    source = "made_sla"
    rows = l3.trend(ro_conn, env["scope"], env["window"], source)
    assert rows[-1]["period"] == WEEK
    assert (rows[-1]["opened"], rows[-1]["resolved"]) == (opened, resolved)
    assert rows[-1]["net"] == opened - resolved
    ewm = l3.trend(ro_conn, env["scope"], env["window"], source, area="ewm", landscape="s4")
    assert all(e["opened"] <= r["opened"] for e, r in zip(ewm, rows, strict=True))


def test_week_kpis_averages(ro_conn, env):
    week = env["week"]
    previous = [week.previous(k) for k in range(4, 0, -1)]
    kpis = l3.week_kpis(ro_conn, env["scope"], week, previous, "made_sla")
    trend = l3.trend(ro_conn, env["scope"], [*previous, week], "made_sla")
    assert kpis["opened"] == trend[-1]["opened"] and kpis["resolved"] == trend[-1]["resolved"]
    assert kpis["opened_avg"] == round(sum(r["opened"] for r in trend[:-1]) / 4, 2)
    assert kpis["sla_pct"] == trend[-1]["sla_pct"]


def test_area_summary_rows(ro_conn, env):
    scope, week, at = env["scope"], env["week"], env["at"]
    rows = l3.area_summary(ro_conn, scope, week, at, "made_sla")
    codes = [r["area"] for r in rows]
    assert codes[: len(scope.config.areas)] == [a.code for a in scope.config.areas]  # every configured area, in order
    by_area = {r["area"]: r for r in l3.backlog(ro_conn, scope, at)["by_area"]}
    flow = {r["area"]: r for r in l3.flow_by_area(ro_conn, scope, [week])}
    for row in rows:
        assert row["open"] == by_area.get(row["area"], {}).get("total", 0)
        assert row["opened"] == flow.get(row["area"], {}).get("arrived", 0)
        assert (row["sla_pct"] is None) == (row["resolved"] == 0)
    assert rows[codes.index("ewm")]["aged_30d"] >= 10


def test_attention_lists_open_sap_tickets_without_people(ro_conn, env, sap_truth):
    result = l3.attention(ro_conn, env["scope"], env["at"], env["settings"].thresholds)
    assert result["count"] >= len(result["items"]) > 0
    for item in result["items"]:
        assert item["number"] in sap_truth["tickets"]
        assert item["area"] == sap_truth["tickets"][item["number"]]["area"]
        assert item["area_label"] == env["scope"].area_labels[item["area"]]
        assert not {"assigned_to_pid", "caller_pid", "reassignment_count", "reopen_count"} & set(item)
    ewm = l3.attention(ro_conn, env["scope"], env["at"], env["settings"].thresholds, area="ewm")
    assert ewm["items"] and {i["area"] for i in ewm["items"]} == {"ewm"}


def test_resolved_scope_gives_the_same_numbers(ro_conn, env):
    from sed import metrics

    scope, at, window, week = env["scope"], env["at"], env["window"], env["week"]
    resolved = scope.resolve(ro_conn)
    assert resolved.tickets is not None and len(resolved.tickets) == len(_sap_rows(ro_conn, scope))
    source = metrics.sla_source(ro_conn)
    thresholds = env["settings"].thresholds
    for area in [None, *l3.area_order(scope)]:
        for landscape in [None, *l3.landscape_order(scope)]:
            kw = {"area": area, "landscape": landscape}
            assert l3.backlog(ro_conn, resolved, at, **kw) == l3.backlog(ro_conn, scope, at, **kw), kw
            assert l3.attention(ro_conn, resolved, at, thresholds, **kw) == l3.attention(
                ro_conn, scope, at, thresholds, **kw
            )
    assert l3.trend(ro_conn, resolved, window, source) == l3.trend(ro_conn, scope, window, source)
    assert l3.trend(ro_conn, resolved, window, source, area="ewm") == l3.trend(
        ro_conn, scope, window, source, area="ewm"
    )
    assert l3.flow_by_area(ro_conn, resolved, window) == l3.flow_by_area(ro_conn, scope, window)
    assert l3.area_summary(ro_conn, resolved, week, at, source) == l3.area_summary(ro_conn, scope, week, at, source)
    assert l3.backlog_by_landscape(ro_conn, resolved, at) == l3.backlog_by_landscape(ro_conn, scope, at)
    previous = window[-5:-1]
    assert l3.week_kpis(ro_conn, resolved, week, previous, source) == l3.week_kpis(
        ro_conn, scope, week, previous, source
    )


def test_resolved_scope_queries_start_from_the_ticket_key(ro_conn, env):
    resolved = env["scope"].resolve(ro_conn)
    where, params = l3.filters(resolved, area="ewm").where()
    assert where.startswith("+t.kind = ?")
    plan = [
        r[3]
        for r in ro_conn.execute(
            f"EXPLAIN QUERY PLAN SELECT t.opened_at FROM ticket t WHERE {where} AND t.opened_at < ? "
            "AND (t.resolved_at IS NULL OR t.resolved_at >= ?)",
            [*params, "2026-09-02T00:00:00Z", "2026-09-02T00:00:00Z"],
        )
    ]
    assert any("(ticket_id=?)" in step for step in plan), plan  # not a walk over every incident by opened_at


def test_an_empty_week_gives_zero_rows_not_errors(ro_conn, env):
    settings = env["settings"]
    old = parse_period("2024-W10", settings.reporting_tz, settings.fiscal_year_start)
    assert l3.flow_by_area(ro_conn, env["scope"], [old]) == []
    assert l3.backlog(ro_conn, env["scope"], old.start_utc)["total"] == 0
    kpis = l3.week_kpis(ro_conn, env["scope"], old, [old.previous(1)], "made_sla")
    assert kpis["opened"] == 0 and kpis["sla_pct"] is None and kpis["mttr_median_h"] is None


def test_rule_window_ends_with_the_last_complete_week():
    from sed.calendar import week_label

    assert week_label(AS_OF - timedelta(days=7)) == WEEK
    assert week_label(date(2026, 8, 30) - timedelta(days=7)) == "2026-W34"
