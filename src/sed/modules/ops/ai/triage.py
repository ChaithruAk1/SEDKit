"""Run handler for the `sed-triage-batch` skill (implements sed.ai.contract.SkillHandler).

Work items are (ticket, stage) pairs of incidents and problems:
* stage `open`: open, not stale, with an open_hash; event_at = opened_at.
* stage `resolved`: resolved_at and resolved_hash set; event_at = resolved_at.
An item is skipped when a label for its current hash exists in a running, completed or approved run, or when another
run holds a live claim on it. Items are ordered by event_at DESC, ticket_id ASC. Packets carry scrubbed columns only
and never the ticket id or number (refs map back through ai_batch_item).
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from datetime import UTC, datetime
from typing import Any, ClassVar

from sed.ai.contract import IngestError, IngestResult, RunContext, SampleCandidate, WorkItem
from sed.ai.runs import lease_until, scope_bounds
from sed.calendar import iso_utc
from sed.errors import Busy
from sed.modules.ops.ai.schemas import TriageBatchOutput
from sed.modules.ops.ai.taxonomy import load_taxonomy, render_context, slugify_symptom
from sed.modules.ops.ai.vocab import approved_keys, render_vocab

LIMITS = {"short": 160, "desc": 500, "close": 300}
RATIONALE_MAX_WORDS = 25


def _cut(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    if not text:
        return None
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _now_iso() -> str:
    return iso_utc(datetime.now(UTC))


class TriageBatchHandler:
    skill: ClassVar[str] = "sed-triage-batch"
    schema_version: ClassVar[int] = 1
    output_model: ClassVar[type[TriageBatchOutput]] = TriageBatchOutput

    # -- configuration ---------------------------------------------------------------------------------------------

    def config_inputs(self, paths: Any) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "taxonomy": load_taxonomy(paths).as_config(),
            "payload_limits": dict(LIMITS),
        }

    # -- start-run -------------------------------------------------------------------------------------------------

    def select(self, ctx: RunContext) -> list[WorkItem]:
        bounds = scope_bounds(ctx.params.scope, ctx.settings, ctx.data_as_of)
        end_open = "AND t.opened_at < :end" if bounds.end_iso else ""
        end_resolved = "AND t.resolved_at < :end" if bounds.end_iso else ""
        sql = f"""
            WITH cand AS (
                SELECT t.ticket_id, 'open' AS stage, t.open_hash AS input_hash, t.opened_at AS event_at
                FROM ticket t
                WHERE t.kind IN ('incident', 'problem') AND t.is_open = 1 AND t.stale_open = 0
                  AND t.open_hash IS NOT NULL AND t.opened_at >= :start {end_open}
                UNION ALL
                SELECT t.ticket_id, 'resolved', t.resolved_hash, t.resolved_at
                FROM ticket t
                WHERE t.kind IN ('incident', 'problem') AND t.resolved_at IS NOT NULL
                  AND t.resolved_hash IS NOT NULL AND t.resolved_at >= :start {end_resolved}
            )
            SELECT c.ticket_id, c.stage, c.input_hash, c.event_at, t.kind, t.priority, t.category,
                   t.assignment_group, t.short_description, t.description, t.close_code, t.close_notes,
                   a.name AS app
            FROM cand c
            JOIN ticket t ON t.ticket_id = c.ticket_id
            LEFT JOIN application a ON a.app_id = t.app_id
            WHERE NOT EXISTS (
                    SELECT 1 FROM ai_ticket_label l JOIN ai_run r ON r.run_id = l.run_id
                    WHERE l.ticket_id = c.ticket_id AND l.stage = c.stage AND l.input_hash = c.input_hash
                      AND r.status IN ('running', 'completed', 'approved'))
              AND NOT EXISTS (
                    SELECT 1 FROM ai_claim k
                    WHERE k.ticket_id = c.ticket_id AND k.stage = c.stage AND k.lease_expires_at > :now
                      AND k.run_id IS NOT :run_id)
            ORDER BY c.event_at DESC, c.ticket_id ASC
        """
        rows = ctx.conn.execute(
            sql, {"start": bounds.start_iso, "end": bounds.end_iso, "now": _now_iso(), "run_id": ctx.run_id}
        ).fetchall()
        return [WorkItem(r["ticket_id"], r["stage"], r["input_hash"], self._payload(r)) for r in rows]

    @staticmethod
    def _payload(r: sqlite3.Row) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "stage": r["stage"],
            "app": r["app"],
            "kind": r["kind"],
            "prio": r["priority"],
            "sn_cat": r["category"],
            "group": r["assignment_group"],
            "short": _cut(r["short_description"], LIMITS["short"]),
            "desc": _cut(r["description"], LIMITS["desc"]),
        }
        if r["stage"] == "resolved":
            payload["close_code"] = r["close_code"]
            payload["close"] = _cut(r["close_notes"], LIMITS["close"])
        return {k: v for k, v in payload.items() if v is not None and v != ""}

    def claim(self, ctx: RunContext, items: list[WorkItem]) -> None:
        now = _now_iso()
        until = lease_until(ctx.settings.ai.claim_lease_hours)
        lost = []
        for item in items:
            cur = ctx.conn.execute(
                "INSERT INTO ai_claim (ticket_id, stage, run_id, lease_expires_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (ticket_id, stage) DO UPDATE SET run_id = excluded.run_id, "
                "lease_expires_at = excluded.lease_expires_at "
                "WHERE ai_claim.run_id = excluded.run_id OR ai_claim.lease_expires_at <= ?",
                (item.item_id, item.stage, ctx.run_id, until, now),
            )
            if cur.rowcount == 0:
                lost.append(f"{item.item_id}|{item.stage}")
        if lost and not ctx.params.resume:
            raise Busy(f"{len(lost)} items were claimed by another run meanwhile; retry start-run", lost[:20])

    def context_files(self, ctx: RunContext, items: list[WorkItem]) -> dict[str, str]:
        return {"context.md": render_context(load_taxonomy(ctx.paths), run_id=ctx.run_id)}

    def batch_files(self, ctx: RunContext, batch: str, items: list[WorkItem]) -> dict[str, str]:
        ids = [i.item_id for i in items]
        app_ids: list[str] = []
        for start in range(0, len(ids), 500):
            chunk = ids[start : start + 500]
            marks = ", ".join("?" for _ in chunk)
            app_ids += [
                r[0]
                for r in ctx.conn.execute(
                    f"SELECT DISTINCT app_id FROM ticket WHERE ticket_id IN ({marks}) AND app_id IS NOT NULL", chunk
                )
            ]
        return {f"vocab_{batch}.txt": render_vocab(batch, approved_keys(ctx.conn, app_ids))}

    # -- ingest ----------------------------------------------------------------------------------------------------

    def validate(self, ctx: RunContext, output: Any, refs: dict[str, WorkItem]) -> list[IngestError]:
        taxonomy = load_taxonomy(ctx.paths)
        errors: list[IngestError] = []
        for idx, item in enumerate(output.items):
            loc = f"items.{idx}"
            category = taxonomy.categories.get(item.am_category)
            if category is None:
                errors.append(
                    IngestError(
                        f"{loc}.am_category",
                        f"'{item.am_category}' is not a taxonomy category ({', '.join(taxonomy.categories)})",
                        item.ref,
                    )
                )
            elif item.am_subcategory is not None and item.am_subcategory not in category.subcategories:
                allowed = ", ".join(category.subcategories) or "none: use null"
                errors.append(
                    IngestError(
                        f"{loc}.am_subcategory",
                        f"'{item.am_subcategory}' is not a subcategory of '{item.am_category}' ({allowed})",
                        item.ref,
                    )
                )
            if item.misfiled_as not in taxonomy.misfiled_as:
                errors.append(
                    IngestError(
                        f"{loc}.misfiled_as",
                        f"'{item.misfiled_as}' is not allowed ({', '.join(taxonomy.misfiled_as)})",
                        item.ref,
                    )
                )
            if not slugify_symptom(item.symptom_key):
                errors.append(IngestError(f"{loc}.symptom_key", "symptom_key has no letters or digits", item.ref))
        return errors

    def write(self, ctx: RunContext, batch_id: str, output: Any, refs: dict[str, WorkItem]) -> IngestResult:
        threshold = ctx.settings.ai.low_confidence_threshold
        warnings: list[str] = []
        created = _now_iso()
        rows = []
        for item in output.items:
            work = refs[item.ref]
            key = slugify_symptom(item.symptom_key)
            if key != item.symptom_key:
                warnings.append(f"{item.ref}: symptom_key normalised to '{key}'")
            if len(item.rationale.split()) > RATIONALE_MAX_WORDS:
                warnings.append(f"{item.ref}: rationale is longer than {RATIONALE_MAX_WORDS} words")
            rows.append(
                (
                    work.item_id,
                    work.stage,
                    ctx.run_id,
                    batch_id,
                    work.input_hash,
                    item.am_category,
                    item.am_subcategory,
                    key,
                    item.misfiled_as,
                    float(item.confidence),
                    item.rationale,
                    created,
                )
            )
        ctx.conn.executemany(
            "INSERT INTO ai_ticket_label (ticket_id, stage, run_id, batch_id, input_hash, am_category, am_subcategory, "
            "symptom_key, misfiled_as, confidence, rationale, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (ticket_id, stage, run_id) DO UPDATE SET batch_id = excluded.batch_id, "
            "input_hash = excluded.input_hash, am_category = excluded.am_category, "
            "am_subcategory = excluded.am_subcategory, symptom_key = excluded.symptom_key, "
            "misfiled_as = excluded.misfiled_as, confidence = excluded.confidence, rationale = excluded.rationale",
            rows,
        )
        low = sum(1 for item in output.items if item.confidence < threshold)
        return IngestResult(items=len(rows), low_confidence=low, warnings=warnings)

    def release(self, ctx: RunContext) -> int:
        return ctx.conn.execute("DELETE FROM ai_claim WHERE run_id = ?", (ctx.run_id,)).rowcount

    # -- review ----------------------------------------------------------------------------------------------------

    def sample_candidates(self, conn: sqlite3.Connection, run_id: str) -> list[SampleCandidate]:
        rows = conn.execute(
            "SELECT ticket_id, stage, am_category, confidence FROM ai_ticket_label WHERE run_id = ? "
            "ORDER BY ticket_id, stage",
            (run_id,),
        ).fetchall()
        return [SampleCandidate(r["ticket_id"], r["stage"], r["am_category"], r["confidence"]) for r in rows]

    def review_card(self, conn: sqlite3.Connection, run_id: str, keys: list[tuple[str, str]]) -> dict[str, Any]:
        cards: dict[str, Any] = {}
        for item_id, stage in keys:
            r = conn.execute(
                "SELECT l.am_category, l.am_subcategory, l.symptom_key, l.misfiled_as, l.confidence, l.rationale, "
                "t.number, t.kind, t.priority, t.category, t.short_description, a.name AS app "
                "FROM ai_ticket_label l JOIN ticket t ON t.ticket_id = l.ticket_id "
                "LEFT JOIN application a ON a.app_id = t.app_id "
                "WHERE l.run_id = ? AND l.ticket_id = ? AND l.stage = ?",
                (run_id, item_id, stage),
            ).fetchone()
            if r is None:
                continue
            cards[f"{item_id}|{stage}"] = {
                "label": {
                    "am_category": r["am_category"],
                    "am_subcategory": r["am_subcategory"],
                    "symptom_key": r["symptom_key"],
                    "misfiled_as": r["misfiled_as"],
                    "confidence": r["confidence"],
                    "rationale": r["rationale"],
                },
                "ticket": {
                    "number": r["number"],
                    "kind": r["kind"],
                    "priority": r["priority"],
                    "app": r["app"],
                    "sn_category": r["category"],
                    "short_description": r["short_description"],
                },
            }
        matrix = [
            dict(r)
            for r in conn.execute(
                "SELECT COALESCE(t.category, '(none)') AS sn_category, l.am_category, COUNT(*) AS n "
                "FROM ai_ticket_label l JOIN ticket t ON t.ticket_id = l.ticket_id WHERE l.run_id = ? "
                "GROUP BY 1, 2 ORDER BY n DESC, 1, 2",
                (run_id,),
            )
        ]
        misfiled = Counter(
            r[0]
            for r in conn.execute(
                "SELECT misfiled_as FROM ai_ticket_label WHERE run_id = ? AND misfiled_as IS NOT NULL "
                "AND misfiled_as <> 'none'",
                (run_id,),
            )
        )
        return {"cards": cards, "matrix": matrix, "misfiled": dict(sorted(misfiled.items()))}
