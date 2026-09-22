"""Shared building blocks for the ops read models: request context, filter SQL, periods, KPIs and findings mapping.

Every function here is read-only. Routes pass a query_only connection from `sed.api.deps.read_conn`; nothing in this
package calls `create_snapshot`, `refresh_rule_findings` or `findings_as_of`.
"""

from __future__ import annotations

import sqlite3
from bisect import bisect_right
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from sed import metrics
from sed.api.deps import CommonFilters
from sed.api.findings import published_findings
from sed.api.models import FindingOut, Kpi
from sed.calendar import Period, as_of_end_utc, iso_utc, local_midnight_utc, month_label, parse_period, week_label
from sed.errors import ValidationFailed
from sed.paths import Paths
from sed.settings import Settings, load_settings

FINDINGS_SCAN_LIMIT = 100_000

# Definitions for KPI keys that are not in sed.metrics.METRICS (shown as dashboard tooltips).
KPI_DEFINITIONS: dict[str, str] = {
    "cost.actual.ytd": "Actual cost (base currency) for the complete fiscal-year months before the as-of month.",
    "cost.budget.ytd": "Budget (newest budget version per month, base currency) for the same fiscal-year-to-date "
    "months.",
    "cost.variance.ytd_pct": "(actual - budget) / budget for the fiscal-year-to-date months.",
    "renewals.90d.count": "Active contracts (not non-renewing/terminated/expired) ending within 90 days of as-of.",
    "notice.30d.count": "Active contracts whose notice deadline falls within 30 days of as-of.",
    "license.idle_cost": "Idle cost of under-used license lines (utilization below the license_utilization low risk "
    "rule, default 70%): max(entitled - active_90d, 0) "
    "x unit cost, base currency.",
    "review.queue.count": "Items awaiting human review: AI findings in draft or update_pending, plus completed AI runs "
    "not yet approved or rejected.",
    "inc.opened.3m": "Incidents opened in the last three complete months before as-of.",
    "inc.sla.pct.3m": "Share of incidents resolved in the last three complete months that met their resolution SLA.",
    "inc.mttr.median_h.3m": "Median hours from opened_at to resolved_at for incidents resolved in the last three "
    "complete months.",
    "inc.p1p2.opened.3m": "Priority 1 and 2 incidents opened in the last three complete months.",
    "license.utilization": "Sum of active_90d over sum of entitled quantity for license lines with a usage snapshot "
    "on or before as-of.",
    "license.annual_cost": "Sum over license lines of entitled quantity x unit cost (base currency).",
    "contracts.annual_value": "Annual value (base currency) of the application's active, non-deleted contracts.",
    "renewals.180d.count": "Active contracts ending or reaching their notice deadline within 180 days of as-of.",
}


@dataclass(frozen=True)
class Context:
    """Everything a read model needs for one request."""

    conn: sqlite3.Connection
    paths: Paths
    settings: Settings
    filters: CommonFilters
    data_as_of: date | None
    as_of: date

    @property
    def tz(self) -> str:
        return self.settings.reporting_tz

    @property
    def as_of_end_iso(self) -> str:
        """Exclusive end of the as-of day (next local midnight), for "current state" views."""
        return iso_utc(as_of_end_utc(self.as_of, self.tz))

    @property
    def as_of_start_iso(self) -> str:
        """Local midnight at the start of the as-of day: the exclusive end of "to date" windows (D20)."""
        return iso_utc(local_midnight_utc(self.as_of, self.tz))

    def parse(self, label: str) -> Period:
        return parse_period(label, self.tz, self.settings.fiscal_year_start)


def build_context(conn: sqlite3.Connection, paths: Paths, filters: CommonFilters | None = None) -> Context:
    from sed.ingest.freshness import data_as_of

    settings = load_settings(paths)
    f = filters or CommonFilters()
    data_date = data_as_of(conn, settings)
    return Context(conn, paths, settings, f, data_date, f.as_of or data_date or date.today())


# ---------------------------------------------------------------------------
# filters
# ---------------------------------------------------------------------------


def marks(values: Sequence[Any]) -> str:
    return ", ".join("?" for _ in values)


def ticket_filter_sql(f: CommonFilters, alias: str = "t") -> tuple[list[str], list[Any]]:
    """WHERE clauses for app (repeatable), family, vendor and group on a ticket alias."""
    clauses, params = entity_filter_sql(f, alias)
    if f.group:
        clauses.append(f"{alias}.assignment_group = ?")
        params.append(f.group)
    return clauses, params


