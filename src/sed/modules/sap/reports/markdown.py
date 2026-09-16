"""Paste-ready Markdown summary of the weekly SAP operations review (email / Teams), from the snapshot only."""

from __future__ import annotations

from sed.reports.md_builder import format_fact as _fmt
from sed.reports.snapshot import Snapshot, render_view
from sed.reports.specs import ReportSpec


def render(snapshot: Snapshot, spec: ReportSpec, *, ai_mode: str) -> str:
    view = render_view(snapshot, ai_mode)
    F = view.facts
    findings = view.tables["sap_findings"]["rows"]
    areas = sorted((r for r in view.tables["sap_areas"]["rows"] if r["open"]), key=lambda r: -r["open"])
    lines = []
    if ai_mode == "draft":
        lines.append("> **DRAFT** – may include unapproved AI content.\n")
    if snapshot.data_class == "synthetic":
        lines.append("> **SYNTHETIC DATA** – generated test data, not for distribution.\n")
    delta = F["sap.l3.opened.delta_vs_avg4w_pct"]["value"]
    lines += [
        f"**{spec.title} – {snapshot.period}** (as of {snapshot.as_of})",
        "",
        f"- **Volume:** {_fmt(F['sap.l3.opened'])} SAP incidents opened"
        + (f" ({delta:+.0f}% vs 4-week avg)" if delta is not None else "")
        + f", {_fmt(F['sap.l3.resolved'])} resolved; backlog {_fmt(F['sap.l3.backlog'])} "
        f"({F['sap.l3.backlog.delta']['value']:+d} this week), "
        f"{_fmt(F['sap.l3.aged_30d'])} open for more than 30 days.",
        f"- **Service:** SLA {_fmt(F['sap.l3.sla.pct'])} ({_fmt(F['sap.l3.sla.delta_pp_vs_4w'])} vs 4-week avg); "
        f"MTTR median {_fmt(F['sap.l3.mttr.median_h'])}; {_fmt(F['sap.l3.p1p2.opened'])} P1/P2 opened; "
        f"{_fmt(F['sap.l3.attention.count'])} tickets need attention.",
    ]
    if areas:
        lines.append(
            "- **Largest backlogs:** "
            + "; ".join(f"{r['label']} {r['open']} ({r['aged_30d']} > 30d)" for r in areas[:3])
        )
    ratio = F["sap.changes.urgent_ratio_8w"]["value"]
    lines.append(
        f"- **Changes:** {_fmt(F['sap.changes.open'])} open, {_fmt(F['sap.changes.prod_imports'])} production imports"
        + (f", urgent share {ratio:.0f}% over 8 weeks" if ratio is not None else "")
        + f"; {_fmt(F['sap.transports.failed_4w'])} failed imports in 28 days, "
        f"{_fmt(F['sap.transports.waiting'])} transports waiting for production, {_fmt(F['sap.changes.stuck'])} stuck "
        f"changes."
    )
    serious = [f for f in findings if f.get("severity") in {"critical", "high"}]
    if serious:
        lines += ["", "**System-detected SAP risks (top):**"]
        lines += [f"- [{f['severity']}] {f['title']}" for f in serious[:3]]
    lines += ["", f"_{F['sap.scope.note']['value']}. AI content: {ai_mode}. Snapshot {snapshot.snapshot_id}._"]
    text = "\n".join(lines)
    words = text.split()
    if len(words) > spec.markdown.max_words:
        text = " ".join(words[: spec.markdown.max_words]) + " …"
    return text + "\n"
