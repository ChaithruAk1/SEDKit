"""Write synthetic data in the shapes real exports have (field-name CSV, display-label XLSX, messy workbooks)."""

from __future__ import annotations

import csv
import html
from collections.abc import Iterable, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

import xlsxwriter

CSV_DT = "%Y-%m-%d %H:%M:%S"
LABEL_DT = "%d/%m/%Y %H:%M:%S"


def fmt_dt(value: datetime | None, fmt: str = CSV_DT) -> str:
    return value.strftime(fmt) if value else ""


def write_csv(
    path: Path,
    header: Sequence[str],
    rows: Iterable[Sequence[Any]],
    *,
    encoding: str = "utf-8",
    delimiter: str = ",",
    preamble: Sequence[str] = (),
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding=encoding, newline="", errors="strict") as fh:
        writer = csv.writer(fh, delimiter=delimiter)
        for line in preamble:
            writer.writerow([line])
        writer.writerow(header)
        for row in rows:
            writer.writerow(["" if v is None else v for v in row])


def write_xlsx(
    path: Path,
    header: Sequence[str],
    rows: Iterable[Sequence[Any]],
    *,
    sheet: str = "Sheet1",
    title_rows: Sequence[str] = (),
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = xlsxwriter.Workbook(str(path), {"strings_to_formulas": False, "strings_to_urls": False})
    ws = wb.add_worksheet(sheet)
    bold = wb.add_format({"bold": True})
    date_fmt = wb.add_format({"num_format": "dd/mm/yyyy"})
    r = 0
    for title in title_rows:
        ws.write_string(r, 0, title, bold)
        r += 2
    ws.write_row(r, 0, list(header), bold)
    r += 1
    for row in rows:
        for c, value in enumerate(row):
            if value is None or value == "":
                continue
            if isinstance(value, datetime):
                ws.write_datetime(r, c, value, date_fmt)
            elif isinstance(value, date):
                ws.write_datetime(r, c, datetime(value.year, value.month, value.day), date_fmt)
            elif isinstance(value, int | float):
                ws.write_number(r, c, value)
            else:
                ws.write_string(r, c, str(value))
        r += 1
    wb.close()


def write_wide_costs(
    path: Path, id_header: Sequence[str], months: list[date], rows: list[tuple[list[Any], dict[date, str]]], title: str
) -> None:
    """Merged fiscal-year header over month columns; amounts as EU-formatted text (as finance exports do)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = xlsxwriter.Workbook(str(path), {"strings_to_formulas": False, "strings_to_urls": False})
    ws = wb.add_worksheet("Actuals")
    bold = wb.add_format({"bold": True, "align": "center"})
    ws.write_string(0, 0, title, bold)
    header_row = 2
    for c, name in enumerate(id_header):
        ws.write_string(header_row, c, name, bold)
    years = sorted({m.year for m in months})
    col = len(id_header)
    month_cols: dict[date, int] = {}
    for year in years:
        year_months = [date(year, mo, 1) for mo in range(1, 13)]
        first = col
        for m in year_months:
            ws.write_string(header_row + 1, col, m.strftime("%b"), bold)
            month_cols[m] = col
            col += 1
        ws.merge_range(header_row, first, header_row, col - 1, f"FY{year % 100:02d}", bold)
    r = header_row + 2
    for ids, amounts in rows:
        for c, value in enumerate(ids):
            if value not in (None, ""):
                ws.write_string(r, c, str(value))
        for m, text in amounts.items():
            if m in month_cols:
                ws.write_string(r, month_cols[m], text)
        r += 1
    wb.close()


def eu_amount(value: float) -> str:
    text = f"{value:,.2f}"
    return text.replace(",", "X").replace(".", ",").replace("X", ".")


def write_confluence_space(root: Path, space_key: str, space_name: str, pages: list[dict[str, Any]]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    links = []
    for page in pages:
        file_name = f"{page['title'].replace(' ', '-')}_{page['page_id']}.html"
        links.append(f'<li><a href="{html.escape(file_name)}">{html.escape(page["title"])}</a></li>')
        labels = "".join(f'<li><a class="label" href="#">{html.escape(lbl)}</a></li>' for lbl in page["labels"])
        body = (
            f"<!DOCTYPE html><html><head><title>{html.escape(space_key)} : {html.escape(page['title'])}</title></head>"
            f'<body><div id="main-header"><h1 id="title-heading">{html.escape(page["title"])}</h1></div>'
            f'<div class="page-metadata">Created by {html.escape(page["author"])}, last modified on '
            f"{page['modified'].strftime('%b %d, %Y')}</div>"
            f'<div id="main-content" class="wiki-content group"><p>{html.escape(page["body"])}</p></div>'
            f'<div class="labels"><ul>{labels}</ul></div></body></html>'
        )
        (root / file_name).write_text(body, encoding="utf-8")
    (root / "index.html").write_text(
        f"<html><head><title>{html.escape(space_name)}</title></head><body><ul>{''.join(links)}</ul></body></html>",
        encoding="utf-8",
    )
