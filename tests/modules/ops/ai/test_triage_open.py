"""sed-triage-open: only open P1-P3 tickets, the triage contract unchanged, a suggestions file with approved clusters
and the run's P1/P2 lines, and its own skill hash."""

from __future__ import annotations

import json
from pathlib import Path

from sed.ai.contract import StartParams
from sed.ai.ingest import ingest_file
from sed.ai.runs import finish_run, start_run
from sed.modules.ops.ai.triage import TriageBatchHandler
from sed.modules.ops.ai.triage_open import TriageOpenHandler
from tests.fake_agent.flow import fake_output, query


def test_open_p1_to_p3_only_with_suggestions(ops_profile_rw):
    paths = ops_profile_rw.paths
    plan = start_run(paths, "sed-triage-open", StartParams(scope="new", batch_size=200))
    assert plan.run_id and plan.plan["items"] > 0
    rows = [json.loads(line) for b in plan.inputs for line in Path(b.packet).read_text(encoding="utf-8").splitlines()]
    assert {r["stage"] for r in rows} == {"open"} and max(r["prio"] for r in rows) <= 3
    names = {Path(p).name for p in plan.context}
    assert names == {"context.md", "suggestions.md"}
    suggestions = Path(next(p for p in plan.context if p.endswith("suggestions.md"))).read_text(encoding="utf-8")
    assert "## Approved issue clusters" in suggestions and "## Open P1/P2 lines" in suggestions
    for batch in plan.inputs:
        assert ingest_file(paths, plan.run_id, fake_output(plan, batch))["status"] == "ingested"
    summary = finish_run(paths, plan.run_id)
    assert summary.status == "completed" and summary.counts["labelled"] == len(rows)
    skill = query(paths, "SELECT skill FROM ai_run WHERE run_id = ?", plan.run_id)[0][0]
    assert skill == "sed-triage-open"


def test_config_inputs_differ_from_the_batch_skill(ops_profile_rw):
    paths = ops_profile_rw.paths
    batch, open_ = TriageBatchHandler().config_inputs(paths), TriageOpenHandler().config_inputs(paths)
    assert (
        open_["max_priority"] == 3 and {k: v for k, v in open_.items() if k not in ("stage", "max_priority")} == batch
    )
