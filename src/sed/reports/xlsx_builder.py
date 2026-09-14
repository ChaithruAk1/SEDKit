"""XLSX report builder (XlsxWriter): Summary with KPIs and native charts, one Excel Table per snapshot table,
conditional formats from the spec, Definitions and Provenance sheets, SYNTHETIC stamp.

Workbooks are created with strings_to_formulas/strings_to_urls disabled so ticket text starting with '=' or
containing URLs can never become a formula or hyperlink in a workbook shared across the organisation.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import xlsxwriter

from sed.metrics import METRICS
from sed.reports.snapshot import Snapshot
from sed.reports.specs import ReportSpec

FORMATS = {
    "count": {"num_format": "#,##0"},
    "number": {"num_format": "#,##0.0"},
    "pct": {"num_format": '0.0"%"'},
    "pp": {"num_format": '+0.0" pp";-0.0" pp"'},
    "ratio": {"num_format": "0%"},
    "hours": {"num_format": '0.0" h"'},
    "eur": {"num_format": "€#,##0"},
    "text": {},
    "date": {},
    "datetime": {},
}
STYLES = {
    "bad": {"bg_color": "#F8D7DA", "font_color": "#842029"},
    "warn": {"bg_color": "#FFF3CD", "font_color": "#664D03"},
    "good": {"bg_color": "#D1E7DD", "font_color": "#0F5132"},
}


def _table_name(key: str) -> str:
    return "T_" + re.sub(r"[^A-Za-z0-9_]", "_", key)[:200]


def build_xlsx(snapshot: Snapshot, spec: ReportSpec, out_path: Path, *, ai_mode: str, generated_at: str) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb = xlsxwriter.Workbook(
        str(out_path), {"strings_to_formulas": False, "strings_to_urls": False, "strings_to_numbers": False}
    )
    wb.set_properties({"title": f"{spec.title} {snapshot.period}", "comments": f"snapshot {snapshot.snapshot_id}"})
    fmts = {k: wb.add_format(v) for k, v in FORMATS.items()}
    bold = wb.add_format({"bold": True})
    title_fmt = wb.add_format({"bold": True, "font_size": 16})
    banner = wb.add_format({"bold": True, "font_color": "#FFFFFF", "bg_color": "#B02A37"})
    wrap = wb.add_format({"text_wrap": True, "valign": "top"})
    styles = {k: wb.add_format(v) for k, v in STYLES.items()}

    summary = wb.add_worksheet("Summary")
    row = 0
    if snapshot.data_class == "synthetic":
        summary.merge_range(row, 0, row, 5, "SYNTHETIC DATA - generated test data, not for distribution", banner)
        row += 2
    summary.write_string(row, 0, spec.title, title_fmt)
    row += 1
    summary.write_string(
        row,
        0,
        f"Period {snapshot.period}  |  as of {snapshot.as_of}  |  timezone {snapshot.reporting_tz}"
        f"  |  SLA source {snapshot.sla_source}  |  AI content: {ai_mode}",
    )
    row += 2
    summary.write_row(row, 0, ["Indicator", "Value", "Comparison", "Change"], bold)
    row += 1
    for kpi in spec.kpis:
        f = snapshot.facts.get(kpi.fact)
        if not f:
            continue
        summary.write_string(row, 0, f["label"])
        _write_value(summary, row, 1, f["value"], fmts.get(f["unit"], fmts["text"]))
        if kpi.compare and kpi.compare in snapshot.facts:
            c = snapshot.facts[kpi.compare]
            _write_value(summary, row, 2, c["value"], fmts.get(c["unit"], fmts["text"]))
        if kpi.delta and kpi.delta in snapshot.facts:
            d = snapshot.facts[kpi.delta]
            _write_value(summary, row, 3, d["value"], fmts.get(d["unit"], fmts["text"]))
        row += 1
    summary.set_column(0, 0, 42)
    summary.set_column(1, 3, 16)
    chart_row = row + 2

    for sheet_spec in spec.sheets:
        tbl = snapshot.tables.get(sheet_spec.table)
        if tbl is None:
            continue
        ws = wb.add_worksheet(sheet_spec.sheet)
        ws.write_string(0, 0, tbl["title"], bold)
        columns = tbl["columns"]
        rows = tbl["rows"]
        header_row = 2
        if rows:
            data = [[r.get(c["key"]) for c in columns] for r in rows]
            ws.add_table(
                header_row,
                0,
                header_row + len(rows),
                len(columns) - 1,
                {
                    "name": _table_name(sheet_spec.table),
                    "columns": [{"header": c["label"], "format": fmts.get(c["format"], fmts["text"])} for c in columns],
                    "style": "Table Style Light 9",
                },
            )
            for r_idx, values in enumerate(data, start=header_row + 1):
                for c_idx, value in enumerate(values):
                    _write_value(ws, r_idx, c_idx, value, fmts.get(columns[c_idx]["format"], fmts["text"]))
            for cond in sheet_spec.conditional:
                col = next((i for i, c in enumerate(columns) if c["key"] == cond.column), None)
                if col is None:
                    continue
                ws.conditional_format(
                    header_row + 1,
                    col,
                    header_row + len(rows),
                    col,
                    {"type": "cell", "criteria": cond.rule, "value": cond.value, "format": styles[cond.style]},
                )
            if sheet_spec.chart:
                _add_chart(wb, summary, ws, sheet_spec, columns, len(rows), header_row, chart_row)
                chart_row += 18
        else:
            ws.write_string(header_row, 0, "No rows for this period.")
        ws.freeze_panes(header_row + 1, 0)
        for c_idx, c in enumerate(columns):
            width = 48 if c["key"] in {"short_description", "title", "reasons", "product", "key"} else 16
            ws.set_column(c_idx, c_idx, width, wrap if width > 40 else None)

    defs = wb.add_worksheet("Definitions")
    defs.write_row(0, 0, ["Metric key", "Unit", "Definition"], bold)
    used = sorted({f["definition"] for f in snapshot.facts.values() if f.get("definition")})
    for i, key in enumerate(used, start=1):
        unit, text = METRICS.get(key, ("", ""))
        defs.write_string(i, 0, key)
        defs.write_string(i, 1, unit)
        defs.write_string(i, 2, text)
    defs.set_column(0, 0, 26)
    defs.set_column(2, 2, 110, wrap)

    prov = wb.add_worksheet("Provenance")
    items = [
        ("Data class", snapshot.data_class.upper()),
        ("Report", spec.title),
        ("Period", snapshot.period),
        ("As of", snapshot.as_of),
        ("Reporting timezone", snapshot.reporting_tz),
        ("SLA source", snapshot.sla_source),
        ("Snapshot id", snapshot.snapshot_id),
        ("Snapshot sha256", snapshot.sha256),
        ("AI content mode", ai_mode),
        ("AI runs used", "none"),
        ("Generated at (UTC)", generated_at),
        ("Code version", snapshot.git_commit or "n/a"),
        ("Person columns", "pseudonymized (P-...) unless display names are enabled on the real profile"),
    ]
    prov.write_row(0, 0, ["Item", "Value"], bold)
    for i, (k, v) in enumerate(items, start=1):
        prov.write_string(i, 0, k)
        prov.write_string(i, 1, str(v))
    r = len(items) + 2
    prov.write_string(r, 0, "Data freshness", bold)
    prov.write_row(r + 1, 0, ["Source mapping", "Last import (UTC)", "Files", "Latest as-of"], bold)
    for i, fr in enumerate(snapshot.freshness, start=r + 2):
        prov.write_row(
            i,
            0,
            [
                str(fr.get("mapping_name") or ""),
                str(fr.get("last_import") or ""),
                str(fr.get("files") or ""),
                str(fr.get("latest_as_of") or ""),
            ],
        )
    r = r + 3 + len(snapshot.freshness)
    prov.write_string(r, 0, "Imported files", bold)
    prov.write_row(r + 1, 0, ["Batch", "File", "Mapping", "Imported (UTC)", "sha256"], bold)
    for i, b in enumerate(snapshot.input_batches[-500:], start=r + 2):
        prov.write_row(
            i, 0, [str(b["batch_id"]), b["file_name"], b["mapping_name"], b["imported_at"], b["file_sha256"]]
        )
    prov.set_column(0, 0, 28)
    prov.set_column(1, 1, 60)
    prov.set_column(4, 4, 66)
    wb.close()
    return out_path


def _write_value(ws: Any, row: int, col: int, value: Any, fmt: Any) -> None:
    if value is None:
        return
    if isinstance(value, bool):
        ws.write_number(row, col, int(value), fmt)
    elif isinstance(value, int | float):
        ws.write_number(row, col, value, fmt)
    else:
        ws.write_string(row, col, str(value), fmt)


def _add_chart(wb, summary, ws, sheet_spec, columns, n_rows, header_row, chart_row) -> None:
    chart_spec = sheet_spec.chart
    keys = [c["key"] for c in columns]
    if chart_spec.categories not in keys:
        return
    chart = wb.add_chart({"type": chart_spec.type})
    cat_col = keys.index(chart_spec.categories)
    for series in chart_spec.series:
        if series not in keys:
            continue
        col = keys.index(series)
        chart.add_series(
            {
                "name": [ws.name, header_row, col],
                "categories": [ws.name, header_row + 1, cat_col, header_row + n_rows, cat_col],
                "values": [ws.name, header_row + 1, col, header_row + n_rows, col],
            }
        )
    chart.set_title({"name": chart_spec.title or sheet_spec.sheet})
    chart.set_size({"width": 720, "height": 320})
    summary.insert_chart(chart_row, 0, chart)