def entity_filter_sql(f: CommonFilters, alias: str) -> tuple[list[str], list[Any]]:
    """WHERE clauses for app, family and vendor on a table with app_id and vendor_id (contract, license, cost_line).

    The assignment-group filter does not apply to commercial data and is ignored here.
    """
    clauses: list[str] = []
    params: list[Any] = []
    if f.app:
        clauses.append(f"{alias}.app_id IN ({marks(f.app)})")
        params += list(f.app)
    if f.family:
        clauses.append(f"{alias}.app_id IN (SELECT app_id FROM application WHERE app_family = ?)")
        params.append(f.family)
    if f.vendor:
        clauses.append(f"{alias}.vendor_id = ?")
        params.append(f.vendor)
    return clauses, params


def has_entity_filter(f: CommonFilters) -> bool:
    return bool(f.app or f.family or f.vendor)


def metrics_filters(f: CommonFilters, kind: str = "incident") -> metrics.Filters:
    return metrics.Filters(kind=kind, app_ids=list(f.app), family=f.family, vendor_id=f.vendor, group=f.group)


def where_sql(clauses: Iterable[str]) -> str:
    items = list(clauses)
    return " AND ".join(items) if items else "1 = 1"


def allowed_ids(ctx: Context, table: str, id_col: str) -> set[str] | None:
    """Ids in a commercial table matching the entity filters, or None when no entity filter is set."""
    f = ctx.filters
    if not has_entity_filter(f):
        return None
    clauses, params = entity_filter_sql(f, "x")
    sql = f"SELECT x.{id_col} FROM {table} x WHERE {where_sql(clauses)}"
    return {r[0] for r in ctx.conn.execute(sql, params)}


# ---------------------------------------------------------------------------
# periods
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Window:
    """A half-open UTC window [start_iso, end_iso) with a display label."""

    label: str
    start_iso: str
    end_iso: str


def last_full_period(ctx: Context, kind: str, before: date | None = None) -> Period:
    """Latest week or month whose exclusive local end is on or before `before` (default as-of): a closed period, D20."""
    day = ctx.as_of if before is None else before
    if kind == "week":
        return ctx.parse(week_label(day - timedelta(days=7)))
    if kind == "month":
        return ctx.parse(month_label(day.replace(day=1) - timedelta(days=1)))
    raise ValidationFailed(f"Unsupported granularity '{kind}'")


def selected_period(ctx: Context, default_kind: str = "week") -> Period:
    return ctx.parse(ctx.filters.period) if ctx.filters.period else last_full_period(ctx, default_kind)


def trend_periods(end: Period, n: int) -> list[Period]:
    return [end.previous(k) for k in range(n - 1, -1, -1)]


def series_end(ctx: Context, granularity: str) -> Period:
    """The last period of a trend series: always a closed week or month, so no bucket is partial or counts tickets
    after as-of.

    Without a period filter: the last full week or month before as-of. With a period of the same granularity that has
    ended by as-of: that period. Otherwise (a coarser period such as a quarter, or a period still in progress): the
    last full week or month ending by both the period end and as-of.
    """
    if ctx.filters.period:
        period = ctx.parse(ctx.filters.period)
        if period.kind == granularity and period.end_local <= ctx.as_of:
            return period
        return last_full_period(ctx, granularity, min(period.end_local, ctx.as_of))
    return last_full_period(ctx, granularity)


def clamp_end(period: Period, ctx: Context) -> str:
    """Exclusive UTC end of a period clamped to the start of the as-of day (to-date window, D20)."""
    return min(period.end_iso, ctx.as_of_start_iso)


def window(period: Period, ctx: Context) -> Window:
    return Window(period.label, period.start_iso, clamp_end(period, ctx))


def last_months_window(ctx: Context, n: int) -> Window:
    end = last_full_period(ctx, "month")
    start = end.previous(n - 1)
    return Window(f"{start.label}..{end.label}", start.start_iso, end.end_iso)


def months_before(as_of: date, n: int) -> list[str]:
    """The n complete calendar months before the as-of month, oldest first (same as the weekly report)."""
    out = []
    d = date(as_of.year, as_of.month, 1)
    for _ in range(n):
        d = date(d.year, d.month, 1) - timedelta(days=1)
        out.append(f"{d.year}-{d.month:02d}")
        d = date(d.year, d.month, 1)
    return list(reversed(out))


