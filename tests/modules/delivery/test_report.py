"""delivery-status on the delivery profile: every format builds cleanly, facts agree with the portfolio read model, the
planted risks reach the tables, per-project facts exist for the AI sections, and the running month is "to date"."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from sed import db
from sed.modules.delivery.queries import portfolio as P
from sed.reports.build import build_report
from sed.reports.snapshot import Snapshot, create_snapshot
from sed.reports.specs import load_report_spec
from tests.platform.reports.test_pptx_builder import deck_problems, open_deck, slide_text

PERIOD = "2026-08"


def _snapshot(profile: Any, period: str = PERIOD) -> Snapshot:
    conn = db.connect(profile.paths.db)
    try:
        return create_snapshot(conn, profile.paths, "delivery-status", period)
    finally:
        conn.close()


def test_every_format_builds(delivery_profile_rw):
    result = build_report(delivery_profile_rw.paths, "delivery-status", PERIOD, None, "none")
    artifacts = {a["format"]: Path(a["path"]) for a in result["artifacts"]}
    assert list(artifacts) == ["xlsx", "md", "pptx"]
    assert all(p.is_file() and p.name.startswith("delivery-status_2026-08") for p in artifacts.values())

    assert deck_problems(artifacts["pptx"]) == []
    deck = open_deck(artifacts["pptx"])
    deck_text = "\n".join(slide_text(s) for s in deck.slides)
    assert deck.slides[-1].shapes.title.text_frame.text.startswith("Provenance")
    assert "Orion ERP e-invoicing rollout" in deck_text and "R-103-02" in deck_text

    from python_calamine import CalamineWorkbook

    book = CalamineWorkbook.from_path(str(artifacts["xlsx"]))
    assert {"Summary", "Projects", "Jira progress", "Slipped milestones", "RAID", "Risks", "Definitions",
            "Provenance"} <= set(book.sheet_names)  # fmt: skip
    md = artifacts["md"].read_text(encoding="utf-8")
    assert md.startswith("> **SYNTHETIC DATA**") and "**Monthly Delivery Status – 2026-08**" in md
    assert "{{" not in md and len(md.split()) <= 300 + 1


def test_facts_agree_with_the_portfolio(delivery_profile_rw):
    snap = _snapshot(delivery_profile_rw)
    facts = {k: v["value"] for k, v in snap.facts.items()}
    conn = db.connect(delivery_profile_rw.paths.db, readonly=True)
    try:
        items = P.portfolio(conn, delivery_profile_rw.paths, date.fromisoformat(snap.as_of))
    finally:
        conn.close()
    assert snap.as_of == "2026-09-01"  # the end of August (exclusive)
    rags = [i["health"]["rag"] for i in items]
    assert facts["delivery.projects.count"] == len(items) == 4
    assert (facts["delivery.projects.red"], facts["delivery.projects.amber"], facts["delivery.projects.green"]) == (
        rags.count("red"),
        rags.count("amber"),
        rags.count("green"),
    )
    assert facts["delivery.project.PRJ-101.worst_slip_days"] == 35
    assert facts["delivery.project.PRJ-101.rag"] == "red"
    assert facts["delivery.milestones.slipped"] >= 2 and facts["delivery.raid.overdue"] >= 1

    slipped = snap.tables["delivery_milestones_slipped"]["rows"]
    assert slipped[0]["slip_days"] == 35 and slipped[0]["project"] == "Orion ERP e-invoicing rollout"
    raid = {r["raid_id"]: r for r in snap.tables["delivery_raid"]["rows"]}
    assert raid["R-103-02"]["days_overdue"] > 0
    kinds = {r["kind"] for r in snap.tables["delivery_findings"]["rows"]}
    assert kinds == {"delivery_risk"}

    spec = load_report_spec("delivery-status", delivery_profile_rw.paths)
    keys = [s.key for s in spec.sections]
    assert keys == ["headline", "progress", "risks_and_issues", "decisions_needed", "next_steps"]


def test_running_month_is_to_date(delivery_profile_rw):
    snap = _snapshot(delivery_profile_rw, "2026-09")
    assert snap.period_end == "2026-10-01" and snap.data_as_of == "2026-09-01"
    assert snap.facts["delivery.milestones.completed"]["label"].endswith("(to date)")
