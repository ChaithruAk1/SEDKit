"""AI findings lifecycle: drafts at ingest, carry-forward at finish-run, per-finding review and stale inputs.

* **Drafts.** A finding skill's ingest writes one `finding` row per (run, stable_key), status `draft`, idempotently.
* **Carry-forward** (finish-run). A draft whose stable_key has an approved (or update_pending) finding from an earlier
  run and that did not change materially (same severity, ticket_count within ±25%, the same suspected change, no new
  evidence fact keys) does not return to the review queue: the new row keeps the approved `body_md` (still what is
  published, with numbers refreshed through tokens) and holds the fresh wording in `pending_body_md` with status
  `update_pending`; identical wording is approved directly. The earlier row becomes `superseded`. A material change
  leaves the draft for full review. `report_section` findings are never carried forward.
* **Review.** One decision per finding, appended to `review_decision`: approve, reject (note), edit (keeps
  `original_body_md` in the payload), approve_update, and for rule findings acknowledge (note) and suppress_until.
  Approving an AI finding supersedes the other approved or update_pending rows with the same stable_key.
* **Stale inputs.** A finding run records the label runs its packet used (`ai_run.input_run_ids_json`). Rejecting one
  of those runs marks the dependent findings `stale_input`, so they leave the published views until a re-run.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from typing import Any

from sed import db
from sed.ai.hashing import sha256_text
from sed.errors import PreconditionFailed, ValidationFailed

NEVER_CARRY = ("report_section",)
MATERIAL_TICKET_CHANGE = 0.25
AI_OPEN = ("draft", "update_pending", "stale_input")
ACTIONS = ("approve", "reject", "edit", "approve_update", "acknowledge", "suppress_until")


def finding_id_for(run_id: str, stable_key: str) -> str:
    return "f-" + sha256_text(f"{run_id}|{stable_key}")[:20]


def upsert_draft(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    stable_key: str,
    kind: str,
    title: str,
    body_md: str | None,
    severity: str | None,
    confidence: float | None,
    payload: dict[str, Any],
    subject_type: str | None = None,
    subject_id: str | None = None,
    period: str | None = None,
) -> str:
    """Insert or refresh the draft finding of (run, stable_key) inside the caller's write_tx; returns its id."""
    finding_id = finding_id_for(run_id, stable_key)
    conn.execute(
        "INSERT INTO finding (finding_id, run_id, origin, stable_key, kind, subject_type, subject_id, period, title, "
        "body_md, severity, confidence, payload_json, status, created_at) "
        "VALUES (?, ?, 'ai', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?) "
        "ON CONFLICT (finding_id) DO UPDATE SET kind = excluded.kind, subject_type = excluded.subject_type, "
        "subject_id = excluded.subject_id, period = excluded.period, title = excluded.title, "
        "body_md = excluded.body_md, severity = excluded.severity, confidence = excluded.confidence, "
        "payload_json = excluded.payload_json, status = 'draft', pending_body_md = NULL, carried_forward_from = NULL",
        (
            finding_id,
            run_id,
            stable_key,
            kind,
            subject_type,
            subject_id,
            period,
            title,
            body_md,
            severity,
            confidence,
            json.dumps(payload, sort_keys=True, ensure_ascii=False),
            db.utc_now(),
        ),
    )
    return finding_id


