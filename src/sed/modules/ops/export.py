"""The ticket list as a workbook: what the reader is looking at, in the columns they chose.

The dashboard already shows a filtered, column-chosen view; this writes that same view to .xlsx so it can be sent on
or worked in. Values come from the read model, so the PII rules that produced them still hold: pseudonyms stay
pseudonyms, and scrubbed text stays scrubbed.

The file is written into the profile's `out` folder. Nothing is written anywhere else, and nothing leaves DATA_DIR.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import xlsxwriter

from sed.paths import Paths

# Column key -> (heading, attribute on the read model). The keys are the dashboard's, so a layout saved there names
# the same columns here and the workbook matches the screen.
COLUMNS: dict[str, tuple[str, str]] = {
    "number": ("Number", "number"),
    "priority": ("Priority", "priority"),
    "state": ("State", "state"),
    "short_description": ("Short description", "short_description"),
    "app": ("Application", "app_name"),
    "group": ("Assignment group", "assignment_group"),
    "category": ("ServiceNow category", "sn_category"),
    "opened_at": ("Opened", "opened_at"),
    "resolved_at": ("Resolved", "resolved_at"),
    "kind": ("Kind", "kind"),
    "vendor": ("Vendor", "vendor_name"),
    "am_category": ("AI category", "am_category"),
    "am_subcategory": ("AI subcategory", "am_subcategory"),
}
DEFAULT_ORDER = ("number", "priority", "state", "opened_at", "resolved_at", "group", "app", "short_description")
MAX_WIDTH = 60


def _value(row: Any, attr: str) -> Any:
    value = getattr(row, attr, None)
    return "" if value is None else value


def write_ticket_workbook(paths: Paths, rows: list[Any], column_keys: list[str] | None) -> Path:
    """Write `rows` as a workbook and return its path. Unknown column keys are ignored, so a layout naming a column
    this export does not carry still produces a file rather than an error."""
    chosen = [k for k in (column_keys or DEFAULT_ORDER) if k in COLUMNS] or list(DEFAULT_ORDER)
    folder = paths.out / "exports"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    suffix = "" if paths.data_class == "real" else f"_{paths.data_class.upper()}"
    path = folder / f"tickets_{stamp}{suffix}.xlsx"

    book = xlsxwriter.Workbook(str(path), {"constant_memory": True, "default_date_format": "yyyy-mm-dd hh:mm"})
    try:
        sheet = book.add_worksheet("Tickets")
        head = book.add_format({"bold": True, "bg_color": "#EFEFEF", "bottom": 1})
        widths = []
        for index, key in enumerate(chosen):
            heading = COLUMNS[key][0]
            sheet.write_string(0, index, heading, head)
            widths.append(len(heading) + 2)
        for r, row in enumerate(rows, start=1):
            for c, key in enumerate(chosen):
                value = _value(row, COLUMNS[key][1])
                if isinstance(value, bool):  # before int: bool is an int in Python
                    sheet.write_string(r, c, "Yes" if value else "No")
                elif isinstance(value, int | float):
                    sheet.write_number(r, c, value)
                else:
                    text = str(value)
                    sheet.write_string(r, c, text)
                    widths[c] = min(MAX_WIDTH, max(widths[c], len(text) + 2))
        for index, width in enumerate(widths):
            sheet.set_column(index, index, width)
        sheet.freeze_panes(1, 0)
        sheet.autofilter(0, 0, max(len(rows), 1), len(chosen) - 1)
    finally:
        book.close()
    return path
