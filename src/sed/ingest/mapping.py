"""Column-mapping specifications (config/<module>/mappings/*.yaml) and file-to-mapping matching.

A mapping says which source columns feed which canonical fields of a target, how values are transformed, and
which PII class each field has (mandatory). Mapping directories come from the enabled modules (`Module.mappings_dir`,
indexed by `sed.modules.mapping_index`); a mapping name must be unique across modules, and `target` names an ingest
target declared by a module (`Module.ingest_targets`). Local overrides in DATA_DIR\\config\\<module>\\mappings use
``extends:`` and usually only add header aliases (``from+``), u_* fields, formats and source_tz; a DATA_DIR-only
mapping is discovered as well.
"""

from __future__ import annotations

import fnmatch
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from sed.errors import ValidationFailed
from sed.ingest.readers import RawTable, ReaderOptions, normalize_header, read_table
from sed.ingest.transforms import TRANSFORMS
from sed.paths import Paths
from sed.settings import config_sha256, load_layered

LoadMode = Literal["delta", "full_snapshot", "append_snapshot", "active_snapshot"]
PiiClass = Literal["none", "person", "free_text"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class FieldSpec(_Strict):
    from_: list[str] = Field(alias="from")
    transform: str = "str"
    pii: PiiClass
    required: bool = False
    default: Any = None
    formats: list[str] | None = None
    options: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_transform(self) -> FieldSpec:
        if self.transform not in TRANSFORMS:
            raise ValueError(f"unknown transform '{self.transform}'")
        if not self.from_:
            raise ValueError("'from' must list at least one source column")
        return self


class MatchSpec(_Strict):
    glob: list[str]
    min_score: float = 0.6


class ReaderSpec(_Strict):
    encoding: str = "auto"
    delimiter: str = "auto"
    sheet: str | None = None
    header_row: int | Literal["auto"] = "auto"
    merged_header: bool | Literal["auto"] = "auto"
    fold_duplicate_headers: bool = False

    def options(self) -> ReaderOptions:
        return ReaderOptions(
            encoding=self.encoding,
            delimiter=self.delimiter,
            sheet=self.sheet,
            header_row=self.header_row,
            merged_header=self.merged_header,
            fold_duplicate_headers=self.fold_duplicate_headers,
        )


class UnpivotSpec(_Strict):
    """Wide month columns -> long rows. ``columns_regex`` named groups feed ``var_template`` + ``var_format``."""

    columns_regex: str
    var_template: str
    var_format: str
    var_output: str = "%Y-%m"
    var_name: str = "period"
    value_name: str = "amount"


class AsOfSpec(_Strict):
    from_filename: str | None = None  # regex with one capture group
    format: str = "%Y-%m-%d"
    from_field: str | None = None


class MappingSpec(_Strict):
    name: str
    target: str
    load_mode: LoadMode
    format: Literal["table", "confluence_html"] = "table"
    description: str = ""
    match: MatchSpec
    reader: ReaderSpec = ReaderSpec()
    source_tz: str = "UTC"
    constants: dict[str, Any] = Field(default_factory=dict)
    fields: dict[str, FieldSpec]
    unpivot: UnpivotSpec | None = None
    as_of: AsOfSpec | None = None
    raw_keep: list[str] = Field(default_factory=list)
    sha256: str = ""

    def known_headers(self) -> set[str]:
        return {alias for spec in self.fields.values() for alias in spec.from_}

    def required_fields(self) -> list[str]:
        return [name for name, spec in self.fields.items() if spec.required]


def mapping_names(paths: Paths | None) -> list[str]:
    """Mapping names of every enabled module (repo defaults plus DATA_DIR overrides)."""
    from sed.modules import mapping_index

    return sorted(mapping_index(paths))


def load_mapping(name: str, paths: Paths | None) -> MappingSpec:
    from sed.modules import mapping_index

    index = mapping_index(paths)
    if name not in index:
        raise ValidationFailed(f"Unknown mapping '{name}'", {"available": sorted(index)})
    data = load_layered(f"{index[name][1]}/{name}.yaml", paths)
    try:
        spec = MappingSpec.model_validate({**data, "sha256": config_sha256(data)})
    except ValidationError as exc:
        errors = [{"loc": ".".join(str(p) for p in e["loc"]), "msg": e["msg"]} for e in exc.errors()]
        raise ValidationFailed(f"Invalid mapping '{name}'", errors) from exc
    if spec.name != name:
        raise ValidationFailed(f"Mapping file {name}.yaml declares name '{spec.name}'")
    return spec


def load_all_mappings(paths: Paths | None) -> dict[str, MappingSpec]:
    return {name: load_mapping(name, paths) for name in mapping_names(paths)}


# ---------------------------------------------------------------------------
# column resolution and matching
# ---------------------------------------------------------------------------


def resolve_columns(spec: MappingSpec, columns: list[str]) -> tuple[dict[str, int], list[str]]:
    """Map canonical field -> column index (first alias that exists). Returns (index, missing_required)."""
    by_norm: dict[str, int] = {}
    for idx, col in enumerate(columns):
        by_norm.setdefault(normalize_header(col), idx)
    index: dict[str, int] = {}
    for field_name, fspec in spec.fields.items():
        for alias in fspec.from_:
            pos = by_norm.get(normalize_header(alias))
            if pos is not None:
                index[field_name] = pos
                break
    missing = [f for f in spec.required_fields() if f not in index]
    return index, missing


def header_match_score(spec: MappingSpec, columns: list[str]) -> float:
    index, missing = resolve_columns(spec, columns)
    required = spec.required_fields()
    if required:
        return (len(required) - len(missing)) / len(required) * 0.7 + len(index) / len(spec.fields) * 0.3
    return len(index) / max(1, len(spec.fields))


def glob_matches(spec: MappingSpec, file_name: str) -> bool:
    lower = file_name.lower()
    return any(fnmatch.fnmatch(lower, g.lower()) for g in spec.match.glob)


def glob_specificity(spec: MappingSpec, file_name: str) -> int:
    lower = file_name.lower()
    return max((len(g.replace("*", "")) for g in spec.match.glob if fnmatch.fnmatch(lower, g.lower())), default=0)


def read_for_mapping(path: Path, spec: MappingSpec) -> RawTable:
    if spec.format == "confluence_html":
        from sed.ingest.confluence import read_confluence_export

        return read_confluence_export(path)
    if path.is_dir():
        raise ValidationFailed(f"{path.name} is a directory; mapping '{spec.name}' expects a file")
    table = read_table(path, spec.reader.options(), spec.known_headers())
    if spec.unpivot:
        table = unpivot(table, spec.unpivot)
    return table


def choose_mapping(
    path: Path, mappings: dict[str, MappingSpec], forced: str | None = None
) -> tuple[MappingSpec, RawTable, float]:
    if forced:
        if forced not in mappings:
            raise ValidationFailed(f"Unknown mapping '{forced}'")
        spec = mappings[forced]
        table = read_for_mapping(path, spec)
        return spec, table, header_match_score(spec, table.columns)
    candidates = [m for m in mappings.values() if glob_matches(m, path.name)]
    if not candidates:
        raise ValidationFailed(f"No mapping matches file name '{path.name}'")
    scored = []
    for spec in candidates:
        table = read_for_mapping(path, spec)
        score = header_match_score(spec, table.columns)
        scored.append((score, glob_specificity(spec, path.name), spec.name, spec, table))
    scored.sort(key=lambda s: (s[0] >= s[3].match.min_score, s[1], s[0]), reverse=True)
    best = scored[0]
    if best[0] < best[3].match.min_score:
        details = [{"mapping": s[2], "score": round(s[0], 2)} for s in scored]
        raise ValidationFailed(f"'{path.name}' headers do not fit any mapping well enough", details)
    return best[3], best[4], best[0]


# ---------------------------------------------------------------------------
# unpivot and as-of
# ---------------------------------------------------------------------------


def unpivot(table: RawTable, spec: UnpivotSpec) -> RawTable:
    pattern = re.compile(spec.columns_regex, re.IGNORECASE)
    value_cols: list[tuple[int, str]] = []
    id_cols: list[int] = []
    for idx, col in enumerate(table.columns):
        m = pattern.match(col)
        if m:
            raw_var = spec.var_template.format(**m.groupdict())
            try:
                period = datetime.strptime(raw_var, spec.var_format).strftime(spec.var_output)
            except ValueError as exc:
                raise ValidationFailed(f"Cannot parse wide column '{col}' as period ({raw_var})") from exc
            value_cols.append((idx, period))
        else:
            id_cols.append(idx)
    if not value_cols:
        raise ValidationFailed(f"No columns match unpivot pattern {spec.columns_regex!r}")
    columns = [table.columns[i] for i in id_cols] + [spec.var_name, spec.value_name]
    rows = []
    for row in table.rows:
        base = [row[i] for i in id_cols]
        for idx, period in value_cols:
            value = row[idx]
            if value in (None, ""):
                continue
            rows.append([*base, period, value])
    warnings = [*table.warnings, f"unpivoted {len(value_cols)} month columns"]
    return RawTable(
        columns, rows, table.source, table.encoding, table.delimiter, table.sheet, table.header_row, warnings
    )


def resolve_as_of(spec: MappingSpec, path: Path, override: date | None) -> str | None:
    if override:
        return override.isoformat()
    if not spec.as_of:
        return None
    if spec.as_of.from_filename:
        m = re.search(spec.as_of.from_filename, path.name)
        if not m:
            raise ValidationFailed(
                f"{path.name}: cannot derive as-of date from file name (pattern {spec.as_of.from_filename!r}); "
                "pass --as-of"
            )
        parsed = datetime.strptime(m.group(1), spec.as_of.format).date()
        if spec.as_of.format in ("%Y-%m", "%Y%m"):
            # Month snapshots are stamped at month end.
            nxt = date(parsed.year + (parsed.month // 12), parsed.month % 12 + 1, 1)
            parsed = date.fromordinal(nxt.toordinal() - 1)
        return parsed.isoformat()
    return None
