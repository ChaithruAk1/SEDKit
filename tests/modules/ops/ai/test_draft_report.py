"""sed-draft-report on the ops profile: one batch per spec section against the stored snapshot (summary last with its
aux file), run files, validation of tokens, headlines, length and citations, section drafts with cited fact values, the
approval gate for cited findings and edits, and the approved section in a build."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from sed import db
from sed.ai import findings as F
from sed.ai.contract import StartParams
from sed.ai.ingest import ingest_file
from sed.ai.review import review_findings
from sed.ai.runs import finish_run, start_run
from sed.errors import PreconditionFailed, ValidationFailed
from sed.reports.build import build_report
from sed.reports.snapshot import create_snapshot
from tests.fake_agent.flow import query
from tests.fake_agent.triage import write_output

SKILL = "sed-draft-report"
PERIOD = "2026-W35"


def _snapshot(paths: Any) -> dict:
    conn = db.connect(paths.db)
    try:
        return create_snapshot(conn, paths, "weekly", PERIOD).facts
    finally:
        conn.close()


def _start(paths: Any, **options: Any):
    return start_run(paths, SKILL, StartParams(scope=f"period:{PERIOD}", report="weekly", **options))


def _line(batch: Any) -> dict:
    return json.loads(Path(batch.packet).read_text(encoding="utf-8").splitlines()[0])


def _ingest(paths: Any, plan: Any, batch: Any, **item: Any) -> dict:
    document = {"meta": {"model": "fake"}, "items": [{"ref": _line(batch)["ref"], **item}]}
    write_output(batch.out, document)
    return ingest_file(paths, plan.run_id, batch.out)


def _cluster(paths: Any) -> str:
    conn = db.connect(paths.db)
    try:
        with db.write_tx(conn):
            conn.execute(
                "INSERT INTO ai_run (run_id, skill, skill_hash, schema_version, invoked_via, profile, status, "
                "started_at) VALUES ('rec-dr', 'sed-find-recurring', 'h', 1, 'workflow', 'synthetic', 'completed', "
                "'2026-09-01T05:00:00Z')"
            )
            return F.upsert_draft(
                conn,
                run_id="rec-dr",
                stable_key="issue_cluster:dr",
                kind="issue_cluster",
                title="Posting timeouts",
                body_md="text",
                severity="high",
                confidence=0.8,
                payload={},
            )
    finally:
        conn.close()


def test_start_needs_a_report_period_and_snapshot(ops_profile_rw):
    paths = ops_profile_rw.paths
    with pytest.raises(PreconditionFailed, match="sed report snapshot weekly --period 2026-W35"):
        _start(paths)
    with pytest.raises(ValidationFailed, match="--report"):
        start_run(paths, SKILL, StartParams(scope=f"period:{PERIOD}"))
    with pytest.raises(ValidationFailed, match="period:"):
        start_run(paths, SKILL, StartParams(scope="new", report="weekly"))
    with pytest.raises(ValidationFailed, match="--vendor"):
        start_run(paths, SKILL, StartParams(scope="period:2026-Q3", report="vendor"))


def test_draft_ingest_approval_and_build(ops_profile_rw):
    paths = ops_profile_rw.paths
    facts = _snapshot(paths)
    cluster = _cluster(paths)
    plan = _start(paths)
    lines = [_line(b) for b in plan.inputs]
    assert [line["section_key"] for line in lines] == [
        "highlights",
        "lowlights",
        "recurring_issues",
        "actions",
        "headline",
    ]
    assert [line["type"] for line in lines][-1] == "summary_section"
    assert [len(b.aux) for b in plan.inputs] == [0, 0, 0, 0, 1]
    aux = Path(plan.inputs[-1].aux[0]).read_text(encoding="utf-8").splitlines()
    assert [a.split("\t")[0] for a in aux] == ["highlights", "lowlights", "recurring_issues", "actions"]
    assert aux[0].split("\t")[1] == Path(plan.inputs[0].out).resolve().as_posix()
    names = sorted(Path(p).name for p in plan.context)
    assert names == ["context.md", "facts.md", "findings.md", "tables.md"]
    run_files = {Path(p).name: Path(p).read_text(encoding="utf-8") for p in plan.context}
    assert "`inc.opened`" in run_files["facts.md"] and cluster in run_files["findings.md"]
    assert "untrusted" in run_files["tables.md"] and "`headline` **Headline** (summary" in run_files["context.md"]
    inputs = json.loads(query(paths, "SELECT input_run_ids_json FROM ai_run WHERE run_id = ?", plan.run_id)[0][0])
    assert "rec-dr" in inputs

    highlights, lowlights, recurring, _actions, headline = plan.inputs
    bad = [
        ({"body_md": "{{f:inc.nope}} opened."}, "is not a fact"),
        ({"body_md": "Fine.", "slide_headline": "Up 12 percent"}, "numbers only"),
        ({"body_md": " ".join(["word"] * 200)}, "words, above"),
        ({"body_md": "Fine.", "cited_finding_ids": ["f-missing"]}, "is not a listed finding"),
    ]
    for item, fragment in bad:
        with pytest.raises(ValidationFailed) as refused:
            _ingest(paths, plan, highlights, **item)
        assert fragment in json.dumps(refused.value.details), fragment

    ok = _ingest(paths, plan, highlights, body_md="- {{f:inc.opened}} incidents opened, 12 more than usual.")
    assert ok["status"] == "ingested" and any("bare numbers" in w for w in ok["warnings"])
    _ingest(paths, plan, lowlights, body_md="- Backlog at {{f:inc.backlog}}.")
    _ingest(paths, plan, recurring, body_md="- Posting timeouts continue.", cited_finding_ids=[cluster])
    _ingest(paths, plan, headline, body_md="Service held at {{f:inc.sla.pct}} SLA.", slide_headline="Service held")
    summary = finish_run(paths, plan.run_id)
    assert summary.failed_batches == ["batch_0004"]  # actions was never written

    rows = {
        r["stable_key"].rsplit(":", 1)[1]: r
        for r in query(paths, "SELECT * FROM finding WHERE kind = 'report_section'")
    }
    assert set(rows) == {"highlights", "lowlights", "recurring_issues", "headline"}
    payload = json.loads(rows["highlights"]["payload_json"])
    assert payload["facts"] == {"inc.opened": facts["inc.opened"]["value"]} and payload["snapshot_id"].startswith(
        "snap-"
    )
    assert rows["headline"]["subject_id"] == "weekly:2026-W35" and rows["headline"]["period"] == PERIOD

    with pytest.raises(PreconditionFailed, match="cites unapproved findings"):
        review_findings(paths, [rows["recurring_issues"]["finding_id"]], "approve", "tester")
    with pytest.raises(ValidationFailed, match="only the facts cited"):
        review_findings(paths, [rows["lowlights"]["finding_id"]], "edit", "tester", body_md="{{f:inc.opened}} opened.")
    review_findings(paths, [cluster], "approve", "tester")
    review_findings(
        paths,
        [rows["recurring_issues"]["finding_id"], rows["headline"]["finding_id"]],
        "approve",
        "tester",
    )
    review_findings(paths, [rows["lowlights"]["finding_id"]], "edit", "tester", body_md="- Backlog: {{f:inc.backlog}}.")

    result = build_report(paths, "weekly", PERIOD, ["md"], "approved")
    assert result["readiness"]["ai_sections_shown"] == 3
    text = Path(result["artifacts"][0]["path"]).read_text(encoding="utf-8")
    assert "Posting timeouts continue." in text and "Backlog:" in text and "12 more" not in text
    with pytest.raises(PreconditionFailed):
        build_report(paths, "weekly", PERIOD, ["md"], "approved", require_complete=True)
