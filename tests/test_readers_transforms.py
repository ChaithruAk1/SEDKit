from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest
import xlsxwriter

from sed import calendar as cal
from sed.errors import ValidationFailed
from sed.ingest import transforms as t
from sed.ingest.readers import ReaderOptions, normalize_header, read_table

# --- transforms -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("2 - High", 2), ("3", 3), (4, 4), ("1 - Critical", 1), (None, None), ("", None)],
)
def test_leading_int(raw, expected):
    assert t.leading_int(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("12.345,67", 12345.67),
        ("1,234.50", 1234.5),
        ("1234.5", 1234.5),
        ("12,5", 12.5),
        ("1,234", 1234.0),
        ("€ 12 000", 12000.0),
        ("(1 234,00)", -1234.0),
        (99, 99.0),
    ],
)
def test_money_auto(raw, expected):
    assert t.money(raw) == pytest.approx(expected)


def test_eu_decimal_forced():
    assert t.eu_decimal("1.234") == 1234.0
    assert t.eu_decimal("0,5") == 0.5


def test_bool_values():
    assert t.to_bool("true") is True and t.to_bool("Oui") is True and t.to_bool("N") is False
    assert t.to_bool("") is None
    with pytest.raises(t.TransformError):
        t.to_bool("maybe")


def test_datetime_timezone_and_dst():
    assert t.to_datetime("2026-08-24 09:30:00", tz="Europe/Paris") == "2026-08-24T07:30:00Z"
    assert t.to_datetime("24/08/2026 09:30:00", tz="Europe/Paris") == "2026-08-24T07:30:00Z"
    # 2026-10-25 02:30 happens twice in Paris; fold=0 picks the first (CEST, UTC+2).
    assert t.to_datetime("2026-10-25 02:30:00", tz="Europe/Paris") == "2026-10-25T00:30:00Z"
    assert t.to_datetime("12/Sep/26 2:03 PM", tz="UTC") == "2026-09-12T14:03:00Z"
    assert t.to_datetime(datetime(2026, 1, 15, 12, 0), tz="Europe/Paris") == "2026-01-15T11:00:00Z"
    assert t.to_datetime(46266.5, tz="UTC") == "2026-09-01T12:00:00Z"


def test_date_formats_and_excel_serial():
    assert t.to_date("31/12/2026") == "2026-12-31"
    assert t.to_date("2026-12-31") == "2026-12-31"
    assert t.to_date(date(2026, 1, 2)) == "2026-01-02"
    assert t.excel_serial_date(46266) == "2026-09-01"


@pytest.mark.parametrize(
    ("raw", "seconds"),
    [
        ("1970-01-01 02:03:00", 7380),
        ("1970-01-03 02:03:00", 2 * 86400 + 7380),
        ("1 Day 2 Hours 3 Minutes", 86400 + 7380),
        ("2 jours 3 heures", 2 * 86400 + 3 * 3600),
        ("02:03:04", 7384),
        ("3600", 3600),
        (7200, 7200),
    ],
)
def test_duration(raw, seconds):
    assert t.duration(raw) == seconds


def test_percent_and_split_and_map():
    assert t.percent("95%") == pytest.approx(0.95)
    assert t.percent(95) == pytest.approx(0.95)
    assert t.split_list("a, b,,c") == ["a", "b", "c"]
    assert t.map_values("Y", values={"y": True, "n": False}) is True


# --- calendar -------------------------------------------------------------------------------------------------


def test_iso_week_bounds_in_reporting_tz():
    p = cal.parse_period("2026-W35", "Europe/Paris")
    assert p.start_local == date(2026, 8, 24) and p.end_local == date(2026, 8, 31)
    assert p.start_iso == "2026-08-23T22:00:00Z"
    assert p.previous().label == "2026-W34"


def test_month_quarter_and_fiscal_year():
    assert cal.parse_period("2026-08", "UTC").end_local == date(2026, 9, 1)
    q = cal.parse_period("2026-Q3", "UTC")
    assert (q.start_local, q.end_local) == (date(2026, 7, 1), date(2026, 10, 1))
    q_apr = cal.parse_period("2026-Q1", "UTC", fiscal_year_start=4)
    assert q_apr.start_local == date(2025, 4, 1)
    assert cal.quarter_label(date(2025, 5, 3), fiscal_year_start=4) == "2026-Q1"
    assert cal.quarter_label(date(2026, 8, 3)) == "2026-Q3"
    assert cal.shift_label("2026-01", -1) == "2025-12"
    assert cal.shift_label("2026-Q1", -1) == "2025-Q4"
    assert cal.shift_label("2026-W01", -1) == "2025-W52"
    with pytest.raises(ValidationFailed):
        cal.parse_period("August", "UTC")


