"""Paste-ready Markdown summary of the monthly delivery status (email / Teams), from the snapshot only. AI-drafted
sections are appended by the core (md_builder)."""

from __future__ import annotations

from sed.reports.md_builder import format_fact as _fmt
from sed.reports.snapshot import Snapshot, render_view
from sed.reports.specs import ReportSpec


def render(snapshot: Snapshot, spec: ReportSpec, *, ai_mode: str) -> str:
    view = render_view(snapshot, ai_mode)
    F = view.facts
    projects = view.tables["delivery_projects"]["rows"]
    findings = view.tables["delivery_findings"]["rows"]
    lines = []
    if ai_mode == "draft":
        lines.append("> **DRAFT** – may include unapproved AI content.\n")
    if snapshot.data_class == "synthetic":
        lines.append("> **SYNTHETIC DATA** – generated test data, not for distribution.\n")
    lines += [
        f"**{spec.title} – {snapshot.period}** (as of {snapshot.as_of})",
        "",
        f"- **Portfolio:** {_fmt(F['delivery.projects.count'])} projects: {_fmt(F['delivery.projects.red'])} red, "
        f"{_fmt(F['delivery.projects.amber'])} amber, {_fmt(F['delivery.projects.green'])} green (computed); "
        f"{_fmt(F['delivery.projects.rag_mismatch'])} differ from the reported RAG.",
        f"- **Milestones:** {_fmt(F['delivery.milestones.completed'])} completed, "
        f"{_fmt(F['delivery.milestones.slipped'])} slipped past baseline, "
        f"{_fmt(F['delivery.milestones.due_30d'])} due in the next 30 days.",
        f"- **RAID:** {_fmt(F['delivery.raid.open_high'])} open high items, "
        f"{_fmt(F['delivery.raid.overdue'])} overdue.",
        f"- **Progress:** {_fmt(F['delivery.points.done_pct'])} of story points done; velocity "
        f"{_fmt(F['delivery.velocity.points_per_week'])} points per week.",
    ]
    at_risk = [p for p in projects if p["computed_rag"] in ("red", "amber")]
    if at_risk:
        lines += ["", "**Projects needing attention:**"]
        lines += [f"- [{p['computed_rag']}] {p['name']}: {p['reasons'] or 'see plan'}" for p in at_risk[:4]]
    serious = [f for f in findings if f.get("severity") in {"critical", "high"}]
    if serious:
        lines += ["", "**System-detected delivery risks (top):**"]
        lines += [f"- [{f['severity']}] {f['title']}" for f in serious[:3]]
    lines += ["", f"_AI content: {ai_mode}. Snapshot {snapshot.snapshot_id}._"]
    text = "\n".join(lines)
    words = text.split()
    if len(words) > spec.markdown.max_words:
        text = " ".join(words[: spec.markdown.max_words]) + " …"
    return text + "\n"
