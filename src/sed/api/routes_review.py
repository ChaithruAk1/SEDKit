"""Review routes mounted at /api: the review queue, finding decisions, run detail with its review sample, sample
verdicts, run approval and label corrections.

GET routes read through `deps.read_conn`. POST routes need the per-launch token (security middleware) and write only
through `sed.ai.review` / `sed.ai.findings`, which use db.write_tx (busy -> 409). Every decision is recorded with the
OS user as reviewer, exactly like the `sed review ...` commands.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from sed.api.deps import read_conn, reviewer
from sed.api.models import (
    BulkReviewIn,
    CategoryOption,
    Correction,
    Evidence,
    FindingReviewIn,
    FindingReviewOut,
    LabelCorrectionIn,
    LabelCorrectionOut,
    ReviewItem,
    ReviewQueueOut,
    ReviewRatesOut,
    ReviewResult,
    RunDetailOut,
    RunReviewIn,
    RunReviewOut,
    RunRow,
    SampleCard,
    SampleLabel,
    SampleTicket,
    VerdictsIn,
    VerdictsOut,
)
from sed.errors import PreconditionFailed, ValidationFailed

router = APIRouter(tags=["core"])


def _reviewer(request: Request) -> str:
    return reviewer(request)


def _evidence(payload: dict[str, Any]) -> list[Evidence]:
    items = payload.get("evidence") or payload.get("signals") or []
    return [
        Evidence(fact_key=str(e["fact_key"]), value=e.get("value", e.get("value_at_run")))
        for e in items
        if isinstance(e, dict) and e.get("fact_key")
    ]


@router.get("/review/queue", response_model=ReviewQueueOut)
def review_queue(
    kind: str | None = Query(None, max_length=40),
    include_rule: bool = True,
    limit: int = Query(200, ge=1, le=1000),
    conn: sqlite3.Connection = Depends(read_conn),
) -> ReviewQueueOut:
    # Findings waiting for a person: AI drafts, stale inputs, wording updates, then active rule findings.
    from sed.ai.findings import queue

    items = []
    counts: dict[str, int] = {}
    for f in queue(conn, kind=kind, include_rule=include_rule, limit=limit):
        payload = f["payload"]
        key = f["status"] if f["origin"] == "ai" else "rule_active"
        counts[key] = counts.get(key, 0) + 1
        change = payload.get("material_change") or {}
        items.append(
            ReviewItem(
                finding_id=f["finding_id"],
                origin=f["origin"],
                run_id=f["run_id"],
                kind=f["kind"],
                status=f["status"],
                severity=f["severity"],
                confidence=f["confidence"],
                title=f["title"],
                subject_type=f["subject_type"],
                subject_id=f["subject_id"],
                body_md=f["body_md"],
                pending_body_md=f["pending_body_md"],
                carried_forward_from=f["carried_forward_from"],
                evidence=_evidence(payload),
                ticket_count=payload.get("ticket_count"),
                periodicity=payload.get("periodicity"),
                suspected_change=payload.get("suspected_change"),
                recommendation=payload.get("recommendation"),
                decision_due=payload.get("decision_due"),
                material_change=[str(r) for r in change.get("reasons", [])],
            )
        )
    return ReviewQueueOut(items=items, counts=counts)


@router.post("/findings/{finding_id}/review", response_model=FindingReviewOut)
def review_one(finding_id: str, body: FindingReviewIn, request: Request) -> FindingReviewOut:
    # One decision on one finding (the `sed review approve|reject|edit|...` commands).
    from sed.ai.review import review_findings

    result = review_findings(
        request.app.state.paths,
        [finding_id],
        body.action,
        _reviewer(request),
        note=body.note,
        body_md=body.body_md,
        until=body.until,
    )
    return FindingReviewOut(
        action=result["action"],
        reviewed_by=result["reviewed_by"],
        results=[ReviewResult(**r) for r in result["results"]],
    )


@router.post("/findings/bulk-review", response_model=FindingReviewOut)
def review_many(body: BulkReviewIn, request: Request) -> FindingReviewOut:
    # One action on several findings in one transaction (all or nothing), e.g. approving wording updates.
    from sed.ai.review import review_findings

    result = review_findings(request.app.state.paths, body.finding_ids, body.action, _reviewer(request), note=body.note)
    return FindingReviewOut(
        action=result["action"],
        reviewed_by=result["reviewed_by"],
        results=[ReviewResult(**r) for r in result["results"]],
    )


def _card(card: dict[str, Any]) -> SampleCard:
    verdict = card.get("verdict")
    correction = None
    if isinstance(verdict, dict):
        correction = Correction(category=verdict.get("category"), subcategory=verdict.get("subcategory"))
        verdict = verdict.get("verdict")
    label = {k: v for k, v in (card.get("label") or {}).items() if k in SampleLabel.model_fields}
    ticket = {k: v for k, v in (card.get("ticket") or {}).items() if k in SampleTicket.model_fields}
    return SampleCard(
        key=f"{card['item_id']}|{card['stage']}",
        item_id=card["item_id"],
        stage=card["stage"],
        sample_kind=card["sample_kind"],
        stratum=card["stratum"],
        weight=card["weight"],
        verdict=verdict,
        correction=correction,
        label=SampleLabel(**label),
        ticket=SampleTicket(**ticket),
    )


@router.get("/runs/{run_id}", response_model=RunDetailOut)
def run_detail(run_id: str, request: Request, conn: sqlite3.Connection = Depends(read_conn)) -> RunDetailOut:
    # One run: its row, review sample cards (label runs), finding counts (finding runs) and last eval summary.
    from sed.ai.review import correction_options, sample
    from sed.ai.runs import run_row

    row = conn.execute("SELECT * FROM ai_run WHERE run_id = ?", (run_id,)).fetchone()
    if row is None:
        raise PreconditionFailed(f"Unknown AI run '{run_id}'")
    full = run_row(row)
    run = RunRow(**{k: full.get(k) for k in RunRow.model_fields})
    cards: dict[str, Any] = {"random": [], "lowest_confidence": [], "matrix": [], "misfiled": {}}
    options: dict[str, Any] = {}
    if row["status"] != "running" and row["skill"] != "manual":
        with contextlib.suppress(PreconditionFailed, ValidationFailed):
            cards = sample(request.app.state.paths, run_id)
        if cards["random"] or cards["lowest_confidence"]:
            options = correction_options(request.app.state.paths, row["skill"])
    findings = dict(conn.execute("SELECT status, COUNT(*) FROM finding WHERE run_id = ? GROUP BY status", (run_id,)))
    eval_file = request.app.state.paths.runs / run_id / "eval.json"
    evaluation: dict[str, Any] = {}
    if eval_file.is_file():
        try:
            evaluation = json.loads(eval_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            evaluation = {}
    try:
        inputs = [str(x) for x in json.loads(row["input_run_ids_json"] or "[]")]
    except ValueError:
        inputs = []
    return RunDetailOut(
        run=run,
        skill_hash=row["skill_hash"],
        model_reported=row["model_reported"],
        input_run_ids=inputs,
        random=[_card(c) for c in cards.get("random", [])],
        lowest_confidence=[_card(c) for c in cards.get("lowest_confidence", [])],
        matrix=list(cards.get("matrix", [])),
        misfiled={str(k): int(v) for k, v in (cards.get("misfiled") or {}).items()},
        findings={str(k): int(v) for k, v in findings.items()},
        eval_passed=evaluation.get("passed"),
        eval_checks={str(k): bool(v) for k, v in (evaluation.get("checks") or {}).items()},
        categories=[CategoryOption(**c) for c in options.get("categories", [])],
        misfiled_as=[str(m) for m in options.get("misfiled_as", [])],
    )


@router.post("/runs/{run_id}/verdicts", response_model=VerdictsOut)
def run_verdicts(run_id: str, body: VerdictsIn, request: Request) -> VerdictsOut:
    # Verdicts for sampled items: "correct", "incorrect" (with an optional correction), or null to skip.
    from sed.ai.review import record_verdict_data

    data: dict[str, Any] = {}
    for key, verdict in body.verdicts.items():
        if verdict == "incorrect":
            correction = body.corrections.get(key)
            extra = {k: v for k, v in (correction.model_dump() if correction else {}).items() if v is not None}
            data[key] = {"verdict": "incorrect", **extra}
        else:
            data[key] = verdict
    return VerdictsOut(**record_verdict_data(request.app.state.paths, run_id, data, reviewer=_reviewer(request)))


@router.post("/runs/{run_id}/review", response_model=RunReviewOut)
def run_review(run_id: str, body: RunReviewIn, request: Request) -> RunReviewOut:
    # Approve a label run once every random-sample verdict is present, or reject it (a note is required).
    from sed.ai.review import approve_run, reject_run

    reviewer = _reviewer(request)
    if body.action == "approve":
        result = approve_run(request.app.state.paths, run_id, reviewer, body.note)
    else:
        result = reject_run(request.app.state.paths, run_id, reviewer, body.note or "")
    return RunReviewOut(**{k: v for k, v in result.items() if k in RunReviewOut.model_fields})


@router.post("/labels/correct", response_model=LabelCorrectionOut)
def correct_label(body: LabelCorrectionIn, request: Request) -> LabelCorrectionOut:
    # A human label for one ticket stage, in the auto-approved manual run of the day.
    from sed.ai.review import correct_label as correct

    correction = {
        k: v
        for k, v in {
            "category": body.category,
            "subcategory": body.subcategory,
            "symptom_key": body.symptom_key,
            "misfiled_as": body.misfiled_as,
        }.items()
        if v is not None
    }
    result = correct(
        request.app.state.paths, body.ticket_id, body.stage, correction, _reviewer(request), skill=body.skill
    )
    return LabelCorrectionOut(ticket_id=result["ticket_id"], stage=result["stage"], run_id=result["run_id"])


@router.get("/ai/review-rates", response_model=ReviewRatesOut)
def review_rates(
    skill: str | None = Query(None, max_length=60),
    conn: sqlite3.Connection = Depends(read_conn),
) -> ReviewRatesOut:
    # How reviewers decided on each skill version (skill hash) per month.
    from sed.ai.rates import review_rates as rates

    return ReviewRatesOut(**rates(conn, skill=skill))