def test_month_boundary_across_dst():
    oct_ = cal.parse_period("2026-10", "Europe/Paris")
    assert oct_.start_iso == "2026-09-30T22:00:00Z"  # CEST
    assert oct_.end_iso == "2026-10-31T23:00:00Z"  # CET after DST ends


# --- readers --------------------------------------------------------------------------------------------------


def test_normalize_header():
    assert normalize_header("Custom field (Story Points)") == "custom field story points"
    assert normalize_header("Catégorie") == "categorie"


def test_csv_cp1252_semicolon_with_title_rows(tmp_path: Path):
    f = tmp_path / "Budget_FY26.csv"
    content = "Budget FY26;;\n;;\nApplication;Catégorie;Montant\nOrion ERP;Maintenance évolutive;12.345,67\n"
    f.write_bytes(content.encode("cp1252"))
    table = read_table(f, ReaderOptions(), {"Application", "Catégorie", "Montant"})
    assert table.encoding == "cp1252" and table.delimiter == ";"
    assert table.columns == ["Application", "Catégorie", "Montant"]
    assert table.rows == [["Orion ERP", "Maintenance évolutive", "12.345,67"]]
    assert any("cp1252" in w for w in table.warnings)
    assert any("row 3" in w for w in table.warnings)


def test_csv_utf8_bom_and_utf16(tmp_path: Path):
    f = tmp_path / "a.csv"
    f.write_bytes("\ufeffnumber,short_description\nINC1,Café down\n".encode())
    assert read_table(f).rows == [["INC1", "Café down"]]
    g = tmp_path / "b.csv"
    g.write_text("number\tstate\nINC2\tNew\n", encoding="utf-16")
    table = read_table(g)
    assert table.columns == ["number", "state"] and table.rows == [["INC2", "New"]]


def test_jira_duplicate_headers_fold(tmp_path: Path):
    f = tmp_path / "jira.csv"
    f.write_text("Issue key,Labels,Labels,Sprint\nAPP-1,backend,,S1\nAPP-2,a,b,\n", encoding="utf-8")
    table = read_table(f, ReaderOptions(fold_duplicate_headers=True))
    assert table.columns == ["Issue key", "Labels", "Sprint"]
    assert table.rows[0] == ["APP-1", ["backend"], "S1"]
    assert table.rows[1] == ["APP-2", ["a", "b"], None]


def test_xlsx_merged_header_and_title_rows(tmp_path: Path):
    f = tmp_path / "IT_Cost_Actuals.xlsx"
    wb = xlsxwriter.Workbook(str(f))
    ws = wb.add_worksheet("Actuals")
    ws.write(0, 0, "IT Cost Actuals FY25-FY26")
    ws.write_row(2, 0, ["Application", "Vendor", "Cost Category"])
    ws.merge_range(2, 3, 2, 4, "FY25")
    ws.write_row(3, 3, ["Jan", "Feb"])
    ws.write_row(4, 0, ["Orion ERP", "Nordwind", "Hosting", "12.345,67", 100.5])
    wb.close()
    table = read_table(f, ReaderOptions(), {"Application", "Vendor", "Cost Category"})
    assert table.columns == ["Application", "Vendor", "Cost Category", "FY25 | Jan", "FY25 | Feb"]
    assert table.rows == [["Orion ERP", "Nordwind", "Hosting", "12.345,67", 100.5]]
    assert "merged two-row header joined" in table.warnings


def test_xlsx_sheet_pattern_and_missing_sheet(tmp_path: Path):
    f = tmp_path / "x.xlsx"
    wb = xlsxwriter.Workbook(str(f))
    wb.add_worksheet("Readme").write(0, 0, "ignore")
    ws = wb.add_worksheet("Contracts 2026")
    ws.write_row(0, 0, ["Contract No", "Vendor"])
    ws.write_row(1, 0, ["C-1", "Acme"])
    wb.close()
    assert read_table(f, ReaderOptions(sheet="contracts*")).rows == [["C-1", "Acme"]]
    with pytest.raises(ValidationFailed):
        read_table(f, ReaderOptions(sheet="nope*"))


def test_iqy_rejected(tmp_path: Path):
    f = tmp_path / "list.iqy"
    f.write_text("WEB\n1\nhttps://example.sharepoint.com/_vti_bin/owssvr.dll\n", encoding="utf-8")
    with pytest.raises(ValidationFailed, match="CSV"):
        read_table(f)
