"""Parity: the single-scan read models in sed.modules.ops.queries return exactly what the report metrics in sed.metrics
return for the same inputs, so dashboard numbers equal report numbers."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from sed import metrics
from sed.api.deps import CommonFilters
from sed.calendar import parse_period
from sed.modules.ops.queries import commercial, tickets
from sed.modules.ops.queries.common import (
    Bucketer,
    Window,
    build_context,
    last_full_period,
    months_before,
    period_months,
    series_end,
    trend_periods,
    ytd_months,
)

TZ = "Europe/Paris"
AS_OF = date(2026, 9, 1)


@pytest.fixture
def ctx(ops_profile: Any, ro_conn: Any):
    return build_context(ro_conn, ops_profile.paths, CommonFilters())


def weeks(n: int = 12) -> list:
    end = parse_period("2026-W35", TZ)
    return [end.previous(k) for k in range(n - 1, -1, -1)]


def test_context_dates(ctx):
    assert ctx.as_of == ctx.data_as_of == AS_OF
    assert last_full_period(ctx, "week").label == "2026-W35"
    assert last_full_period(ctx, "month").label == "2026-08"
    assert series_end(ctx, "week").label == "2026-W35"
    assert months_before(AS_OF, 3) == ["2026-06", "2026-07", "2026-08"]
    assert ytd_months(ctx) == [f"2026-{m:02d}" for m in range(1, 9)]
    assert period_months(parse_period("2026-Q3", TZ), ctx) == ["2026-07", "2026-08"]


@pytest.mark.parametrize(
    ("as_of", "week", "month"),
    [
        (date(2026, 8, 30), "2026-W34", "2026-07"),  # Sunday: its own week is not complete yet
        (date(2026, 8, 31), "2026-W35", "2026-07"),  # Monday: the previous week just closed
        (date(2026, 9, 1), "2026-W35", "2026-08"),
        (date(2026, 1, 1), "2025-W52", "2025-12"),
    ],
)
def test_last_full_period_boundaries(ops_profile, ro_conn, as_of, week, month):
    ctx = build_context(ro_conn, ops_profile.paths, CommonFilters(as_of=as_of))
    assert last_full_period(ctx, "week").label == week
    assert last_full_period(ctx, "month").label == month


def test_bucketer_matches_sql_bounds():
    periods = weeks(3)
    buckets = Bucketer(periods)
    assert buckets.index(periods[0].start_iso) == 0
    assert buckets.index(periods[1].start_iso) == 1
    assert buckets.index(periods[-1].end_iso) is None
    assert buckets.index("2000-01-01T00:00:00Z") is None and buckets.index(None) is None
    clamped = Bucketer([Window("w", periods[0].start_iso, periods[0].start_iso)])
    assert clamped.index(periods[0].start_iso) is None


def test_volumes_match_metrics(ctx, ro_conn):
    for kind in ("incident", "sc_req_item"):
        periods = weeks()
        expected = metrics.volume_trend(ro_conn, metrics.Filters(kind=kind), periods)
        assert [r.model_dump() for r in tickets.volume_rows(ctx, kind, periods)] == expected
    months = trend_periods(parse_period("2026-08", TZ), 18)
    assert [r.model_dump() for r in tickets.volume_rows(ctx, "incident", months)] == metrics.volume_trend(
        ro_conn, metrics.Filters(), months
    )


def test_sla_and_mttr_match_metrics(ctx, ro_conn):
    out = tickets.sla(ctx, "week", 12)
    periods = weeks()
    source = metrics.sla_source(ro_conn)
    assert out.sla_source == source
    by_priority: dict[int, list[int]] = {}
    for row, period in zip(out.items, periods, strict=True):
        expected = metrics.sla(ro_conn, metrics.Filters(), period, source)
        assert (row.period, row.pct, row.met, row.total) == (
            period.label,
            expected["pct"],
            expected["met"],
            expected["total"],
        )
        for prio, values in expected["by_priority"].items():
            acc = by_priority.setdefault(prio, [0, 0])
            acc[0] += values["total"]
            acc[1] += values["met"]
    assert {p.priority: (p.total, p.met) for p in out.by_priority} == {
        (f"P{k}" if k else "P?"): tuple(v) for k, v in by_priority.items()
    }
    for source_name in ("made_sla", "targets"):
        rows = tickets.resolved_rows(ctx, periods[-1].start_iso, periods[-1].end_iso, source=source_name)
        expected = metrics.sla(ro_conn, metrics.Filters(), periods[-1], source_name)
        assert (len(rows), sum(1 for r in rows if r[2])) == (expected["total"], expected["met"])

    mttr = tickets.mttr(ctx, "week", 12)
    for row, period in zip(mttr.items, periods, strict=True):
        expected = metrics.mttr(ro_conn, metrics.Filters(), period)
        assert row.model_dump() == {"period": period.label, **expected}


def test_backlog_matches_metrics(ctx, ro_conn):
    key_map = {"0-7d": "d0_7", "8-30d": "d8_30", "31-90d": "d31_90", ">90d": "d90p"}
    for at_period in ("2026-W35", "2026-W30", "2026-05"):
        at = parse_period(at_period, TZ).end_utc
        at_iso = parse_period(at_period, TZ).end_iso
        expected = metrics.backlog(ro_conn, metrics.Filters(), at)
        everything = metrics.backlog(ro_conn, metrics.Filters(), at, exclude_stale=False)
        got = tickets.backlog_summary(ctx, at_iso)
        assert got["total"] == expected["total"]
        assert got["stale_excluded"] == everything["total"] - expected["total"]
        assert got["aging"] == {key_map[k]: v for k, v in expected["aging"].items()}
        groups = {
            (g or "(unassigned)"): {key_map.get(k, k): v for k, v in values.items()}
            for g, values in got["by_group"].items()
        }
        assert groups == {
            g: {key_map.get(k, k): v for k, v in values.items()} for g, values in expected["by_group"].items()
        }


def test_flow_matches_group_flow(ctx, ro_conn):
    periods = weeks()
    expected = metrics.group_flow(ro_conn, metrics.Filters(), periods)
    got = tickets.flow(ctx, periods)
    assert sorted((r.period, r.group or "", r.arrived, r.closed) for r in got) == sorted(
        (r["period"], r["group"] or "", r["arrived"], r["closed"]) for r in expected
    )


def test_vendor_trend_matches_metrics(ctx, ro_conn):
    for months in (6, 3):
        expected = {v["vendor_id"]: v for v in metrics.vendor_sla_trend(ro_conn, AS_OF, TZ, months=months)}
        got = commercial.vendor_trend(ctx, months)
        assert {v.vendor_id for v in got.items} == set(expected)
        for row in got.items:
            ref = expected[row.vendor_id]
            assert row.vendor == ref["vendor"] and row.delta_pp == ref["delta_pp"]
            assert [(p.period, p.sla_pct, p.mttr_median_h, p.reassign_avg, p.tickets) for p in row.series] == [
                (p["period"], p["sla_pct"], p["mttr_median_h"], p["reassign_avg"], p["resolved"]) for p in ref["series"]
            ]


def test_costs_match_cost_vs_budget(ctx, ro_conn):
    months = months_before(AS_OF, 2)
    expected = {r["key"]: r for r in metrics.cost_vs_budget(ro_conn, months, "app_category")}
    got = {r.key: r for r in commercial.cost_rows(ctx, months, "app_category")}
    assert set(got) == set(expected)
    for key, row in got.items():
        assert (row.actual, row.budget, row.variance_pct) == (
            expected[key]["actual"],
            expected[key]["budget"],
            expected[key]["variance_pct"],
        )
