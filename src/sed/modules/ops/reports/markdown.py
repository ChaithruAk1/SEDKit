"""Paste-ready Markdown summaries (email / Teams) of the monthly, quarterly and vendor reports, from the snapshot only.

Each stays within the spec's `markdown.max_words`; AI-drafted sections are appended by the core (md_builder) in approved
and draft modes.
"""

from __future__ import annotations

from typing import Any

from sed.reports.md_builder import format_fact as _fmt
from sed.reports.snapshot import Snapshot, render_view
from sed.reports.specs import ReportSpec


def _head(snapshot: Snapshot, spec: ReportSpec, ai_mode: str, title: str) -> list[str]:
    lines = []
    if ai_mode == "draft":
        lines.append("> **DRAFT** – may include unapproved AI content.\n")
    if snapshot.data_class == "synthetic":
        lines.append("> **SYNTHETIC DATA** – generated test data, not for distribution.\n")
    lines += [f"**{title} – {snapshot.period}** (as of {snapshot.as_of})", ""]
    return lines


def _cap(lines: list[str], spec: ReportSpec) -> str:
    text = "\n".join(lines).rstrip() + "\n"
    words = text.split(" ")
    if len(text.split()) > spec.markdown.max_words:
        kept, count = [], 0
        for word in words:
            count += len(word.split())
            if count > spec.markdown.max_words:
                break
            kept.append(word)
        text = " ".join(kept).rstrip() + " …\n"
    return text


def _rows(view: Any, key: str) -> list[dict[str, Any]]:
    return list(view.tables.get(key, {}).get("rows") or [])


def _top_risks(view: Any, key: str, limit: int = 3) -> list[str]:
    risks = [r for r in _rows(view, key) if r.get("severity") in {"critical", "high"}]
    return [f"- [{r['severity']}] {r['title']}" for r in risks[:limit]]


def render_monthly(snapshot: Snapshot, spec: ReportSpec, *, ai_mode: str) -> str:
    view = render_view(snapshot, ai_mode)
    F = view.facts
    recurring = _rows(view, "top_recurring")
    lines = _head(snapshot, spec, ai_mode, spec.title)
    lines += [
        f"- **Volume:** {_fmt(F['inc.opened'])} incidents opened (previous month {_fmt(F['inc.opened.prev_month'])}), "
        f"{_fmt(F['inc.resolved'])} resolved; backlog {_fmt(F['inc.backlog'])}.",
        f"- **Service:** SLA {_fmt(F['inc.sla.pct'])} ({_fmt(F['inc.sla.delta_pp_vs_prev_month'])} vs previous "
        f"month); MTTR median {_fmt(F['inc.mttr.median_h'])}; {_fmt(F['inc.p1p2.opened'])} P1/P2 opened.",
        f"- **Changes and delivery:** {_fmt(F['chg.count'])} changes closed, {_fmt(F['chg.success.pct'])} "
        f"successful; {_fmt(F['work.resolved.count'])} work items resolved.",
    ]
    if recurring:
        lines.append(
            "- **Top recurring issues:** "
            + "; ".join(f"{r.get('app')} {r.get('category')} ({r.get('incidents')})" for r in recurring[:3])
            + "."
        )
    upcoming = _rows(view, "upcoming_changes")
    if upcoming:
        lines.append(f"- **Next 30 days:** {len(upcoming)} changes planned.")
    risks = _top_risks(view, "findings")
    if risks:
        lines += ["", "**System-detected risks (top):**", *risks]
    return _cap(lines, spec)


def render_quarterly(snapshot: Snapshot, spec: ReportSpec, *, ai_mode: str) -> str:
    view = render_view(snapshot, ai_mode)
    F = view.facts
    cur = snapshot.base_currency
    lines = _head(snapshot, spec, ai_mode, spec.title)
    lines += [
        f"- **Spend:** {_fmt(F['cost.actual.qtd'], cur)} quarter to date against a budget of "
        f"{_fmt(F['cost.budget.qtd'], cur)} ({_fmt(F['cost.variance.qtd_pct'])}); year to date "
        f"{_fmt(F['cost.actual.ytd'], cur)} against {_fmt(F['cost.budget.ytd'], cur)}.",
        f"- **Licenses:** idle license cost {_fmt(F['license.idle_cost'], cur)} per year.",
        f"- **Renewals:** {_fmt(F['renewals.2q.count'])} contracts end and {_fmt(F['notice.2q.count'])} notice "
        "deadlines fall in the next two quarters.",
        f"- **Portfolio:** {_fmt(F['apps.quiet.count'])} quiet applications with license cost.",
    ]
    over = sorted(
        (r for r in _rows(view, "spend_by_app") if (r.get("variance_pct") or 0) > 0),
        key=lambda r: -(r.get("variance_pct") or 0),
    )
    if over:
        lines.append(
            "- **Largest overruns:** "
            + "; ".join(f"{r.get('key')} ({r['variance_pct']:+.0f}%)" for r in over[:3])
            + "."
        )
    risks = _top_risks(view, "risks")
    if risks:
        lines += ["", "**Top risks:**", *risks]
    return _cap(lines, spec)


def render_vendor(snapshot: Snapshot, spec: ReportSpec, *, ai_mode: str) -> str:
    view = render_view(snapshot, ai_mode)
    F = view.facts
    cur = snapshot.base_currency
    lines = _head(snapshot, spec, ai_mode, f"{spec.title}: {F['vendor.name']['value']}")
    lines += [
        f"- **Commercial:** {_fmt(F['vendor.contracts.count'])} active contracts worth "
        f"{_fmt(F['vendor.contracts.annual_value'], cur)} a year; spend {_fmt(F['vendor.spend.period'], cur)} "
        f"(previous period {_fmt(F['vendor.spend.prev_period'], cur)}); idle license cost "
        f"{_fmt(F['vendor.license.idle_cost'], cur)}.",
        f"- **Service:** SLA {_fmt(F['vendor.sla.pct'])} ({_fmt(F['vendor.sla.delta_pp'])} over three months); "
        f"MTTR median {_fmt(F['vendor.mttr.median_h'])}; {_fmt(F['vendor.reassign.avg'])} reassignments on "
        f"average; {_fmt(F['vendor.incidents.count'])} incidents opened.",
    ]
    notices = [r for r in _rows(view, "renewal_timeline") if r.get("days_to_notice") is not None]
    if notices:
        first = min(notices, key=lambda r: r["days_to_notice"])
        lines.append(
            f"- **Next notice deadline:** {first.get('contract_number')} ({first.get('product')}) on "
            f"{first.get('notice_deadline')}."
        )
    risks = _top_risks(view, "risks")
    if risks:
        lines += ["", "**Risks:**", *risks]
    return _cap(lines, spec)
