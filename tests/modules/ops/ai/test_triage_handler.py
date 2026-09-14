"""TriageBatchHandler details: payloads, context and vocabulary files, exclusions, taxonomy and weekly AI facts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sed import db
from sed.ai.ingest import ingest_file
from sed.ai.review import reject_run
from sed.ai.runs import finish_run
from sed.errors import ValidationFailed
from sed.modules import metric_definitions
from sed.modules.ops.ai.definitions import AI_DEFINITIONS
from sed.modules.ops.ai.taxonomy import load_taxonomy, slugify_symptom
from sed.modules.ops.reports.ai_provenance import AI_FACTS, weekly_ai
from sed.reports.snapshot import build_request
from tests.fake_agent.flow import context_text, fake_output, finished_run, label_all, query, review_and_approve, start
from tests.fake_agent.triage import parse_taxonomy

PAYLOAD_KEYS = {"stage", "app", "kind", "prio", "sn_cat", "group", "short", "desc", "close_code", "close"}


def _packet_rows(plan) -> list[dict]:
    rows = []
    for batch in plan.inputs:
        rows += [json.loads(line) for line in Path(batch.packet).read_text(encoding="utf-8").splitlines()]
    return rows


def test_payload_fields_and_limits(ops_profile_rw):
    plan = start(ops_profile_rw.paths, limit=150, batch_size=100)
    rows = _packet_rows(plan)
    stages = {r["stage"] for r in rows}
    assert len(rows) == 150 and stages == {"open", "resolved"}
    for r in rows:
        assert set(r) - {"ref"} <= PAYLOAD_KEYS and None not in r.values()
        assert r["kind"] in {"incident", "problem"}
        assert len(r.get("short", "")) <= 160 and len(r.get("desc", "")) <= 500 and len(r.get("close", "")) <= 300
        if r["stage"] == "open":
            assert "close" not in r and "close_code" not in r


def test_context_is_machine_readable_and_matches_the_taxonomy(ops_profile_rw):
    paths = ops_profile_rw.paths
    plan = start(paths, limit=5)
    context = context_text(plan)
    taxonomy = load_taxonomy(paths)
    assert parse_taxonomy(context) == {c.code: list(c.subcategories) for c in taxonomy.categories.values()}
    assert "untrusted" in context.lower() and all(f"`{m}`" in context for m in taxonomy.misfiled_as)
    assert plan.run_id in context


def test_effective_taxonomy_is_layered(ops_profile_rw):
    paths = ops_profile_rw.paths
    override = paths.config / "ops" / "taxonomy.yaml"
    override.parent.mkdir(parents=True, exist_ok=True)
    override.write_text(
        "categories:\n  security:\n    description: Security incidents\n    subcategories: [phishing]\n"
        "  how_to: ~delete\n",
        encoding="utf-8",
    )
    plan = start(paths, limit=10, batch_size=10)
    context = context_text(plan)
    assert "`security`" in context and "`how_to`" not in context
    document = json.loads(fake_output(plan, plan.inputs[0]).read_text(encoding="utf-8"))
    document["items"][0].update(am_category="security", am_subcategory="phishing")
    document["items"][1].update(am_category="how_to", am_subcategory=None)
    Path(plan.inputs[0].out).write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValidationFailed) as exc:
        ingest_file(paths, plan.run_id, plan.inputs[0].out)
    assert [d["ref"] for d in exc.value.details] == [document["items"][1]["ref"]]


def test_labels_of_active_runs_exclude_items_but_rejected_runs_do_not(ops_profile_rw):
    paths = ops_profile_rw.paths
    plan, _ = finished_run(paths, limit=40, batch_size=40)
    labelled = {r[0] for r in query(paths, "SELECT ticket_id FROM ai_ticket_label WHERE run_id = ?", plan.run_id)}
    again = start(paths, limit=40, batch_size=40)
    picked = {
        r[0] for r in query(paths, "SELECT item_id FROM ai_batch_item WHERE batch_id LIKE ?", f"{again.run_id}/%")
    }
    assert labelled and not labelled & picked
    finish_run(paths, again.run_id)
    reject_run(paths, plan.run_id, "tester", "test rejection")
    third = start(paths, limit=40, batch_size=40)
    picked = {
        r[0] for r in query(paths, "SELECT item_id FROM ai_batch_item WHERE batch_id LIKE ?", f"{third.run_id}/%")
    }
    assert picked == labelled


def test_vocabulary_lists_approved_symptom_keys(ops_profile_rw, tmp_path):
    paths = ops_profile_rw.paths
    plan, _ = finished_run(paths, scope="period:2026-08", limit=100, batch_size=100)
    review_and_approve(paths, plan.run_id, tmp_path)
    keys = {r[0] for r in query(paths, "SELECT symptom_key FROM ai_ticket_label WHERE run_id = ?", plan.run_id)}
    later = start(paths, scope="period:2026-07", limit=100, batch_size=100)
    vocab = Path(later.inputs[0].aux[0]).read_text(encoding="utf-8")
    assert "# no approved symptom keys yet" not in vocab and "\n## " in vocab
    listed = {line.split(" | ")[0] for line in vocab.splitlines() if " | " in line and not line.startswith("#")}
    assert listed and listed <= keys
    assert all(len(line.split(" | ")) == 3 for line in vocab.splitlines() if " | " in line and not line.startswith("#"))


def test_low_confidence_count_uses_the_threshold(ops_profile_rw):
    paths = ops_profile_rw.paths
    plan = start(paths, limit=30, batch_size=30)
    out = fake_output(plan, plan.inputs[0])
    document = json.loads(out.read_text(encoding="utf-8"))
    expected = sum(1 for item in document["items"] if item["confidence"] < 0.5)
    assert label_all(paths, plan)[0]["low_confidence"] == expected


def test_slugify_symptom():
    assert slugify_symptom("Queue Messages STUCK!") == "queue_messages_stuck"
    assert slugify_symptom("Zürich VPN — drops") == "zurich_vpn_drops"
    assert slugify_symptom("!!!") == ""
    assert len(slugify_symptom("x" * 100)) == 60


def test_weekly_ai_without_runs(ops_profile_rw):
    paths = ops_profile_rw.paths
    conn = db.connect(paths.db, readonly=True)
    try:
        parts = weekly_ai(build_request(conn, paths, "weekly", "2026-W35"))
    finally:
        conn.close()
    assert parts.ai_runs == [] and parts.ai_derived_tables == ("category_breakdown",)
    assert set(parts.ai_derived_facts) == set(parts.facts) == set(AI_FACTS) == set(AI_DEFINITIONS)
    assert all(f["value"] is None and f["definition"] in AI_DEFINITIONS for f in parts.facts.values())
    definitions = metric_definitions(paths)
    assert all(definitions[k][0] == "pct" for k in AI_FACTS)


def test_weekly_ai_with_an_approved_run(ops_profile_rw, tmp_path):
    paths = ops_profile_rw.paths
    plan, _ = finished_run(paths, limit=100, batch_size=50)
    approval = review_and_approve(paths, plan.run_id, tmp_path)
    conn = db.connect(paths.db, readonly=True)
    try:
        parts = weekly_ai(build_request(conn, paths, "weekly", "2026-W35"))
    finally:
        conn.close()
    assert [r["run_id"] for r in parts.ai_runs] == [plan.run_id]
    run = parts.ai_runs[0]
    assert run["used_for"] == ["category_breakdown"] and run["sample_n"] == 30 and run["status"] == "approved"
    assert parts.facts["ai.category.sample_ci_low_pct"]["value"] == round(100 * approval["sample_ci_low"], 1)
    assert parts.facts["ai.category.labelled_pct"]["value"] > 0
