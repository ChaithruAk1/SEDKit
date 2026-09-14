"""Template tooling for writing and signing off a template map.

* `inspect_template(path)`: slide size, every layout with its placeholders (idx, type, position and size in inches)
  and a suggested starter map, so a corporate template map can be written without guessing idx values.
* `template_proof(tmap, out_dir, *, data_class)`: a deck with one slide of every kind filled with dummy content, for
  visual sign-off of a map (layout choice, content boxes, fonts, colours, footer and classification label).

The dummy content is fictional and generic; nothing is read from a profile database.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sed.errors import ValidationFailed
from sed.reports.template_map import (
    EMU_PER_INCH,
    POTX_MESSAGE,
    SLIDE_KINDS,
    TEXT_KINDS,
    LoadedTemplateMap,
    all_layouts,
    open_pptx,
    slide_ratio_name,
)

_TITLE_TYPES = {"TITLE", "CENTER_TITLE", "VERTICAL_TITLE"}
_BODY_TYPES = {"BODY", "OBJECT", "VERTICAL_BODY", "VERTICAL_OBJECT"}
_CHROME_TYPES = {"DATE", "FOOTER", "SLIDE_NUMBER"}


def _inches(emu: int | None) -> float | None:
    return None if emu is None else round(emu / EMU_PER_INCH, 3)


def inspect_template(path: Path) -> dict[str, Any]:
    """Describe a .pptx template's layouts and placeholders (exit 2 for .potx or unreadable files)."""
    path = Path(path)
    if path.suffix.lower() == ".potx":
        raise ValidationFailed(POTX_MESSAGE, {"file": str(path)})
    if path.suffix.lower() != ".pptx":
        raise ValidationFailed(f"template-inspect needs a .pptx file (got '{path.name}')")
    prs = open_pptx(path)
    layouts = []
    first_master_count = len(prs.slide_layouts)
    for global_idx, (m_idx, l_idx, layout) in enumerate(all_layouts(prs)):
        placeholders = []
        for ph in layout.placeholders:
            fmt = ph.placeholder_format
            placeholders.append(
                {
                    "idx": fmt.idx,
                    "type": fmt.type.name if fmt.type is not None else None,
                    "name": ph.name,
                    "left_in": _inches(ph.left),
                    "top_in": _inches(ph.top),
                    "width_in": _inches(ph.width),
                    "height_in": _inches(ph.height),
                }
            )
        layouts.append(
            {
                "index": l_idx if m_idx == 0 else None,
                "master": m_idx,
                "position": global_idx,
                "name": layout.name,
                "placeholders": placeholders,
            }
        )
    width_in = prs.slide_width / EMU_PER_INCH
    height_in = prs.slide_height / EMU_PER_INCH
    return {
        "path": str(path.resolve()),
        "slide_size": {
            "width_in": round(width_in, 3),
            "height_in": round(height_in, 3),
            "ratio": slide_ratio_name(prs.slide_width, prs.slide_height),
        },
        "masters": len(prs.slide_masters),
        "slides": len(prs.slides),
        "fallback_index_range": [0, first_master_count - 1],
        "layouts": layouts,
        "suggested_map": suggest_map(layouts, width_in, height_in, prs.slide_width, prs.slide_height),
    }


def _types(layout: dict[str, Any]) -> set[str]:
    return {p["type"] for p in layout["placeholders"] if p["type"]}


def _first(layout: dict[str, Any], types: set[str]) -> dict[str, Any] | None:
    return next((p for p in layout["placeholders"] if p["type"] in types), None)


