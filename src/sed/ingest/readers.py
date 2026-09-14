"""Tolerant readers for real-world exports.

CSV: encoding sniffing (BOM / UTF-8 strict / cp1252 fallback / UTF-16), delimiter sniffing (, ; tab |).
XLSX/XLS: python-calamine, sheet selection by name pattern.
Both: header-row auto-detection against the mapping's known column aliases, merged two-row headers
(forward-filled and joined), duplicate headers folded into lists (Jira), locked-file retry, `.iqy` rejection.
"""

from __future__ import annotations

import codecs
import csv
import fnmatch
import io
import re
import shutil
import tempfile
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from sed.errors import PreconditionFailed, ValidationFailed

CSV_SUFFIXES = {".csv", ".tsv", ".txt"}
EXCEL_SUFFIXES = {".xlsx", ".xlsm", ".xls", ".xlsb", ".ods"}
MAX_HEADER_SCAN_ROWS = 20


@dataclass
class ReaderOptions:
    encoding: str = "auto"
    delimiter: str = "auto"
    sheet: str | None = None  # fnmatch pattern; None = first sheet
    header_row: int | str = "auto"  # 1-based row number or "auto"
    merged_header: bool | str = "auto"
    fold_duplicate_headers: bool = False


@dataclass
class RawTable:
    columns: list[str]
    rows: list[list[Any]]
    source: str
    encoding: str | None = None
    delimiter: str | None = None
    sheet: str | None = None
    header_row: int = 1
    warnings: list[str] = field(default_factory=list)

    def records(self) -> list[dict[str, Any]]:
        return [dict(zip(self.columns, row, strict=False)) for row in self.rows]


def normalize_header(name: Any) -> str:
    """Case/accent/punctuation-insensitive header key: 'Custom field (Story Points)' -> 'custom field story points'."""
    text = "" if name is None else str(name)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^0-9a-zA-Z]+", " ", text).strip().lower()
    return text


# ---------------------------------------------------------------------------
# low-level I/O
# ---------------------------------------------------------------------------


def _read_bytes_with_retry(path: Path, attempts: int = 3, delay: float = 1.0) -> bytes:
    last: OSError | None = None
    for _ in range(attempts):
        try:
            return path.read_bytes()
        except PermissionError as exc:
            last = exc
            time.sleep(delay)
    raise PreconditionFailed(f"{path.name} is locked (open in Excel?). Close the file and retry.") from last


def decode_csv_bytes(raw: bytes, encoding: str = "auto") -> tuple[str, str]:
    if encoding != "auto":
        return raw.decode(encoding), encoding
    for bom, codec in (
        (codecs.BOM_UTF8, "utf-8-sig"),
        (codecs.BOM_UTF16_LE, "utf-16"),
        (codecs.BOM_UTF16_BE, "utf-16"),
    ):
        if raw.startswith(bom):
            return raw.decode(codec), codec
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace"), "cp1252"


def sniff_delimiter(text: str, preferred: str = "auto") -> str:
    if preferred != "auto":
        return "\t" if preferred in {"tab", "\\t"} else preferred
    sample = "\n".join(text.splitlines()[:50])
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        counts = {d: sample.count(d) for d in ",;\t|"}
        return max(counts, key=counts.get) if any(counts.values()) else ","


def _read_csv_grid(path: Path, opts: ReaderOptions) -> tuple[list[list[Any]], str, str]:
    raw = _read_bytes_with_retry(path)
    text, enc = decode_csv_bytes(raw, opts.encoding)
    delim = sniff_delimiter(text, opts.delimiter)
    grid = [list(r) for r in csv.reader(io.StringIO(text, newline=""), delimiter=delim)]
    return grid, enc, delim


def _cell(value: Any) -> Any:
    if isinstance(value, str):
        return value if value.strip() else None
    if isinstance(value, float) and value != value:  # NaN
        return None
    return value


def _read_excel_grid(path: Path, opts: ReaderOptions) -> tuple[list[list[Any]], str]:
    from python_calamine import CalamineWorkbook

    try:
        wb = CalamineWorkbook.from_path(str(path))
    except PermissionError:
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / path.name
            try:
                shutil.copyfile(path, copy)
            except PermissionError as exc:
                raise PreconditionFailed(f"{path.name} is locked (open in Excel?). Close it and retry.") from exc
            wb = CalamineWorkbook.from_path(str(copy))
    except Exception as exc:  # calamine raises its own error types
        raise ValidationFailed(f"Cannot read workbook {path.name}: {exc}") from exc
    names = wb.sheet_names
    if not names:
        raise ValidationFailed(f"{path.name} has no sheets")
    chosen = names[0]
    if opts.sheet:
        matches = [n for n in names if fnmatch.fnmatch(n.lower(), opts.sheet.lower())]
        if not matches:
            raise ValidationFailed(f"{path.name}: no sheet matches '{opts.sheet}' (sheets: {names})")
        chosen = matches[0]
    grid = [[_cell(v) for v in row] for row in wb.get_sheet_by_name(chosen).to_python(skip_empty_area=False)]
    return grid, chosen


# ---------------------------------------------------------------------------
# header handling
# ---------------------------------------------------------------------------


def header_score(row: list[Any], known: set[str]) -> int:
    return sum(1 for cell in row if cell is not None and normalize_header(cell) in known)


