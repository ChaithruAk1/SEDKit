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


class SectionSpec(_Strict):
    """One AI-drafted section (a `report_section` finding): what it must say and how long it may be."""

    key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,39}$")
    title: str = Field(min_length=1, max_length=80)
    guide: str = Field(min_length=1, max_length=1200)
    max_words: int = Field(120, ge=20, le=600)
    # Written last, from the other drafted sections (headline, executive summary).
    summary: bool = False
    # --require-complete needs an approved version of every required section.
    required: bool = True


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
    sections: list[SectionSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _section_keys(self) -> ReportSpec:
        keys = [s.key for s in self.sections]
        if len(keys) != len(set(keys)):
            raise ValueError("section keys must be unique")
        # Specs without sections (template proofs, tests) keep free narrative keys; those slides never render.
        unknown = sorted({s.ai_section_key for s in self.slides if s.kind == "narrative"} - set(keys) - {None})
        if keys and unknown:
            raise ValueError(f"narrative slides use undeclared sections: {', '.join(unknown)}")
        return self


def load_report_spec(report_key: str, paths: Paths | None) -> ReportSpec:
    from sed.modules import report

    _, rdef = report(report_key)
    data: dict[str, Any] = load_layered(rdef.spec, paths)
    try:
        return ReportSpec.model_validate(data)
    except ValidationError as exc:
        errors = [{"loc": ".".join(str(p) for p in e["loc"]), "msg": e["msg"]} for e in exc.errors()]
        raise ValidationFailed(f"Invalid report spec '{report_key}'", errors) from exc
