"""Weekly deck, workbook and Markdown on the deterministic ops profile (--ai none), checked against the spec."""

from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path
from typing import Any

from sed.reports.build import build_report
from sed.reports.pptx_builder import CHART_KINDS, EXCLUDED_TEXT, SYNTHETIC_BANNER, TABLE_KINDS
from sed.reports.specs import load_report_spec
from sed.reports.template_map import load_template_map
from tests.platform.reports.test_pptx_builder import deck_problems, open_deck, slide_text


def stored_snapshot(paths: Any, snapshot_id: str) -> dict[str, Any]:
    conn = sqlite3.connect(str(paths.db))
    try:
        row = conn.execute(
            "SELECT facts_json, tables_json, provenance_json FROM report_snapshot WHERE snapshot_id = ?", (snapshot_id,)
        ).fetchone()
    finally:
        conn.close()
    return {"facts": json.loads(row[0]), "tables": json.loads(row[1]), "provenance": json.loads(row[2])}


def expected_content_slides(spec: Any, snapshot: dict[str, Any], tmap: Any, ai_mode: str) -> int:
    """Slides before the provenance pages: one per SlideSpec plus pagination, computed independently of the builder."""
    excluded_tables = set(snapshot["provenance"]["ai_derived_tables"]) if ai_mode == "none" else set()
    excluded_facts = set(snapshot["provenance"]["ai_derived_facts"]) if ai_mode == "none" else set()
    count = 0
    for s in spec.slides:
        per_page = s.max_rows or tmap.map.layouts[s.kind].max_rows
        if s.kind in {"provenance", "narrative"}:
            continue
        if s.kind == "kpis":
            visible = [k for k in s.kpis if k.fact not in excluded_facts]
            count += max(1, math.ceil(len(visible) / per_page))
            continue
        if s.kind in TABLE_KINDS or s.kind in CHART_KINDS:
            n = len(snapshot["tables"][s.table]["rows"])
            if s.when == "has_rows" and n == 0:
                continue
            if s.table in excluded_tables or s.kind in CHART_KINDS:
                count += 1
                continue
            count += max(1, math.ceil(n / per_page))
            continue
        count += 1
    return count


def test_weekly_w35_builds_pptx_xlsx_md_with_ai_none(ops_profile_rw):
    paths = ops_profile_rw.paths
    result = build_report(paths, "weekly", ops_profile_rw.periods["week"], ["pptx", "xlsx", "md"], "none")
    names = {a["format"]: Path(a["path"]).name for a in result["artifacts"]}
    assert names == {
        "pptx": "weekly_2026-W35_SYNTHETIC.pptx",
        "xlsx": "weekly_2026-W35_SYNTHETIC.xlsx",
        "md": "weekly_2026-W35_SYNTHETIC.md",
    }
    assert result["ai_runs"] == [] and result["data_class"] == "synthetic"
    deck = next(Path(a["path"]) for a in result["artifacts"] if a["format"] == "pptx")
    assert deck_problems(deck) == []

    spec = load_report_spec("weekly", paths)
    tmap = load_template_map(None, paths)
    snapshot = stored_snapshot(paths, result["snapshot_id"])
    prs = open_deck(deck)
    titles = [s.shapes.title.text_frame.text for s in prs.slides]
    provenance_pages = [t for t in titles if t.startswith("Provenance")]
    assert provenance_pages and titles[-len(provenance_pages) :] == provenance_pages
    content = expected_content_slides(spec, snapshot, tmap, "none")
    assert len(prs.slides) == content + len(provenance_pages)
    assert len(provenance_pages) <= math.ceil(60 / tmap.map.layouts["provenance"].max_rows)

    title_text = slide_text(prs.slides[0], notes=False)
    assert "SYNTHETIC" in title_text and "Weekly Application Operations Review" in title_text
    assert all(any(sh.name == "sed-synthetic-banner" for sh in s.shapes) for s in prs.slides)
    assert all(result["snapshot_id"] in s.notes_slide.notes_text_frame.text for s in prs.slides)
    charts = [sh for s in prs.slides for sh in s.shapes if getattr(sh, "has_chart", False) and sh.has_chart]
    assert len(charts) == sum(1 for s in spec.slides if s.kind in CHART_KINDS)

    everything = "\n".join(slide_text(s) for s in prs.slides)
    assert EXCLUDED_TEXT in everything  # category_breakdown is AI-derived
    provenance = "\n".join(
        slide_text(s, notes=False) for s in prs.slides if s.shapes.title.text.startswith("Provenance")
    )
    assert "Suppressed/acknowledged rule findings" in provenance and "AI content excluded (--ai none)" in provenance
    assert result["snapshot_sha256"] in provenance and "servicenow_incident" in provenance
    assert SYNTHETIC_BANNER in everything

    conn = sqlite3.connect(str(paths.db))
    try:
        rows = conn.execute("SELECT format, template_map_sha, ai_run_ids_json FROM report_artifact").fetchall()
    finally:
        conn.close()
    shas = {fmt: sha for fmt, sha, _ in rows}
    assert shas["pptx"] == tmap.sha256 and shas["xlsx"] is None and shas["md"] is None
    assert {r[2] for r in rows} == {"[]"}


def test_weekly_default_formats_and_draft_suffix(ops_profile_rw):
    paths = ops_profile_rw.paths
    result = build_report(paths, "weekly", "2026-W35", None, "draft")
    assert [a["format"] for a in result["artifacts"]] == ["xlsx", "md", "pptx"]
    deck = next(Path(a["path"]) for a in result["artifacts"] if a["format"] == "pptx")
    assert deck.name == "weekly_2026-W35_SYNTHETIC_DRAFT.pptx"
    prs = open_deck(deck)
    assert all(any(sh.name == "sed-draft-stamp" for sh in s.shapes) for s in prs.slides)
    assert EXCLUDED_TEXT not in "\n".join(slide_text(s, notes=False) for s in prs.slides)
    assert deck_problems(deck) == []
