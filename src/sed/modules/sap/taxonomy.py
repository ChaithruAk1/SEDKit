"""SAP subcategories for AI triage (config/sap/taxonomy.yaml), under the portfolio categories of the ops taxonomy.

Loaded by the SAP triage extension (triage.py) and checked by doctor. Real subcategory names and guides, when they
differ, live only in DATA_DIR\\config\\sap\\taxonomy.yaml.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, ValidationError, model_validator

from sed.errors import ValidationFailed
from sed.paths import Paths
from sed.settings import StrictModel, load_layered

TAXONOMY_FILE = "sap/taxonomy.yaml"
CODE_PREFIX = "sap_"


class SubcategoryDef(StrictModel):
    code: str = Field(pattern=r"^sap_[a-z0-9_]{1,36}$")
    category: str = Field(min_length=1, max_length=40)
    description: str = Field(min_length=1, max_length=300)


class SapTaxonomy(StrictModel):
    version: Literal[1] = 1
    subcategories: list[SubcategoryDef] = Field(default_factory=list)
    guide: str = Field("", max_length=4000)

    @model_validator(mode="after")
    def _unique(self) -> SapTaxonomy:
        codes = [s.code for s in self.subcategories]
        duplicates = sorted({c for c in codes if codes.count(c) > 1})
        if duplicates:
            raise ValueError(f"subcategories listed twice: {', '.join(duplicates)}")
        return self

    def labels(self) -> dict[str, str]:
        """Subcategory code -> short label for tables, e.g. sap_idoc_error -> "IDoc error"."""
        return {s.code: label(s.code) for s in self.subcategories}


def label(code: str | None) -> str:
    if not code:
        return "No subcategory"
    text = code.removeprefix(CODE_PREFIX).replace("_", " ")
    for word, fixed in (("idoc", "IDoc"), ("basis", "Basis")):
        text = text.replace(word, fixed)
    return text[:1].upper() + text[1:]


def load_sap_taxonomy(paths: Paths | None) -> SapTaxonomy:
    data = load_layered(TAXONOMY_FILE, paths)
    try:
        return SapTaxonomy.model_validate(data)
    except ValidationError as exc:
        errors = [{"loc": ".".join(str(p) for p in e["loc"]), "msg": e["msg"]} for e in exc.errors()]
        raise ValidationFailed(f"Invalid {TAXONOMY_FILE}", errors) from exc
