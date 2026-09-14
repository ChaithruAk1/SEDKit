"""Reporting calendar: periods (ISO week, month, quarter) bucketed in settings.reporting_tz, stored as UTC.

Period labels: ``2026-W35`` (ISO week), ``2026-08`` (month), ``2026-Q3`` (fiscal quarter from
``fiscal_year_start``), ``FY2026`` (fiscal year). Bounds are half-open ``[start, end)`` in UTC.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sed.errors import ValidationFailed

WEEK_RE = re.compile(r"^(\d{4})-W(\d{2})$")
MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")
QUARTER_RE = re.compile(r"^(\d{4})-Q([1-4])$")
FY_RE = re.compile(r"^FY(\d{4})$")


@dataclass(frozen=True)
class Period:
    label: str
    kind: str  # week | month | quarter | year
    start_local: date
    end_local: date  # exclusive
    tz: str

    @property
    def start_utc(self) -> datetime:
        return local_midnight_utc(self.start_local, self.tz)

    @property
    def end_utc(self) -> datetime:
        return local_midnight_utc(self.end_local, self.tz)

    @property
    def start_iso(self) -> str:
        return iso_utc(self.start_utc)

    @property
    def end_iso(self) -> str:
        return iso_utc(self.end_utc)

    @property
    def last_day(self) -> date:
        return self.end_local - timedelta(days=1)

    def previous(self, n: int = 1) -> Period:
        p = self
        for _ in range(n):
            p = parse_period(shift_label(p.label, -1, p.kind), p.tz)
        return p


def iso_utc(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def local_midnight_utc(day: date, tz: str) -> datetime:
    return datetime.combine(day, time(0, 0), tzinfo=ZoneInfo(tz)).astimezone(UTC)


def _month_add(year: int, month: int, delta: int) -> tuple[int, int]:
    idx = year * 12 + (month - 1) + delta
    return idx // 12, idx % 12 + 1


def fiscal_quarter_start(fy_label_year: int, quarter: int, fiscal_year_start: int) -> date:
    """FY named by the calendar year in which it ENDS when fiscal_year_start > 1 (FY2026 = Apr 2025–Mar 2026)."""
    start_year = fy_label_year if fiscal_year_start == 1 else fy_label_year - 1
    y, m = _month_add(start_year, fiscal_year_start, 3 * (quarter - 1))
    return date(y, m, 1)


def parse_period(label: str, tz: str, fiscal_year_start: int = 1) -> Period:
    label = label.strip()
    if m := WEEK_RE.match(label):
        year, week = int(m.group(1)), int(m.group(2))
        try:
            start = date.fromisocalendar(year, week, 1)
        except ValueError as exc:
            raise ValidationFailed(f"Invalid ISO week '{label}'") from exc
        return Period(label, "week", start, start + timedelta(days=7), tz)
    if m := MONTH_RE.match(label):
        year, month = int(m.group(1)), int(m.group(2))
        if not 1 <= month <= 12:
            raise ValidationFailed(f"Invalid month '{label}'")
        ny, nm = _month_add(year, month, 1)
        return Period(label, "month", date(year, month, 1), date(ny, nm, 1), tz)
    if m := QUARTER_RE.match(label):
        start = fiscal_quarter_start(int(m.group(1)), int(m.group(2)), fiscal_year_start)
        ny, nm = _month_add(start.year, start.month, 3)
        return Period(label, "quarter", start, date(ny, nm, 1), tz)
    if m := FY_RE.match(label):
        start = fiscal_quarter_start(int(m.group(1)), 1, fiscal_year_start)
        return Period(label, "year", start, date(start.year + 1, start.month, 1), tz)
    raise ValidationFailed(f"Unrecognised period '{label}' (use 2026-W35, 2026-08, 2026-Q3 or FY2026)")


def shift_label(label: str, delta: int, kind: str | None = None) -> str:
    if m := WEEK_RE.match(label):
        start = date.fromisocalendar(int(m.group(1)), int(m.group(2)), 1) + timedelta(weeks=delta)
        iso = start.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    if m := MONTH_RE.match(label):
        y, mo = _month_add(int(m.group(1)), int(m.group(2)), delta)
        return f"{y}-{mo:02d}"
    if m := QUARTER_RE.match(label):
        idx = int(m.group(1)) * 4 + int(m.group(2)) - 1 + delta
        return f"{idx // 4}-Q{idx % 4 + 1}"
    if m := FY_RE.match(label):
        return f"FY{int(m.group(1)) + delta}"
    raise ValidationFailed(f"Cannot shift period '{label}'")


def week_label(day: date) -> str:
    iso = day.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def month_label(day: date) -> str:
    return f"{day.year}-{day.month:02d}"


def quarter_label(day: date, fiscal_year_start: int = 1) -> str:
    offset = (day.month - fiscal_year_start) % 12
    fy_start_year = day.year if day.month >= fiscal_year_start else day.year - 1
    fy_label = fy_start_year if fiscal_year_start == 1 else fy_start_year + 1
    return f"{fy_label}-Q{offset // 3 + 1}"


def to_local(ts_utc: str | datetime, tz: str) -> datetime:
    dt = parse_utc(ts_utc) if isinstance(ts_utc, str) else ts_utc
    return dt.astimezone(ZoneInfo(tz))


def parse_utc(value: str) -> datetime:
    """Parse our stored ISO-8601 UTC text ('2026-09-01T10:00:00Z')."""
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def as_of_end_utc(as_of: date, tz: str) -> datetime:
    """End of the as-of day (exclusive bound = next local midnight)."""
    return local_midnight_utc(as_of + timedelta(days=1), tz)