def detect_header_row(grid: list[list[Any]], known: set[str]) -> int:
    """0-based index of the most plausible header row within the first rows."""
    best_idx, best_score = 0, -1
    for idx, row in enumerate(grid[:MAX_HEADER_SCAN_ROWS]):
        nonempty = sum(1 for c in row if c not in (None, ""))
        if nonempty == 0:
            continue
        score = header_score(row, known)
        if score > best_score:
            best_idx, best_score = idx, score
    return best_idx


def _is_text_label(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and not re.fullmatch(r"[\d.,\s%€$()+-]+", value)


def _looks_like_subheader(top: list[Any], below: list[Any]) -> bool:
    """Second header row under merged group cells (e.g. 'FY25' spanning 'Jan'..'Dec').

    Heuristic: the next row holds only text labels (no numbers/dates), at least two of them, and at least one
    label sits under a blank top cell (the tail of a merged range).
    """
    labels = 0
    under_blank = False
    for i, under in enumerate(below):
        if under in (None, ""):
            continue
        if not _is_text_label(under):
            return False
        labels += 1
        top_cell = top[i] if i < len(top) else None
        if top_cell in (None, ""):
            under_blank = True
    return labels >= 2 and under_blank


def join_merged_header(top: list[Any], below: list[Any]) -> list[str]:
    width = max(len(top), len(below))
    filled: list[str] = []
    last = ""
    for i in range(width):
        cell = top[i] if i < len(top) else None
        if cell not in (None, ""):
            last = str(cell).strip()
        filled.append(last)
    out = []
    for i in range(width):
        lower_label = below[i] if i < len(below) else None
        top_label = filled[i]
        if lower_label not in (None, "") and top_label and normalize_header(top_label) != normalize_header(lower_label):
            out.append(f"{top_label} | {str(lower_label).strip()}")
        elif lower_label not in (None, ""):
            out.append(str(lower_label).strip())
        else:
            out.append(top_label)
    return out


def fold_duplicates(columns: list[str], rows: list[list[Any]]) -> tuple[list[str], list[list[Any]]]:
    positions: dict[str, list[int]] = {}
    order: list[str] = []
    for idx, name in enumerate(columns):
        if name not in positions:
            positions[name] = []
            order.append(name)
        positions[name].append(idx)
    if all(len(p) == 1 for p in positions.values()):
        return columns, rows
    new_rows = []
    for row in rows:
        new_row = []
        for name in order:
            idxs = positions[name]
            if len(idxs) == 1:
                new_row.append(row[idxs[0]] if idxs[0] < len(row) else None)
            else:
                values = [row[i] for i in idxs if i < len(row) and row[i] not in (None, "")]
                new_row.append(values or None)
        new_rows.append(new_row)
    return order, new_rows


def dedupe_names(columns: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for name in columns:
        if name in seen:
            seen[name] += 1
            out.append(f"{name}__{seen[name]}")
        else:
            seen[name] = 1
            out.append(name)
    return out


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------


def read_table(path: Path, opts: ReaderOptions | None = None, known_headers: set[str] | None = None) -> RawTable:
    opts = opts or ReaderOptions()
    known = {normalize_header(h) for h in (known_headers or set())}
    suffix = path.suffix.lower()
    warnings: list[str] = []
    encoding = delimiter = sheet = None

    if suffix == ".iqy":
        raise ValidationFailed(
            f"{path.name} is an Excel web query (.iqy), not data. Export the SharePoint list as CSV instead."
        )
    if suffix in CSV_SUFFIXES:
        grid, encoding, delimiter = _read_csv_grid(path, opts)
        if encoding == "cp1252":
            warnings.append("encoding: cp1252 (not UTF-8) detected")
        if delimiter != ",":
            warnings.append(f"delimiter: {delimiter!r} detected")
    elif suffix in EXCEL_SUFFIXES:
        grid, sheet = _read_excel_grid(path, opts)
    else:
        raise ValidationFailed(f"Unsupported file type: {path.name}")

    grid = [row for row in grid]
    if not grid:
        return RawTable([], [], str(path), encoding, delimiter, sheet, 1, [*warnings, "empty file"])

    auto_header = detect_header_row(grid, known) if known else 0
    h_idx = auto_header if opts.header_row == "auto" else int(opts.header_row) - 1
    if h_idx > 0:
        warnings.append(f"header found on row {h_idx + 1} (title rows skipped)")

    header = grid[h_idx]
    data_start = h_idx + 1
    merged = opts.merged_header
    if data_start < len(grid) and (
        merged is True or (merged == "auto" and _looks_like_subheader(header, grid[data_start]))
    ):
        header = join_merged_header(header, grid[data_start])
        data_start += 1
        warnings.append("merged two-row header joined")

    columns = [str(c).strip() if c not in (None, "") else f"column_{i + 1}" for i, c in enumerate(header)]
    rows = [row for row in grid[data_start:] if any(c not in (None, "") for c in row)]
    width = len(columns)
    rows = [(row + [None] * (width - len(row)))[:width] if len(row) != width else row for row in rows]

    if opts.fold_duplicate_headers:
        columns, rows = fold_duplicates(columns, rows)
    elif len(set(columns)) != len(columns):
        columns = dedupe_names(columns)
        warnings.append("duplicate column names suffixed")

    rows = [[_normalize_cell(v) for v in row] for row in rows]
    return RawTable(columns, rows, str(path), encoding, delimiter, sheet, h_idx + 1, warnings)


def _normalize_cell(value: Any) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped if stripped else None
    if isinstance(value, datetime | date | int | float | list) or value is None:
        return value
    return str(value)
