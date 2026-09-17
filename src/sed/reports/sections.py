"""AI-drafted report sections: identity, rendering into builds, and readiness.

A section is a `finding` row of kind `report_section` (drafted by the `sed-draft-report` skill, reviewed like any AI
finding). Its stable key names the report, period, vendor and section key, so a newer approved draft supersedes the
previous one and sections are never carried forward. Numbers in `body_md` appear only as `{{f:<fact_key>}}` tokens;
the payload keeps the value of every cited fact at drafting time (`facts`) and the findings the text relies on
(`cited_finding_ids`).

At build time a section renders only when every token resolves in the fresh snapshot to the value it had when drafted
(otherwise it is `stale`), and, in approved mode, when every cited finding is published (otherwise `blocked`). The
report spec lists the sections (`sections:`); narrative slides, the Markdown summary and the workbook show them.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from sed.reports.snapshot import AiRunProvenance, Snapshot
from sed.reports.specs import ReportSpec

KIND = "report_section"
TOKEN_RE = re.compile(r"\{\{f:([A-Za-z0-9_.:\-]{1,120})\}\}")
PUBLISHED = ("approved", "update_pending")
# Digits a section may show outside tokens: dates, period labels, priorities and counting phrases.
BARE_ALLOWED = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b\d{4}-\d{2}-\d{2}\b",
        r"\b\d{4}-(W\d{2}|Q[1-4]|\d{2})\b",
        r"\bW\d{1,2}\b",
        r"\bQ[1-4]\b",
        r"\bP[1-5](\s*[-/–]\s*P?[1-5])?\b",
        r"\btop\s+\d+\b",
        r"\b\d+\s+(days?|weeks?|months?|quarters?)\b",
        r"\bFY\d{2,4}\b",
        r"\b(S/4|ECC6?)\b",
    )
]


def subject_id(report_key: str, period: str, vendor_id: str | None) -> str:
    return f"{report_key}:{period}" + (f":{vendor_id}" if vendor_id else "")


def stable_key(report_key: str, period: str, vendor_id: str | None, section_key: str) -> str:
    return f"{KIND}:{subject_id(report_key, period, vendor_id)}:{section_key}"


def bare_numbers(text: str | None) -> bool:
    """True when the text shows a digit outside `{{f:...}}` tokens and the allowed labels."""
    rest = TOKEN_RE.sub("", text or "")
    for pattern in BARE_ALLOWED:
        rest = pattern.sub("", rest)
    return bool(re.search(r"\d", rest))


def tokens(text: str | None) -> list[str]:
    return list(dict.fromkeys(TOKEN_RE.findall(text or "")))


def same_value(a: Any, b: Any) -> bool:
    """Fact values compared as stored JSON (a float stored as 12.0 equals 12)."""
    numbers = [isinstance(v, int | float) and not isinstance(v, bool) for v in (a, b)]
    if all(numbers):
        return abs(float(a) - float(b)) < 1e-9
    return json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)


@dataclass
class SectionState:
    key: str
    title: str
    required: bool
    status: str = "missing"  # missing | draft | approved | stale | blocked
    finding_id: str | None = None
    run_id: str | None = None
    finding_status: str | None = None
    reviewed_by: str | None = None
    headline: str | None = None
    body_md: str | None = None  # tokens filled with formatted values
    paragraphs: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    has_newer_draft: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "required": self.required,
            "status": self.status,
            "finding_id": self.finding_id,
            "run_id": self.run_id,
            "finding_status": self.finding_status,
            "reviewed_by": self.reviewed_by,
            "headline": self.headline,
            "body_md": self.body_md,
            "paragraphs": list(self.paragraphs),
            "reasons": list(self.reasons),
            "has_newer_draft": self.has_newer_draft,
        }


def _payload(row: sqlite3.Row) -> dict[str, Any]:
    try:
        data = json.loads(row["payload_json"] or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _candidates(conn: sqlite3.Connection, key: str) -> list[sqlite3.Row]:
    """Section findings of one stable key, newest run first (manual edits keep their run)."""
    return conn.execute(
        "SELECT f.*, r.run_seq FROM finding f LEFT JOIN ai_run r ON r.run_id = f.run_id "
        "WHERE f.kind = ? AND f.stable_key = ? AND f.origin = 'ai' "
        "AND f.status IN ('draft', 'approved', 'update_pending', 'stale_input') "
        "ORDER BY COALESCE(r.run_seq, 0) DESC, f.created_at DESC",
        (KIND, key),
    ).fetchall()


def fill_tokens(text: str, facts: dict[str, dict[str, Any]], currency: str) -> str:
    from sed.reports.pptx_builder import format_value

    def one(match: re.Match[str]) -> str:
        fact = facts.get(match.group(1))
        if not fact:
            return "n/a"
        return format_value(fact.get("value"), fact.get("unit", "text"), empty="n/a", currency=currency)

    return TOKEN_RE.sub(one, text)


def paragraphs(markdown: str) -> list[str]:
    """Plain-text paragraphs and bullets for slides: blank lines split paragraphs, list items become their own line,
    emphasis markers and headings are dropped."""
    out: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            out.append(" ".join(current))
            current.clear()

    for raw in markdown.splitlines():
        line = raw.strip()
        if not line:
            flush()
            continue
        line = re.sub(r"^#{1,6}\s+", "", line)
        bullet = re.match(r"^([-*+]|\d+[.)])\s+(.*)$", line)
        if bullet:
            flush()
            out.append(_plain(bullet.group(2)))
        else:
            current.append(_plain(line))
    flush()
    return [p for p in out if p]


def _plain(text: str) -> str:
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    return re.sub(r"(\*\*|__|`)", "", text).strip()


def check_section(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    facts: dict[str, dict[str, Any]] | None,
    *,
    require_published_citations: bool,
) -> list[str]:
    """Why a section finding cannot render against `facts` (None skips the fact check)."""
    payload = _payload(row)
    drafted = payload.get("facts") if isinstance(payload.get("facts"), dict) else {}
    reasons: list[str] = []
    if facts is not None:
        for token in tokens(row["body_md"]):
            if token not in facts:
                reasons.append(f"fact {token} is not in this snapshot")
            elif token not in drafted:
                reasons.append(f"fact {token} was not cited when the section was drafted")
            elif not same_value(facts[token].get("value"), drafted[token]):
                reasons.append(f"fact {token} changed since drafting")
    if require_published_citations:
        cited = [str(x) for x in payload.get("cited_finding_ids") or []]
        if cited:
            marks = ", ".join("?" for _ in cited)
            rows = {
                r["finding_id"]: r
                for r in conn.execute(
                    f"SELECT finding_id, origin, status FROM finding WHERE finding_id IN ({marks})", cited
                ).fetchall()
            }
            for fid in cited:
                r = rows.get(fid)
                if r is None:
                    reasons.append(f"cited finding {fid} no longer exists")
                elif not (r["origin"] == "rule" or r["status"] in PUBLISHED):
                    reasons.append(f"cited finding {fid} is {r['status']}")
    return reasons


def load_sections(
    conn: sqlite3.Connection,
    spec: ReportSpec,
    report_key: str,
    period: str,
    vendor_id: str | None,
    *,
    ai_mode: str,
    facts: dict[str, dict[str, Any]] | None,
    currency: str = "EUR",
) -> list[SectionState]:
    """The state of every spec section for a build (`facts` = the build snapshot) or for readiness (`facts` = None
    compares with the latest stored snapshot of the period, see `readiness`)."""
    states = []
    for section in spec.sections:
        state = SectionState(section.key, section.title, section.required)
        rows = _candidates(conn, stable_key(report_key, period, vendor_id, section.key))
        published = next((r for r in rows if r["status"] in PUBLISHED), None)
        draft = next((r for r in rows if r["status"] in ("draft", "stale_input")), None)
        state.has_newer_draft = bool(
            draft is not None and (published is None or (draft["run_seq"] or 0) > (published["run_seq"] or 0))
        )
        chosen = published
        if ai_mode == "draft" and state.has_newer_draft:
            chosen = draft
        if chosen is None:
            if draft is not None:
                state.status = "draft"
                state.finding_id, state.run_id, state.finding_status = draft["finding_id"], draft["run_id"], "draft"
                state.reasons.append("not approved yet")
            states.append(state)
            continue
        state.finding_id, state.run_id = chosen["finding_id"], chosen["run_id"]
        state.finding_status, state.reviewed_by = chosen["status"], chosen["reviewed_by"]
        payload = _payload(chosen)
        state.headline = payload.get("slide_headline")
        reasons = check_section(conn, chosen, facts, require_published_citations=ai_mode != "draft")
        if reasons:
            state.status = "stale" if any(r.startswith("fact ") for r in reasons) else "blocked"
            state.reasons = reasons
        else:
            state.status = "approved" if chosen["status"] in PUBLISHED else "draft"
            if facts is not None:
                state.body_md = fill_tokens(chosen["body_md"] or "", facts, currency)
                state.paragraphs = paragraphs(state.body_md)
        states.append(state)
    return states


def renderable(states: list[SectionState], ai_mode: str) -> list[SectionState]:
    """Sections a build shows: approved ones, plus drafts in draft mode; nothing in none mode."""
    if ai_mode == "none":
        return []
    allowed = {"approved", "draft"} if ai_mode == "draft" else {"approved"}
    return [s for s in states if s.status in allowed and s.body_md is not None]


def incomplete(states: list[SectionState], ai_mode: str) -> list[str]:
    """Required sections a --require-complete build would miss, as 'key: reason'."""
    shown = {s.key for s in renderable(states, ai_mode)}
    out = []
    for s in states:
        if s.required and s.key not in shown:
            reason = "; ".join(s.reasons) if s.reasons else ("no draft" if s.status == "missing" else s.status)
            out.append(f"{s.key}: {reason}")
    return out


def section_runs(conn: sqlite3.Connection, shown: list[SectionState]) -> list[AiRunProvenance]:
    from sed.ai.provenance import run_provenance

    return run_provenance(conn, {s.run_id for s in shown if s.run_id}, used_for=("report_sections",))


def attach(snapshot: Snapshot, conn: sqlite3.Connection, spec: ReportSpec, ai_mode: str) -> list[SectionState]:
    """Load the sections for a build of `snapshot` and attach the renderable ones (and their runs) to it."""
    states = load_sections(
        conn,
        spec,
        snapshot.report_key,
        snapshot.period,
        snapshot.vendor_id,
        ai_mode=ai_mode,
        facts=snapshot.facts,
        currency=snapshot.base_currency,
    )
    shown = renderable(states, ai_mode)
    snapshot.sections = [s.as_dict() for s in shown]
    if shown:
        known = {r["run_id"] for r in snapshot.ai_runs}
        snapshot.ai_runs = list(snapshot.ai_runs) + [r for r in section_runs(conn, shown) if r["run_id"] not in known]
    return states


def readiness(
    conn: sqlite3.Connection, paths: Any, report_key: str, period: str, vendor_id: str | None
) -> dict[str, Any]:
    """Read-only readiness of a report period: section states against the latest stored snapshot of the period (a
    build takes a fresh snapshot and checks again), plus the cited findings that still wait for review."""
    from sed.reports.specs import load_report_spec

    spec = load_report_spec(report_key, paths)
    row = conn.execute(
        "SELECT snapshot_id, facts_json, created_at FROM report_snapshot WHERE report_key = ? AND period = ? "
        "AND COALESCE(vendor_id, '') = ? ORDER BY created_at DESC LIMIT 1",
        (report_key, period, vendor_id or ""),
    ).fetchone()
    facts = json.loads(row["facts_json"]) if row else None
    approved = load_sections(conn, spec, report_key, period, vendor_id, ai_mode="approved", facts=facts)
    drafts = load_sections(conn, spec, report_key, period, vendor_id, ai_mode="draft", facts=facts)
    waiting: set[str] = set()
    for s in drafts:
        if not s.finding_id:
            continue
        finding = conn.execute("SELECT payload_json FROM finding WHERE finding_id = ?", (s.finding_id,)).fetchone()
        cited = [str(x) for x in (_payload(finding).get("cited_finding_ids") or [])] if finding else []
        for fid in cited:
            r = conn.execute("SELECT origin, status FROM finding WHERE finding_id = ?", (fid,)).fetchone()
            if r is not None and r["origin"] == "ai" and r["status"] in ("draft", "stale_input"):
                waiting.add(fid)
    required = [s for s in approved if s.required]
    return {
        "report": report_key,
        "period": period,
        "vendor_id": vendor_id,
        "snapshot_id": row["snapshot_id"] if row else None,
        "sections_required": len(required),
        "sections_approved": sum(1 for s in required if s.status == "approved"),
        "sections_with_drafts": sum(1 for s in drafts if s.status == "draft" or s.has_newer_draft),
        "cited_findings_unapproved": sorted(waiting),
        "complete": row is not None and not incomplete(approved, "approved"),
        "sections": [
            {
                "key": a.key,
                "title": a.title,
                "required": a.required,
                "approved_status": a.status,
                "draft_status": d.status,
                "has_newer_draft": d.has_newer_draft,
                "finding_id": d.finding_id or a.finding_id,
                "reasons": a.reasons or d.reasons,
            }
            for a, d in zip(approved, drafts, strict=True)
        ],
    }
