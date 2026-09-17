"""Markdown summaries of the monthly, quarterly and vendor reports: built from the snapshot, stamped SYNTHETIC (and
DRAFT in draft mode), within the spec's word limit."""

from __future__ import annotations

from pathlib import Path

import pytest

from sed.reports.build import build_report
from sed.reports.specs import load_report_spec


@pytest.mark.parametrize(
    ("report", "period", "needle"),
    [
        ("monthly", "2026-08", "- **Service:** SLA"),
        ("quarterly", "2026-Q3", "- **Spend:**"),
        ("vendor", "2026-Q3", "- **Commercial:**"),
    ],
)
def test_markdown_summary(ops_profile_rw, report, period, needle):
    paths = ops_profile_rw.paths
    vendor = ops_profile_rw.ids["vendor_p2"] if report == "vendor" else None
    result = build_report(paths, report, period, ["md"], "none", vendor)
    text = Path(result["artifacts"][0]["path"]).read_text(encoding="utf-8")
    spec = load_report_spec(report, paths)
    assert "**SYNTHETIC DATA**" in text and f"– {period}**" in text and needle in text
    assert "n/a n/a" not in text and "{{" not in text
    assert len(text.split()) <= spec.markdown.max_words + 1

    draft = build_report(paths, report, period, ["md"], "draft", vendor)
    assert Path(draft["artifacts"][0]["path"]).read_text(encoding="utf-8").startswith("> **DRAFT**")