def ytd_months(ctx: Context) -> list[str]:
    """Complete fiscal-year months before the as-of month."""
    fy_start = ctx.settings.fiscal_year_start
    year = ctx.as_of.year if ctx.as_of.month >= fy_start else ctx.as_of.year - 1
    months = []
    y, m = year, fy_start
    while (y, m) < (ctx.as_of.year, ctx.as_of.month):
        months.append(f"{y}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return months


def period_months(period: Period, ctx: Context) -> list[str]:
    """Calendar months of a period that are complete on or before as-of."""
    months = []
    d = period.start_local.replace(day=1)
    limit = min(period.end_local, ctx.as_of.replace(day=1))
    while d < limit:
        months.append(f"{d.year}-{d.month:02d}")
        d = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
    return months


class Bucketer:
    """Assigns ISO-8601 UTC timestamps to consecutive periods by string comparison (same as SQL `>= start AND <
    end`)."""

    def __init__(self, periods: Sequence[Period | Window]) -> None:
        self.starts = [p.start_iso for p in periods]
        self.ends = [p.end_iso for p in periods]

    def index(self, ts: str | None) -> int | None:
        if ts is None:
            return None
        i = bisect_right(self.starts, ts) - 1
        if i < 0 or ts >= self.ends[i]:
            return None
        return i


# ---------------------------------------------------------------------------
# values, KPIs and findings
# ---------------------------------------------------------------------------


def pct(num: float, den: float) -> float | None:
    return round(100.0 * num / den, 2) if den else None


def kpi(
    key: str,
    label: str,
    value: float | int | str | None,
    unit: str,
    *,
    compare: float | None = None,
    definition_key: str | None = None,
) -> Kpi:
    delta = None
    if isinstance(value, int | float) and not isinstance(value, bool) and compare is not None:
        delta = round(value - compare, 2)
    lookup = definition_key or key
    definition = KPI_DEFINITIONS.get(key) or (metrics.METRICS.get(lookup, (None, None))[1])
    return Kpi(key=key, label=label, value=value, unit=unit, compare=compare, delta=delta, definition=definition)


def has_tickets_in(ctx: Context, period: Period) -> bool:
    """Whether any ticket falls in a window at all.

    A comparison against an empty window reads as a fall to zero, which tells the reader less than no comparison
    does. It happens whenever the window predates the data: a custom range compares with the same dates a year
    earlier, and early in a store's life there is nothing there.
    """
    row = ctx.conn.execute(
        "SELECT 1 FROM ticket WHERE opened_at >= ? AND opened_at < ? LIMIT 1", (period.start_iso, period.end_iso)
    ).fetchone()
    return row is not None


def review_queue_count(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT (SELECT COUNT(*) FROM finding WHERE origin = 'ai' AND status IN ('draft', 'update_pending')) + "
        "(SELECT COUNT(*) FROM ai_run WHERE status = 'completed')"
    ).fetchone()
    return int(row[0] or 0)


def freshness_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return metrics.freshness(conn)


def data_as_of_text(ctx: Context) -> str | None:
    return ctx.data_as_of.isoformat() if ctx.data_as_of else None


@dataclass
class AppIndex:
    """Lookups that relate finding subjects (contracts, licenses, vendors, app/category keys) to applications."""

    contract_app: dict[str, str]
    license_app: dict[str, str]
    vendor_apps: dict[str, list[str]]
    app_names: dict[str, str]

    @classmethod
    def load(cls, conn: sqlite3.Connection) -> AppIndex:
        contract_app = {
            r[0]: r[1] for r in conn.execute("SELECT contract_id, app_id FROM contract WHERE app_id IS NOT NULL")
        }
        license_app = {
            r[0]: r[1] for r in conn.execute("SELECT license_id, app_id FROM license WHERE app_id IS NOT NULL")
        }
        vendor_apps: dict[str, list[str]] = {}
        app_names = {}
        for app_id, name, vendor_id in conn.execute("SELECT app_id, name, primary_vendor_id FROM application"):
            app_names[app_id] = name
            if vendor_id:
                vendor_apps.setdefault(vendor_id, []).append(app_id)
        return cls(contract_app, license_app, vendor_apps, app_names)

    def apps_for(self, finding: FindingOut) -> list[str]:
        subject, kind = finding.subject_id, finding.subject_type
        if not subject:
            return []
        if kind in ("application", "app"):
            return [subject]
        if kind == "contract":
            return [self.contract_app[subject]] if subject in self.contract_app else []
        if kind == "license":
            return [self.license_app[subject]] if subject in self.license_app else []
        if kind == "vendor":
            return list(self.vendor_apps.get(subject, []))
        if kind == "app_category":
            return [app_id for app_id, name in self.app_names.items() if subject.startswith(f"{name} / ")]
        return []


def findings_by_app(conn: sqlite3.Connection, as_of: date) -> dict[str, list[FindingOut]]:
    index = AppIndex.load(conn)
    out: dict[str, list[FindingOut]] = {}
    for finding in published_findings(conn, as_of, limit=FINDINGS_SCAN_LIMIT):
        for app_id in index.apps_for(finding):
            out.setdefault(app_id, []).append(finding)
    return out
