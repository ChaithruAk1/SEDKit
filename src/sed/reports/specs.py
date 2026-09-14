"""Report specifications (config/reports/<report>.yaml, layered like all config)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

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


class ReportSpec(_Strict):
    report: str
    title: str
    audience: str = ""
    kpis: list[KpiSpec]
    sheets: list[SheetSpec]
    markdown: MarkdownSpec = MarkdownSpec()


def load_report_spec(report_key: str, paths: Paths | None) -> ReportSpec:
    data: dict[str, Any] = load_layered(f"reports/{report_key}.yaml", paths)
    try:
        return ReportSpec.model_validate(data)
    except ValidationError as exc:
        errors = [{"loc": ".".join(str(p) for p in e["loc"]), "msg": e["msg"]} for e in exc.errors()]
        raise ValidationFailed(f"Invalid report spec '{report_key}'", errors) from exc
