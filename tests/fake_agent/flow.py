"""Test helpers that drive the AI lifecycle in-process with the fake agent (start-run -> label -> ingest -> finish)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from tests.fake_agent.triage import label_packet, write_output
from tests.fake_agent.verdicts import verdicts_for

SKILL = "sed-triage-batch"


def start(paths: Any, *, scope: str = "period:2026-08", **options: Any) -> Any:
    from sed.ai.contract import StartParams
    from sed.ai.runs import start_run

    return start_run(paths, SKILL, StartParams(scope=scope, **options))


def context_text(plan: Any) -> str:
    return Path(plan.context[0]).read_text(encoding="utf-8")


def fake_output(plan: Any, batch: Any, *, mode: str = "valid") -> Path:
    """Label one batch with the fake agent and write its output file; returns the out path."""
    document = label_packet(Path(batch.packet).read_text(encoding="utf-8"), context_text(plan), mode=mode)
    write_output(batch.out, document)
    return Path(batch.out)


def label_all(paths: Any, plan: Any, *, mode: str = "valid") -> list[dict[str, Any]]:
    from sed.ai.ingest import ingest_file

    return [ingest_file(paths, plan.run_id, fake_output(plan, batch, mode=mode)) for batch in plan.inputs]


def query(paths: Any, sql: str, *params: Any) -> list[sqlite3.Row]:
    from sed import db

    conn = db.connect(paths.db, readonly=True)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def scalar(paths: Any, sql: str, *params: Any) -> Any:
    rows = query(paths, sql, *params)
    return rows[0][0] if rows else None


def finished_run(paths: Any, **options: Any) -> tuple[Any, Any]:
    from sed.ai.runs import finish_run

    options.setdefault("limit", 100)
    options.setdefault("batch_size", 50)
    plan = start(paths, **options)
    label_all(paths, plan)
    return plan, finish_run(paths, plan.run_id)


def review_and_approve(paths: Any, run_id: str, folder: Path, *, incorrect_every: int = 10) -> dict[str, Any]:
    from sed.ai.review import approve_run, record_verdicts, sample

    template = folder / f"verdicts_{run_id}.json"
    cards = sample(paths, run_id, template=template)
    template.write_text(json.dumps(verdicts_for(cards, incorrect_every=incorrect_every)), encoding="utf-8")
    record_verdicts(paths, run_id, template, reviewer="tester")
    return approve_run(paths, run_id, "tester", "fake review")