def suggest_map(
    layouts: list[dict[str, Any]], width_in: float, height_in: float, width_emu: int, height_emu: int
) -> dict[str, Any]:
    """A starter template map (best-guess layouts and content boxes) to edit, save as <name>.map.yaml and proof."""
    usable = [lay for lay in layouts if lay["index"] is not None] or layouts

    def pick(predicate: Any, default: dict[str, Any] | None) -> dict[str, Any] | None:
        return next((lay for lay in usable if predicate(lay)), default)

    with_title = pick(lambda lay: _types(lay) & _TITLE_TYPES, usable[0] if usable else None)
    title = pick(lambda lay: "CENTER_TITLE" in _types(lay) and "SUBTITLE" in _types(lay), with_title)
    section = pick(lambda lay: "section" in lay["name"].lower() and _types(lay) & _TITLE_TYPES, with_title)
    narrative = pick(
        lambda lay: "TITLE" in _types(lay) and len([p for p in lay["placeholders"] if p["type"] in _BODY_TYPES]) == 1,
        with_title,
    )
    title_only = pick(
        lambda lay: _types(lay) & _TITLE_TYPES and not (_types(lay) - _TITLE_TYPES - _CHROME_TYPES), with_title
    )

    def entry(layout: dict[str, Any] | None, roles: dict[str, set[str]], box: bool) -> dict[str, Any]:
        if layout is None:
            return {"layout_name": "", "fallback_index": 0, "placeholders": {}}
        placeholders = {}
        for role, types in roles.items():
            ph = _first(layout, types)
            if ph is not None:
                placeholders[role] = ph["idx"]
        out: dict[str, Any] = {
            "layout_name": layout["name"],
            "fallback_index": layout["index"] or 0,
            "placeholders": placeholders,
        }
        if box:
            ph = _first(layout, _TITLE_TYPES)
            top = (ph["top_in"] + ph["height_in"] + 0.1) if ph and ph["top_in"] is not None else 1.5
            top = round(min(top, height_in - 1.5), 2)
            out["content_box_in"] = [0.5, top, round(width_in - 1.0, 2), round(height_in - top - 0.6, 2)]
        return out

    chosen = {"title": title, "section": section, "narrative": narrative}
    mapping: dict[str, Any] = {}
    for kind in SLIDE_KINDS:
        if kind == "title":
            mapping[kind] = entry(title, {"title": _TITLE_TYPES, "subtitle": {"SUBTITLE", *_BODY_TYPES}}, False)
        elif kind in TEXT_KINDS:
            mapping[kind] = entry(chosen[kind], {"title": _TITLE_TYPES, "body": _BODY_TYPES | {"SUBTITLE"}}, False)
        else:
            mapping[kind] = entry(title_only, {"title": _TITLE_TYPES}, True)
    ratio = slide_ratio_name(width_emu, height_emu)
    return {
        "name": "corporate",
        "template": "corporate.pptx",
        "slide_size": ratio if ratio != "other" else "16:9",
        "fonts": {"title": "Calibri", "body": "Calibri", "body_pt": 14, "table_pt": 10},
        "series_colors": ["#1F4E79", "#2E75B6", "#9DC3E6", "#C55A11", "#7F7F7F"],
        "footer_text": "{report_title} | {period}",
        "classification_label": "Internal",
        "layouts": mapping,
    }


def _dummy_snapshot(data_class: str) -> Any:
    from sed.reports.snapshot import Snapshot, fact, table

    weeks = [f"Week {i}" for i in range(1, 9)]
    return Snapshot(
        snapshot_id="snap-template-proof",
        report_key="weekly",
        period="2026-W01",
        vendor_id=None,
        as_of="2026-01-05",
        data_class=data_class,
        reporting_tz="Europe/Paris",
        sla_source="example",
        facts={
            "example.count": fact(1234, "count", "Example count"),
            "example.count.avg": fact(1180.5, "number", "Example count, average"),
            "example.pct": fact(92.4, "pct", "Example percentage"),
            "example.pp": fact(-1.6, "pp", "Example change"),
            "example.hours": fact(18.25, "hours", "Example duration"),
            "example.eur": fact(45250.0, "eur", "Example amount"),
        },
        tables={
            "trend": table(
                "Example trend",
                [("week", "Week", "text"), ("series_a", "Series A", "count"), ("series_b", "Series B", "count")],
                [{"week": w, "series_a": 40 + 7 * i, "series_b": 35 + 5 * ((i * 3) % 7)} for i, w in enumerate(weeks)],
            ),
            "breakdown": table(
                "Example breakdown",
                [
                    ("group", "Group", "text"),
                    ("low", "Low", "count"),
                    ("medium", "Medium", "count"),
                    ("high", "High", "count"),
                ],
                [
                    {"group": f"Group {g}", "low": 12 - i, "medium": 5 + i, "high": i % 4}
                    for i, g in enumerate("ABCDEF")
                ],
            ),
            "items": table(
                "Example items",
                [
                    ("item", "Item", "text"),
                    ("owner", "Owner", "text"),
                    ("amount", "Amount", "eur"),
                    ("share", "Share", "ratio"),
                    ("due", "Due", "date"),
                    ("note", "Note", "text"),
                ],
                [
                    {
                        "item": f"ITEM-{i:03d}",
                        "owner": f"Team {'ABC'[i % 3]}",
                        "amount": 1000.0 * (i + 1),
                        "share": 0.1 * (i % 10),
                        "due": f"2026-02-{i + 1:02d}",
                        "note": "Example text that wraps across the column to show the cell layout",
                    }
                    for i in range(10)
                ],
            ),
            "risks": table(
                "Example findings",
                [("severity", "Severity", "text"), ("title", "Finding", "text"), ("subject", "Subject", "text")],
                [
                    {
                        "severity": sev,
                        "title": f"Example finding {i + 1} with a descriptive headline",
                        "subject": f"S-{i}",
                    }
                    for i, sev in enumerate(["critical", "high", "medium", "low", "medium"])
                ],
            ),
        },
        freshness=[
            {
                "mapping_name": "example_export",
                "last_import": "2026-01-05T08:00:00Z",
                "files": 1,
                "latest_as_of": "2026-01-05",
            }
        ],
        input_batches=[],
        sha256="0" * 64,
        created_at="2026-01-05T08:00:00Z",
        git_commit=None,
        period_end="2026-01-05",
        data_as_of="2026-01-05",
        suppressed_findings=[
            {
                "stable_key": "example",
                "kind": "renewal_risk",
                "title": "Example acknowledged finding",
                "status": "acknowledged",
                "suppress_until": None,
            }
        ],
    )


