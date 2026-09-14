"""Value transforms used by column mappings.

Every transform takes the raw cell value (str | int | float | datetime | date | None) plus options and returns a
Python value or raises TransformError (the loader records it as a DQ warning or rejects the row if required).
Datetimes are returned as ISO-8601 UTC text; dates as YYYY-MM-DD text.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

EXCEL_EPOCH = datetime(1899, 12, 30)
TRUE_VALUES = {"true", "yes", "y", "1", "x", "oui", "vrai", "t"}
FALSE_VALUES = {"false", "no", "n", "0", "non", "faux", "f", ""}


class TransformError(ValueError):
    pass


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def to_str(value: Any, **_: Any) -> str | None:
    if _blank(value):
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def to_int(value: Any, **_: Any) -> int | None:
    if _blank(value):
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not value.is_integer():
            raise TransformError(f"not an integer: {value}")
        return int(value)
    text = str(value).strip().replace(" ", "").replace(" ", "")
    try:
        return int(float(text.replace(",", "."))) if re.fullmatch(r"-?\d+([.,]0+)?", text) else int(text)
    except ValueError as exc:
        raise TransformError(f"not an integer: {value!r}") from exc


def leading_int(value: Any, **_: Any) -> int | None:
    """'2 - High' -> 2; '3' -> 3."""
    if _blank(value):
        return None
    if isinstance(value, int | float):
        return int(value)
    m = re.match(r"\s*(\d+)", str(value))
    if not m:
        raise TransformError(f"no leading integer in {value!r}")
    return int(m.group(1))


_NUM_CLEAN = re.compile(r"[^\d,.\-+eE]")


def parse_number(value: Any, *, decimal: str = "auto") -> float | None:
    """Parse '1.234,56' (EU), '1,234.56' (US), '1234.56', '(1 234,00)', '€ 12 000'."""
    if _blank(value):
        return None
    if isinstance(value, bool):
        raise TransformError("boolean is not a number")
    if isinstance(value, int | float):
        return float(value)
    text = str(value).strip()
    negative = text.startswith("(") and text.endswith(")")
    text = _NUM_CLEAN.sub("", text.replace(" ", "").replace(" ", ""))
    if not text or text in {"-", "+"}:
        raise TransformError(f"not a number: {value!r}")
    if decimal == "auto":
        if "," in text and "." in text:
            decimal = "," if text.rfind(",") > text.rfind(".") else "."
        elif "," in text:
            # '12,5' / '12,50' -> decimal comma; '1,234' / '1,234,567' -> thousands separator.
            thousands_only = bool(re.fullmatch(r"-?\d{1,3}(,\d{3})+", text))
            decimal = "." if thousands_only else ","
        else:
            decimal = "."
    text = text.replace(".", "").replace(",", ".") if decimal == "," else text.replace(",", "")
    try:
        number = float(text)
    except ValueError as exc:
        raise TransformError(f"not a number: {value!r}") from exc
    return -number if negative else number


def to_float(value: Any, **opts: Any) -> float | None:
    return parse_number(value, decimal=opts.get("decimal", "auto"))


def eu_decimal(value: Any, **_: Any) -> float | None:
    return parse_number(value, decimal=",")


def money(value: Any, **_: Any) -> float | None:
    return parse_number(value, decimal="auto")


def percent(value: Any, **_: Any) -> float | None:
    """'95%' -> 0.95; 95 -> 0.95; 0.95 -> 0.95."""
    if _blank(value):
        return None
    text = str(value).strip()
    has_pct = text.endswith("%")
    number = parse_number(text.rstrip("%"))
    if number is None:
        return None
    return number / 100 if has_pct or number > 1 else number


def to_bool(value: Any, **_: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return bool(value)
    text = str(value).strip().lower()
    if text in TRUE_VALUES:
        return True
    if text in FALSE_VALUES:
        return None if text == "" else False
    raise TransformError(f"not a boolean: {value!r}")


def excel_serial_to_datetime(serial: float) -> datetime:
    if not 0 < serial < 2_958_466:
        raise TransformError(f"not an Excel serial date: {serial}")
    return EXCEL_EPOCH + timedelta(days=float(serial))


def _parse_local_datetime(value: Any, formats: list[str]) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, int | float):
        return excel_serial_to_datetime(float(value))
    text = str(value).strip()
    if re.fullmatch(r"\d{5}(\.\d+)?", text):
        return excel_serial_to_datetime(float(text))
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TransformError(f"unparseable datetime {value!r} (formats {formats})") from exc


DEFAULT_DATETIME_FORMATS = [
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%d-%m-%Y %H:%M:%S",
    "%d/%b/%y %I:%M %p",
    "%d/%b/%Y %I:%M %p",
    "%m/%d/%Y %I:%M:%S %p",
    "%Y-%m-%dT%H:%M:%S",
]
DEFAULT_DATE_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%d.%m.%Y", "%d-%m-%Y", "%d/%b/%y", "%d-%b-%Y", "%m/%d/%Y"]


def to_datetime(value: Any, *, formats: list[str] | None = None, tz: str = "UTC", **_: Any) -> str | None:
    """Local wall-clock text in the exporting user's timezone -> ISO UTC. DST-ambiguous times use fold=0."""
    if _blank(value):
        return None
    dt = _parse_local_datetime(value, formats or DEFAULT_DATETIME_FORMATS)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(tz), fold=0)
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def to_date(value: Any, *, formats: list[str] | None = None, **_: Any) -> str | None:
    if _blank(value):
        return None
    dt = _parse_local_datetime(value, formats or DEFAULT_DATE_FORMATS + DEFAULT_DATETIME_FORMATS)
    return dt.date().isoformat()


