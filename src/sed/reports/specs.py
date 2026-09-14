"""Report specifications (config/<module>/reports/<report>.yaml via ReportDef.spec, layered like all config)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from sed.errors import ValidationFailed
from sed.paths import Paths
from sed.settings import load_layered


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class KpiSpec(_Strict):
    fact: str
    compare: str | None = None
    delta: str | None = None


class ChartSpec(_Strict):
    type: Literal["column", "line", "bar"] = "column"
    categories: str
    series: list[str]
    title: str = ""


class ConditionalSpec(_Strict):
    column: str
    rule: Literal["<", "<=", ">", ">=", "=="]
    value: float
    style: Literal["bad", "warn", "good"] = "bad"


class SheetSpec(_Strict):
    table: str
    sheet: str = Field(max_length=31)
    chart: ChartSpec | None = None
    conditional: list[ConditionalSpec] = Field(default_factory=list)


class MarkdownSpec(_Strict):
    max_words: int = 250


SlideKind = Literal[
    "title",
    "section",
    "kpis",
    "line_chart",
    "bar_chart",
    "stacked_bar",
    "table",
    "narrative",
    "findings",
    "attention_list",
    "provenance",
]
_NEEDS_TABLE = {"line_chart", "bar_chart", "stacked_bar", "table", "findings", "attention_list"}
_NEEDS_CHART = {"line_chart", "bar_chart", "stacked_bar"}


class SlideSpec(_Strict):
    kind: SlideKind
    title: str = ""
    subtitle: str = ""
    kpis: list[KpiSpec] | None = None
    table: str | None = None
    columns: list[str] | None = None
    chart: ChartSpec | None = None
    max_rows: int | None = Field(None, ge=1)
    ai_section_key: str | None = None
    when: Literal["always", "has_rows"] = "always"
    notes: str = ""

    @model_validator(mode="after")
    def _required_fields(self) -> SlideSpec:
        if self.kind == "kpis" and not self.kpis:
            raise ValueError("kpis slides need a non-empty 'kpis' list")
        if self.kind in _NEEDS_TABLE and not self.table:
            raise ValueError(f"{self.kind} slides need 'table'")
        if self.kind in _NEEDS_CHART and not self.chart:
            raise ValueError(f"{self.kind} slides need 'chart'")
        if self.kind == "narrative" and not self.ai_section_key:
            raise ValueError("narrative slides need 'ai_section_key'")
        if self.when == "has_rows" and not self.table:
            raise ValueError("when: has_rows needs 'table'")
        return self


class ReportSpec(_Strict):
    report: str
    title: str
    audience: str = ""
    kpis: list[KpiSpec]
    sheets: list[SheetSpec]
    slides: list[SlideSpec] = Field(default_factory=list)
    markdown: MarkdownSpec = MarkdownSpec()


def load_report_spec(report_key: str, paths: Paths | None) -> ReportSpec:
    from sed.modules import report

    _, rdef = report(report_key)
    data: dict[str, Any] = load_layered(rdef.spec, paths)
    try:
        return ReportSpec.model_validate(data)
    except ValidationError as exc:
        errors = [{"loc": ".".join(str(p) for p in e["loc"]), "msg": e["msg"]} for e in exc.errors()]
        raise ValidationFailed(f"Invalid report spec '{report_key}'", errors) from exc