def _dummy_spec() -> Any:
    from sed.reports.specs import ChartSpec, KpiSpec, ReportSpec, SlideSpec

    slides = [
        SlideSpec(kind="title", title="Template proof", subtitle="Every slide kind with dummy content"),
        SlideSpec(kind="section", title="Section header", subtitle="Section subtitle text"),
        SlideSpec(
            kind="kpis",
            title="KPI tiles",
            kpis=[
                KpiSpec(fact="example.count", compare="example.count.avg", delta="example.pp"),
                KpiSpec(fact="example.pct", delta="example.pp"),
                KpiSpec(fact="example.hours"),
                KpiSpec(fact="example.eur"),
                KpiSpec(fact="example.pp"),
                KpiSpec(fact="example.count.avg"),
            ],
        ),
        SlideSpec(
            kind="line_chart",
            title="Line chart",
            table="trend",
            chart=ChartSpec(type="line", categories="week", series=["series_a", "series_b"], title="Example trend"),
        ),
        SlideSpec(
            kind="bar_chart",
            title="Bar chart",
            table="trend",
            chart=ChartSpec(type="column", categories="week", series=["series_a"], title="Example values"),
        ),
        SlideSpec(
            kind="stacked_bar",
            title="Stacked bar chart",
            table="breakdown",
            chart=ChartSpec(type="bar", categories="group", series=["low", "medium", "high"], title="Example mix"),
        ),
        SlideSpec(kind="table", title="Table", table="items"),
        SlideSpec(kind="narrative", title="Narrative", ai_section_key="example_narrative"),
        SlideSpec(kind="findings", title="Findings", table="risks"),
        SlideSpec(
            kind="attention_list", title="Attention list", table="items", columns=["item", "owner", "due", "note"]
        ),
        SlideSpec(kind="provenance", title="Provenance"),
    ]
    return ReportSpec(report="weekly", title="Template proof", kpis=[], sheets=[], slides=slides)


def template_proof(tmap: LoadedTemplateMap, out_dir: Path, *, data_class: str) -> dict[str, Any]:
    """Render one slide of every kind with dummy content using the map; returns the file and per-slide layouts."""
    from sed.db import utc_now
    from sed.reports.pptx_builder import render_deck

    snapshot = _dummy_snapshot(data_class)
    spec = _dummy_spec()
    narratives = {
        "example_narrative": [
            "First example bullet with enough words to show wrapping inside the body placeholder",
            "Second example bullet",
            "Third example bullet",
        ]
    }
    suffix = "_SYNTHETIC" if data_class == "synthetic" else ""
    out_path = Path(out_dir) / f"template-proof_{tmap.map.name}{suffix}.pptx"
    manifest = render_deck(
        snapshot, spec, tmap, out_path, ai_mode="approved", generated_at=utc_now(), narratives=narratives
    )
    kinds = sorted({s["kind"] for s in manifest["slides"]})
    return {
        "map": tmap.map.name,
        "map_path": str(tmap.path),
        "template": str(tmap.template_path) if tmap.template_path else None,
        "template_map_sha256": tmap.sha256,
        "path": manifest["path"],
        "kinds": kinds,
        "slides": manifest["slides"],
        "fallbacks": [s["kind"] for s in manifest["slides"] if s["fallback_used"]],
    }
