"""Every registered report builds every format with --ai none (4 decks + 4 workbooks once ws3-reports lands).

A report whose snapshot builder still raises NotImplementedByWorkstream is skipped; SED_DECKS_REQUIRE_ALL=1 turns
those skips into failures (integration step I2).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from sed.errors import NotImplementedByWorkstream
from sed.modules import reports
from sed.reports.build import build_report
from tests.platform.reports.test_pptx_builder import deck_problems, open_deck, slide_text

REQUIRE_ALL = os.environ.get("SED_DECKS_REQUIRE_ALL") == "1"


def _report_keys() -> list[str]:
    return [rdef.key for _, rdef in reports()]


def test_every_ops_report_is_registered():
    assert set(_report_keys()) >= {"weekly", "monthly", "quarterly", "vendor"}


@pytest.mark.parametrize("report_key", _report_keys())
def test_report_builds_every_format_with_ai_none(ops_profile_rw, report_key):
    from sed.modules import report

    _, rdef = report(report_key)
    period = ops_profile_rw.periods[rdef.period_kinds[0]]  # week 2026-W35, month 2026-08, quarter 2026-Q3
    vendor = ops_profile_rw.ids["vendor_p2"] if rdef.needs_vendor else None
    try:
        result = build_report(ops_profile_rw.paths, report_key, period, None, "none", vendor)
    except NotImplementedByWorkstream as exc:
        if REQUIRE_ALL:
            pytest.fail(f"{report_key}: builder not implemented ({exc.workstream}) but SED_DECKS_REQUIRE_ALL=1")
        pytest.skip(f"{report_key}: snapshot builder not implemented yet ({exc.workstream})")

    assert [a["format"] for a in result["artifacts"]] == list(rdef.formats)
    for artifact in result["artifacts"]:
        path = Path(artifact["path"])
        assert path.is_file() and path.stat().st_size > 0
        assert "_SYNTHETIC" in path.name
        if artifact["format"] == "pptx":
            assert artifact["template_map"] and artifact["template_map"]["name"]
            assert deck_problems(path) == []
            prs = open_deck(path)
            assert prs.slides[-1].shapes.title.text_frame.text.startswith("Provenance")
            assert all(any(sh.name == "sed-synthetic-banner" for sh in s.shapes) for s in prs.slides)
            assert result["snapshot_id"] in slide_text(prs.slides[0])
        elif artifact["format"] == "xlsx":
            from python_calamine import CalamineWorkbook

            sheets = CalamineWorkbook.from_path(str(path)).sheet_names
            assert {"Summary", "Definitions", "Provenance"} <= set(sheets)
        else:
            assert "SYNTHETIC" in path.read_text(encoding="utf-8")
