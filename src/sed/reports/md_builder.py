"""Paste-ready Markdown summary (email / Teams) rendered from the snapshot only (no AI in --ai none mode)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sed.reports.snapshot import Snapshot, render_view
from sed.reports.specs import ReportSpec


def _fmt(f: dict[str, Any] | None) -> str:
    if not f or f["value"] is None:
        return "n/a"
    v, unit = f["value"], f["unit"]
    if unit == "pct":
        return f"{v:.1f}%"
    if unit == "pp":
        return f"{v:+.1f} pp"
    if unit == "hours":
        return f"{v:.1f} h"
    if unit == "eur":
        return f"€{v:,.0f}"
    if unit == "number":
        return f"{v:,.1f}"
    if unit == "count":
        return f"{int(v):,}"
    return str(v)


def _signed_pct(f: dict[str, Any] | None) -> str:
    if not f or f["value"] is None:
        return ""
    return f" ({f['value']:+.0f}% vs 4-week avg)"


def render_weekly_md(snapshot: Snapshot, spec: ReportSpec, *, ai_mode: str) -> str:
    view = render_view(snapshot, ai_mode)
    F = view.facts
    findings = view.tables["findings"]["rows"]
    renewals = view.tables["renewals_90d"]["rows"]
    critical = [f for f in findings if f.get("severity") in {"critical", "high"}]
    lines = []
    if snapshot.data_class == "synthetic":
        lines.append("> **SYNTHETIC DATA** – generated test data, not for distribution.\n")
    lines += [
        f"**{spec.title} – {snapshot.period}** (as of {snapshot.as_of})",
        "",
        f"- **Volume:** {_fmt(F['inc.opened'])} opened{_signed_pct(F['inc.opened.delta_vs_avg4w_pct'])}, "
        f"{_fmt(F['inc.resolved'])} resolved; backlog {_fmt(F['inc.backlog'])} ({F['inc.backlog.delta']['value']:+d} "
        "net this week, "
        f"{_fmt(F['inc.backlog.stale_excluded'])} stale tickets excluded).",
        f"- **Service:** SLA {_fmt(F['inc.sla.pct'])} ({_fmt(F['inc.sla.delta_pp_vs_4w'])} vs 4-week avg, source "
        f"{F['inc.sla.source']['value']}); MTTR median {_fmt(F['inc.mttr.median_h'])}; {_fmt(F['inc.p1p2.opened'])} "
        "P1/P2 opened.",
        f"- **Attention:** {_fmt(F['attention.count'])} open incidents need attention; "
        f"{_fmt(F['inc.stale_open'])} open in the store but missing from the latest open-incident export (likely "
        "resolved).",
        f"- **Changes:** {_fmt(F['chg.count'])} closed, {_fmt(F['chg.success.pct'])} successful.",
        f"- **Commercial:** {_fmt(F['renewals.90d.count'])} contracts end within 90 days, "
        f"{_fmt(F['notice.30d.count'])} notice deadlines within 30 days; idle license cost "
        f"{_fmt(F['license.idle_cost'])}/yr.",
    ]
    if critical:
        lines.append("")
        lines.append("**System-detected risks (top):**")
        for f in critical[:3]:
            lines.append(f"- [{f['severity']}] {f['title']}")
    soon = [r for r in renewals if r.get("days_to_notice") is not None and r["days_to_notice"] <= 30]
    if soon:
        lines.append("")
        lines.append(
            "**Notice deadlines within 30 days:** "
            + "; ".join(f"{r['vendor']} ({r['notice_deadline']})" for r in soon[:4])
        )
    lines.append("")
    lines.append(f"_AI content: {ai_mode}. Snapshot {snapshot.snapshot_id}._")
    text = "\n".join(lines)
    words = text.split()
    if len(words) > spec.markdown.max_words:
        text = " ".join(words[: spec.markdown.max_words]) + " …"
    return text + "\n"


def build_md(snapshot: Snapshot, spec: ReportSpec, out_path: Path, *, ai_mode: str) -> Path:
    """Render with the report's declared Markdown renderer (ReportDef.markdown)."""
    from sed.errors import ValidationFailed
    from sed.modules import load_ref, report

    _, rdef = report(snapshot.report_key)
    if not rdef.markdown:
        raise ValidationFailed(f"The {snapshot.report_key} report has no Markdown format")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(load_ref(rdef.markdown)(snapshot, spec, ai_mode=ai_mode), encoding="utf-8")
    return out_path
