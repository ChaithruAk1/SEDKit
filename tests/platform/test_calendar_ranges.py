"""Custom date ranges as a period kind (`2024-10-01..2024-11-30`).

A range is a period label like any other, so it reaches filters, URLs and every query on the paths the calendar
periods already use. These pin the parts that differ from a week or a month: inclusive bounds, and a `previous`
that steps a year rather than a window of its own shape.
"""

from __future__ import annotations

from datetime import date

import pytest

from sed.calendar import parse_period
from sed.errors import ValidationFailed

TZ = "Europe/Paris"


def test_a_range_covers_both_named_days():
    """Both days are inclusive for the reader; end_local stays exclusive for the SQL, as with every other kind."""
    period = parse_period("2024-10-01..2024-11-30", TZ)
    assert period.kind == "range"
    assert period.start_local == date(2024, 10, 1)
    assert period.last_day == date(2024, 11, 30)
    assert period.end_local == date(2024, 12, 1)


def test_a_single_day_range_is_that_day():
    period = parse_period("2024-10-01..2024-10-01", TZ)
    assert period.start_local == date(2024, 10, 1)
    assert period.last_day == date(2024, 10, 1)


def test_bounds_are_local_midnight_in_the_reporting_zone():
    period = parse_period("2024-10-01..2024-10-01", TZ)
    assert period.start_iso == "2024-09-30T22:00:00Z"  # CEST is UTC+2
    assert period.end_iso == "2024-10-01T22:00:00Z"


def test_previous_steps_a_year_not_a_window():
    """The owner's choice (2026-09-22): an arbitrary window has no predecessor of its own shape, so the comparison
    is the same dates a year earlier."""
    period = parse_period("2024-10-01..2024-11-30", TZ)
    assert period.previous().label == "2023-10-01..2023-11-30"
    assert period.previous(2).label == "2022-10-01..2022-11-30"


def test_previous_clamps_the_leap_day():
    assert parse_period("2024-02-29..2024-02-29", TZ).previous().label == "2023-02-28..2023-02-28"


def test_other_kinds_still_step_their_own_way():
    """Ranges must not change what `previous` means for the calendar periods."""
    assert parse_period("2026-W35", TZ).previous().label == "2026-W34"
    assert parse_period("2026-08", TZ).previous().label == "2026-07"


@pytest.mark.parametrize(
    "label",
    [
        "2024-11-30..2024-10-01",  # ends before it starts
        "2024-13-01..2024-13-02",  # not a real month
        "2024-10-01..",  # half a range
        "2024-10-01..2024-11-31",  # November has 30 days
    ],
)
def test_a_range_that_cannot_be_read_is_refused(label: str):
    with pytest.raises(ValidationFailed):
        parse_period(label, TZ)