def excel_serial_date(value: Any, **_: Any) -> str | None:
    if _blank(value):
        return None
    return excel_serial_to_datetime(float(value)).date().isoformat()


_DURATION_PARTS = {
    "day": 86400,
    "days": 86400,
    "jour": 86400,
    "jours": 86400,
    "j": 86400,
    "hour": 3600,
    "hours": 3600,
    "heure": 3600,
    "heures": 3600,
    "h": 3600,
    "minute": 60,
    "minutes": 60,
    "min": 60,
    "mins": 60,
    "second": 1,
    "seconds": 1,
    "seconde": 1,
    "secondes": 1,
    "sec": 1,
    "secs": 1,
    "s": 1,
}


def duration(value: Any, **_: Any) -> int | None:
    """ServiceNow durations -> seconds.

    Accepts epoch datetimes ('1970-01-03 02:03:00' = 2 days 2h 3m), display strings
    ('1 Day 2 Hours 3 Minutes', '2 jours 3 heures'), 'HH:MM:SS', and integer seconds.
    """
    if _blank(value):
        return None
    if isinstance(value, int | float):
        return int(value)
    if isinstance(value, datetime):
        return int((value.replace(tzinfo=None) - datetime(1970, 1, 1)).total_seconds())
    text = str(value).strip()
    if re.fullmatch(r"\d+", text):
        return int(text)
    if re.fullmatch(r"1970-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}", text):
        return int(
            (datetime.strptime(text.replace("T", " "), "%Y-%m-%d %H:%M:%S") - datetime(1970, 1, 1)).total_seconds()
        )
    if m := re.fullmatch(r"(\d+):(\d{2}):(\d{2})", text):
        h, mi, s = (int(x) for x in m.groups())
        return h * 3600 + mi * 60 + s
    parts = re.findall(r"(\d+)\s*([A-Za-zéèû]+)", text)
    if parts and all(unit.lower() in _DURATION_PARTS for _, unit in parts):
        return sum(int(n) * _DURATION_PARTS[unit.lower()] for n, unit in parts)
    raise TransformError(f"unparseable duration {value!r}")


def split_list(value: Any, *, sep: str = ",", **_: Any) -> list[str] | None:
    if _blank(value):
        return None
    if isinstance(value, list):
        return [str(v).strip() for v in value if not _blank(v)]
    return [part.strip() for part in str(value).split(sep) if part.strip()]


def map_values(value: Any, *, values: dict[str, Any] | None = None, default: Any = "__keep__", **_: Any) -> Any:
    if _blank(value):
        return None
    table = {str(k).strip().lower(): v for k, v in (values or {}).items()}
    key = str(value).strip().lower()
    if key in table:
        return table[key]
    if default == "__keep__":
        return str(value).strip()
    return default


def lower(value: Any, **_: Any) -> str | None:
    s = to_str(value)
    return s.lower() if s is not None else None


TRANSFORMS: dict[str, Callable[..., Any]] = {
    "str": to_str,
    "int": to_int,
    "leading_int": leading_int,
    "float": to_float,
    "money": money,
    "eu_decimal": eu_decimal,
    "percent": percent,
    "bool": to_bool,
    "date": to_date,
    "datetime": to_datetime,
    "excel_serial_date": excel_serial_date,
    "duration": duration,
    "split_list": split_list,
    "map_values": map_values,
    "lower": lower,
}


def apply_transform(name: str, value: Any, **opts: Any) -> Any:
    try:
        fn = TRANSFORMS[name]
    except KeyError as exc:
        raise TransformError(f"unknown transform '{name}'") from exc
    return fn(value, **opts)