def _payload(row: sqlite3.Row) -> dict[str, Any]:
    try:
        data = json.loads(row["payload_json"] or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _fact_keys(payload: dict[str, Any]) -> set[str]:
    items = list(payload.get("evidence") or []) + list(payload.get("signals") or [])
    return {str(e.get("fact_key")) for e in items if isinstance(e, dict) and e.get("fact_key")}


def material_change(prior: sqlite3.Row, new: sqlite3.Row) -> list[str]:
    """Reasons the new finding differs materially from the prior one (empty = carry forward)."""
    reasons: list[str] = []
    if prior["severity"] != new["severity"]:
        reasons.append(f"severity {prior['severity']} -> {new['severity']}")
    old, fresh = _payload(prior), _payload(new)
    before, after = old.get("ticket_count"), fresh.get("ticket_count")
    numeric = isinstance(before, int | float) and isinstance(after, int | float) and before > 0
    if numeric and abs(after - before) / before > MATERIAL_TICKET_CHANGE:
        reasons.append(f"ticket_count {before} -> {after}")
    if fresh.get("suspected_change") and fresh.get("suspected_change") != old.get("suspected_change"):
        reasons.append(f"suspected change {old.get('suspected_change')} -> {fresh.get('suspected_change')}")
    new_keys = _fact_keys(fresh) - _fact_keys(old)
    if new_keys:
        reasons.append("new evidence " + ", ".join(sorted(new_keys)))
    return reasons


def carry_forward(conn: sqlite3.Connection, run_id: str) -> dict[str, int]:
    """Apply carry-forward to the run's drafts (inside the caller's write_tx)."""
    counts = {"carried_forward": 0, "unchanged_approved": 0, "material_change": 0, "new": 0}
    drafts = conn.execute(
        "SELECT * FROM finding WHERE run_id = ? AND origin = 'ai' AND status = 'draft' ORDER BY stable_key", (run_id,)
    ).fetchall()
    for new in drafts:
        if new["kind"] in NEVER_CARRY:
            counts["new"] += 1
            continue
        prior = conn.execute(
            "SELECT * FROM finding WHERE stable_key = ? AND origin = 'ai' AND run_id IS NOT ? "
            "AND status IN ('approved', 'update_pending') ORDER BY created_at DESC, finding_id DESC LIMIT 1",
            (new["stable_key"], run_id),
        ).fetchone()
        if prior is None:
            counts["new"] += 1
            continue
        reasons = material_change(prior, new)
        payload = _payload(new)
        if reasons:
            payload["material_change"] = {"from_finding": prior["finding_id"], "reasons": reasons}
            conn.execute(
                "UPDATE finding SET payload_json = ? WHERE finding_id = ?",
                (json.dumps(payload, sort_keys=True, ensure_ascii=False), new["finding_id"]),
            )
            counts["material_change"] += 1
            continue
        published = prior["body_md"]
        same = (new["body_md"] or "").strip() == (published or "").strip()
        conn.execute(
            "UPDATE finding SET status = ?, body_md = ?, pending_body_md = ?, carried_forward_from = ?, "
            "reviewed_by = ?, reviewed_at = ? WHERE finding_id = ?",
            (
                "approved" if same else "update_pending",
                published,
                None if same else new["body_md"],
                prior["finding_id"],
                prior["reviewed_by"],
                prior["reviewed_at"],
                new["finding_id"],
            ),
        )
        conn.execute("UPDATE finding SET status = 'superseded' WHERE finding_id = ?", (prior["finding_id"],))
        counts["unchanged_approved" if same else "carried_forward"] += 1
    return counts


def mark_stale_dependents(conn: sqlite3.Connection, rejected_run_id: str) -> int:
    """Findings of runs that used `rejected_run_id` as input become stale_input (inside the caller's write_tx)."""
    dependents = [
        r["run_id"]
        for r in conn.execute("SELECT run_id, input_run_ids_json FROM ai_run WHERE input_run_ids_json <> '[]'")
        if rejected_run_id in _run_ids(r["input_run_ids_json"])
    ]
    changed = 0
    for run_id in dependents:
        changed += conn.execute(
            "UPDATE finding SET status = 'stale_input' WHERE run_id = ? AND origin = 'ai' "
            "AND status IN ('draft', 'approved', 'update_pending')",
            (run_id,),
        ).rowcount
    return changed


def _run_ids(text: str | None) -> list[str]:
    try:
        data = json.loads(text or "[]")
    except ValueError:
        return []
    return [str(x) for x in data] if isinstance(data, list) else []


def _decide(
    conn: sqlite3.Connection, finding_id: str, decision: str, reviewer: str, payload: dict[str, Any], now: str
) -> None:
    conn.execute(
        "INSERT INTO review_decision (target_type, target_id, decision, payload_json, reviewer, decided_at) "
        "VALUES ('finding', ?, ?, ?, ?, ?)",
        (finding_id, decision, json.dumps(payload, sort_keys=True, ensure_ascii=False), reviewer, now),
    )


def _supersede_others(conn: sqlite3.Connection, row: sqlite3.Row) -> None:
    conn.execute(
        "UPDATE finding SET status = 'superseded' WHERE stable_key = ? AND origin = 'ai' AND finding_id <> ? "
        "AND status IN ('approved', 'update_pending')",
        (row["stable_key"], row["finding_id"]),
    )


def review_finding(
    conn: sqlite3.Connection,
    finding_id: str,
    action: str,
    reviewer: str,
    *,
    note: str | None = None,
    body_md: str | None = None,
    until: date | None = None,
) -> dict[str, Any]:
    """Apply one review decision (inside the caller's write_tx). Exit 4 when the finding's status does not allow it."""
    if action not in ACTIONS:
        raise ValidationFailed(f"Unknown review action '{action}'", {"actions": list(ACTIONS)})
    row = conn.execute("SELECT * FROM finding WHERE finding_id = ?", (finding_id,)).fetchone()
    if row is None:
        raise PreconditionFailed(f"Unknown finding '{finding_id}'")
    origin, status = row["origin"], row["status"]
    now = db.utc_now()
    note = note.strip() if note and note.strip() else None

    def refuse(expected: str) -> PreconditionFailed:
        return PreconditionFailed(f"Cannot {action} finding {finding_id}: it is a {origin} finding in status {status}; "
                                  f"{action} needs {expected}")  # fmt: skip

    if action in ("acknowledge", "suppress_until"):
        if origin != "rule" or status not in ("active", "acknowledged"):
            raise refuse("an active rule finding")
        if not note:
            raise ValidationFailed(f"{action} needs a --note explaining why")
        if action == "acknowledge":
            conn.execute(
                "UPDATE finding SET status = 'acknowledged', reviewed_by = ?, reviewed_at = ?, review_note = ? "
                "WHERE finding_id = ?",
                (reviewer, now, note, finding_id),
            )
            payload: dict[str, Any] = {"note": note}
        else:
            if until is None:
                raise ValidationFailed("suppress_until needs --until YYYY-MM-DD")
            conn.execute(
                "UPDATE finding SET status = 'active', suppress_until = ?, reviewed_by = ?, reviewed_at = ?, "
                "review_note = ? WHERE finding_id = ?",
                (until.isoformat(), reviewer, now, note, finding_id),
            )
            payload = {"note": note, "until": until.isoformat()}
        _decide(conn, finding_id, action, reviewer, payload, now)
        return {
            "finding_id": finding_id,
            "action": action,
            "status": "acknowledged" if action == "acknowledge" else "active",
        }

    if origin != "ai":
        raise refuse("an AI finding (rule findings take acknowledge or suppress_until)")
    new_status = status
    payload = {"note": note} if note else {}
    if action == "approve":
        if status not in ("draft", "stale_input"):
            raise refuse("status draft or stale_input")
        new_status = "approved"
        conn.execute(
            "UPDATE finding SET status = 'approved', reviewed_by = ?, reviewed_at = ?, review_note = ? "
            "WHERE finding_id = ?",
            (reviewer, now, note, finding_id),
        )
        _supersede_others(conn, row)
    elif action == "approve_update":
        if status != "update_pending":
            raise refuse("status update_pending")
        new_status = "approved"
        conn.execute(
            "UPDATE finding SET status = 'approved', body_md = COALESCE(pending_body_md, body_md), "
            "pending_body_md = NULL, reviewed_by = ?, reviewed_at = ?, review_note = ? WHERE finding_id = ?",
            (reviewer, now, note, finding_id),
        )
        _supersede_others(conn, row)
    elif action == "edit":
        if status not in ("draft", "update_pending", "approved", "stale_input"):
            raise refuse("status draft, update_pending, approved or stale_input")
        if not body_md or not body_md.strip():
            raise ValidationFailed("edit needs the new body text")
        data = _payload(row)
        data.setdefault("original_body_md", row["pending_body_md"] or row["body_md"])
        new_status = "approved"
        conn.execute(
            "UPDATE finding SET status = 'approved', body_md = ?, pending_body_md = NULL, edited = 1, "
            "payload_json = ?, reviewed_by = ?, reviewed_at = ?, review_note = ? WHERE finding_id = ?",
            (body_md.strip(), json.dumps(data, sort_keys=True, ensure_ascii=False), reviewer, now, note, finding_id),
        )
        _supersede_others(conn, row)
        payload["body_md"] = body_md.strip()
    else:  # reject
        if status not in ("draft", "update_pending", "approved", "stale_input"):
            raise refuse("status draft, update_pending, approved or stale_input")
        if not note:
            raise ValidationFailed("reject needs a --note explaining why")
        new_status = "rejected"
        conn.execute(
            "UPDATE finding SET status = 'rejected', reviewed_by = ?, reviewed_at = ?, review_note = ? "
            "WHERE finding_id = ?",
            (reviewer, now, note, finding_id),
        )
    _decide(conn, finding_id, action, reviewer, payload, now)
    return {"finding_id": finding_id, "action": action, "status": new_status}


def queue(
    conn: sqlite3.Connection, *, kind: str | None = None, include_rule: bool = True, limit: int = 200
) -> list[dict]:
    """Findings waiting for a person: AI drafts, update_pending and stale_input, plus active rule findings that are not
    suppressed (same rule as v_findings_published: suppressed until a date before today)."""
    where = ["(origin = 'ai' AND status IN ('draft', 'update_pending', 'stale_input'))"]
    if include_rule:
        where.append(
            "(origin = 'rule' AND status = 'active' "
            "AND (suppress_until IS NULL OR suppress_until < strftime('%Y-%m-%d', 'now')))"
        )
    sql = f"SELECT * FROM finding WHERE ({' OR '.join(where)})"
    params: list[Any] = []
    if kind:
        sql += " AND kind = ?"
        params.append(kind)
    sql += (
        " ORDER BY CASE status WHEN 'draft' THEN 0 WHEN 'stale_input' THEN 1 WHEN 'update_pending' THEN 2 ELSE 3 END, "
        "CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, kind, title LIMIT ?"
    )
    params.append(limit)
    out = []
    for r in conn.execute(sql, params):
        out.append(
            {
                "finding_id": r["finding_id"],
                "origin": r["origin"],
                "run_id": r["run_id"],
                "kind": r["kind"],
                "status": r["status"],
                "severity": r["severity"],
                "confidence": r["confidence"],
                "title": r["title"],
                "subject_type": r["subject_type"],
                "subject_id": r["subject_id"],
                "body_md": r["body_md"],
                "pending_body_md": r["pending_body_md"],
                "carried_forward_from": r["carried_forward_from"],
                "payload": _payload(r),
            }
        )
    return out
