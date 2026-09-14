"""`sed ai start-run`: selection, trimming, max-items refusal, packets, dry runs, scopes, resume and plan paths."""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sed import bootstrap, db
from sed.ai import packets
from sed.ai.contract import PacketLimits, RunContext, StartParams, WorkItem
from sed.ai.ingest import ingest_file
from sed.ai.runs import scope_bounds, start_run
from sed.calendar import iso_utc, local_midnight_utc
from sed.errors import PreconditionFailed, ValidationFailed
from sed.paths import get_paths
from sed.settings import load_settings
from tests.fake_agent.flow import SKILL, fake_output, query, scalar, start

ABS_POSIX = re.compile(r"^[A-Za-z]:/[^\\]+$|^/[^\\]+$")


def _counts(paths, run_id=None):
    where, params = ("WHERE run_id = ?", (run_id,)) if run_id else ("", ())
    return {
        table: scalar(paths, f"SELECT COUNT(*) FROM {table} {where}", *params)
        for table in ("ai_run", "ai_batch", "ai_claim")
    }


def test_plan_counts_rows_and_claims(ops_profile_rw):
    paths = ops_profile_rw.paths
    plan = start(paths, limit=100, batch_size=50)
    assert plan.status == "running" and plan.run_id and not plan.dry_run
    assert plan.plan["items"] == 100 and plan.plan["batches"] == 2 and plan.plan["claimed"] == 100
    assert plan.plan["est_agents"] == 2 and [b.items for b in plan.inputs] == [50, 50]
    assert _counts(paths, plan.run_id) == {"ai_run": 1, "ai_batch": 2, "ai_claim": 100}
    items = scalar(
        paths,
        "SELECT COUNT(*) FROM ai_batch_item i JOIN ai_batch b ON b.batch_id = i.batch_id WHERE b.run_id = ?",
        plan.run_id,
    )
    assert items == 100
    run = query(paths, "SELECT * FROM ai_run WHERE run_id = ?", plan.run_id)[0]
    assert run["status"] == "running" and run["invoked_via"] == "interactive" and len(run["skill_hash"]) == 64
    assert json.loads(run["params_json"])["scope"] == "period:2026-08" and run["input_manifest_sha"]


def test_limit_trims_before_batching(ops_profile_rw):
    plan = start(ops_profile_rw.paths, limit=7, batch_size=5)
    assert plan.plan["items"] == 7 and [b.items for b in plan.inputs] == [5, 2]


def test_model_and_claude_version_recorded(ops_profile_rw):
    paths = ops_profile_rw.paths
    plan = start(paths, limit=3, model_arg="model-under-test", claude_version="9.9.9 (Claude Code)")
    row = query(paths, "SELECT model_arg, claude_version FROM ai_run WHERE run_id = ?", plan.run_id)[0]
    assert tuple(row) == ("model-under-test", "9.9.9 (Claude Code)")


def test_above_max_items_exits_4_and_rolls_back(ops_profile_rw):
    paths = ops_profile_rw.paths
    with pytest.raises(PreconditionFailed) as exc:
        start(paths, max_items=10)
    assert exc.value.exit_code == 4 and exc.value.details["plan"]["items"] > 10
    assert _counts(paths) == {"ai_run": 0, "ai_batch": 0, "ai_claim": 0}
    assert scalar(paths, "SELECT COUNT(*) FROM ai_batch_item") == 0
    assert not any(paths.runs.iterdir())


def test_cli_start_run_above_max_items_prints_one_json_line(ops_profile_rw):
    from sed.cli import app

    paths = ops_profile_rw.paths
    argv = ["ai", "start-run", SKILL, "--scope", "period:2026-08", "--max-items", "5"]
    result = CliRunner().invoke(app, [*argv, "--data-dir", str(paths.data_dir), "--profile", "synthetic", "--json"])
    assert result.exit_code == 4
    lines = result.stdout.strip().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["error"]["kind"] == "precondition"


def test_every_batch_and_line_within_limits(ops_profile_rw):
    plan = start(ops_profile_rw.paths, limit=120, batch_size=50, max_chars=6000)
    assert plan.plan["max_chars_per_batch"] <= 6000 and len(plan.inputs) >= 3
    seen = 0
    for batch in plan.inputs:
        text = Path(batch.packet).read_bytes().decode("utf-8")
        assert "\r" not in text and text.endswith("\n")
        assert len(text) == batch.chars <= 6000
        lines = text.splitlines()
        assert len(lines) == batch.items <= 50
        refs = []
        for line in lines:
            assert len(line) <= 1800
            refs.append(json.loads(line)["ref"])
        assert refs == [f"T{i:03d}" for i in range(1, len(lines) + 1)]
        seen += len(lines)
    assert seen == 120


def test_render_line_truncates_desc_then_close_then_short():
    payload = {"stage": "resolved", "short": "s" * 160, "desc": "d" * 3000, "close": "c" * 300}
    line = packets.render_line("T001", payload, 1800)
    data = json.loads(line)
    assert len(line) <= 1800 and data["short"] == "s" * 160 and data["close"] == "c" * 300
    assert data["desc"].endswith("…") and len(data["desc"]) < 3000
    tight = json.loads(packets.render_line("T001", payload, 300))
    tight.pop("ref")
    assert tight["desc"] == "" and tight["close"].endswith("…") and tight["short"] == "s" * 160
    assert len(json.dumps({"ref": "T001", **tight}, ensure_ascii=False, separators=(",", ":"))) <= 300
    quoted = packets.render_line("T001", {"desc": '"\\' * 2000}, 1800)
    assert len(quoted) <= 1800 and json.loads(quoted)["ref"] == "T001"


def test_packet_limits_smaller_than_a_line_still_fit():
    items = [WorkItem(f"x:{i}", "open", "h", {"desc": "word " * 400}) for i in range(5)]
    batches = packets.plan_batches(items, PacketLimits(max_items=10, max_chars=1000))
    assert len(batches) == 5 and all(b.chars <= 1000 for b in batches)


def test_packets_contain_no_ticket_ids(ops_profile_rw):
    paths = ops_profile_rw.paths
    plan = start(paths, limit=100, batch_size=50)
    ids = query(
        paths,
        "SELECT t.ticket_id, t.number, t.sys_id FROM ai_batch_item i JOIN ai_batch b ON b.batch_id = i.batch_id "
        "JOIN ticket t ON t.ticket_id = i.item_id WHERE b.run_id = ?",
        plan.run_id,
    )
    assert len(ids) == 100
    run_dir = Path(plan.run_dir)
    texts = [p.read_text(encoding="utf-8") for p in (run_dir / "in").iterdir()]
    texts.append((run_dir / "manifest.json").read_text(encoding="utf-8"))
    for row in ids:
        for value in (row["ticket_id"], row["number"], row["sys_id"]):
            if value:
                assert not any(value in text for text in texts), value


def test_run_folder_files(ops_profile_rw):
    plan = start(ops_profile_rw.paths, limit=10, batch_size=5)
    run_dir = Path(plan.run_dir)
    names = sorted(p.name for p in (run_dir / "in").iterdir())
    assert names == [
        "batch_0001.jsonl",
        "batch_0002.jsonl",
        "context.md",
        "vocab_batch_0001.txt",
        "vocab_batch_0002.txt",
    ]
    assert (run_dir / "out").is_dir() and not any((run_dir / "out").iterdir())
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["run_id"] == plan.run_id and [b["batch"] for b in manifest["batches"]] == [
        "batch_0001",
        "batch_0002",
    ]
    assert "# no approved symptom keys yet" in (run_dir / "in" / "vocab_batch_0001.txt").read_text(encoding="utf-8")


def test_runplan_paths_are_absolute_with_forward_slashes(ops_profile_rw):
    plan = start(ops_profile_rw.paths, limit=10, batch_size=5)
    paths_in_plan = [plan.run_dir, plan.out_dir, *plan.context]
    for batch in plan.inputs:
        paths_in_plan += [batch.packet, batch.out, *batch.aux]
    for value in paths_in_plan:
        assert "\\" not in value and ABS_POSIX.match(value), value
    assert all(Path(p).is_file() for b in plan.inputs for p in [b.packet, *b.aux])
    assert all(b.out.startswith(plan.out_dir + "/") and b.out.endswith(f"/{b.batch}.json") for b in plan.inputs)


def test_dry_run_writes_nothing(ops_profile):
    paths = ops_profile.paths
    before = sorted(p.name for p in paths.runs.iterdir()) if paths.runs.is_dir() else []
    plan = start_run(paths, SKILL, StartParams(scope="period:2026-08", limit=100, batch_size=50, dry_run=True))
    assert plan.status == "planned" and plan.dry_run and plan.run_id is None and plan.inputs == []
    assert {k: plan.plan[k] for k in ("items", "batches", "est_agents")} == {
        "items": 100,
        "batches": 2,
        "est_agents": 2,
    }
    assert 0 < plan.plan["longest_line_chars"] <= 1800 and plan.plan["max_chars_per_batch"] > 0
    assert (sorted(p.name for p in paths.runs.iterdir()) if paths.runs.is_dir() else []) == before
    assert _counts(paths) == {"ai_run": 0, "ai_batch": 0, "ai_claim": 0}


def test_select_runs_on_a_query_only_connection(ops_profile):
    from sed.modules import handler

    paths = ops_profile.paths
    conn = db.connect(paths.db, readonly=True)
    try:
        assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
        settings = load_settings(paths)
        ctx = RunContext(
            conn, paths, settings, None, SKILL, StartParams(scope="new"), date(2026, 9, 1), None, None, None
        )
        items = handler(SKILL).select(ctx)
    finally:
        conn.close()
    assert items and all(i.stage in {"open", "resolved"} and i.input_hash for i in items)


def test_real_profile_without_approval_exits_4(tmp_path):
    paths = get_paths("real", tmp_path / "real")
    bootstrap.init_profile(paths, new_salt=True, write_claude_settings=False)
    with pytest.raises(PreconditionFailed, match="approved"):
        start_run(paths, SKILL, StartParams(scope="since:2026-01-01"))
    with pytest.raises(PreconditionFailed):
        start_run(paths, SKILL, StartParams(scope="since:2026-01-01", dry_run=True))


def test_scope_new_needs_data_and_bad_scopes_exit_2(tmp_path):
    paths = get_paths("synthetic", tmp_path / "empty")
    bootstrap.init_profile(paths, new_salt=True, write_claude_settings=False)
    with pytest.raises(PreconditionFailed, match="scope=new"):
        start_run(paths, SKILL, StartParams(scope="new", dry_run=True))
    for bad in ("last-week", "since:2026-13-01", "period:2026-X1"):
        with pytest.raises(ValidationFailed):
            start_run(paths, SKILL, StartParams(scope=bad, dry_run=True))


def _eligible_since(paths, start_iso: str) -> int:
    return scalar(
        paths,
        "SELECT (SELECT COUNT(*) FROM ticket WHERE kind IN ('incident','problem') AND is_open = 1 AND stale_open = 0 "
        "AND open_hash IS NOT NULL AND opened_at >= ?) + (SELECT COUNT(*) FROM ticket WHERE kind IN "
        "('incident','problem') AND resolved_at IS NOT NULL AND resolved_hash IS NOT NULL AND resolved_at >= ?)",
        start_iso,
        start_iso,
    )


def test_scope_new_selects_only_the_backfill_window(ops_profile_rw):
    paths = ops_profile_rw.paths
    settings = load_settings(paths)
    boundary = iso_utc(local_midnight_utc(date(2026, 9, 1) - timedelta(days=90), settings.reporting_tz))
    assert scope_bounds("new", settings, date(2026, 9, 1)).start_iso == boundary
    dry = start_run(paths, SKILL, StartParams(scope="new", dry_run=True))
    assert dry.plan["items"] == _eligible_since(paths, boundary)
    assert _eligible_since(paths, "2000-01-01T00:00:00Z") > dry.plan["items"]
    override = paths.config / "settings.yaml"
    override.write_text("ai:\n  backfill_days: 30\n", encoding="utf-8")
    boundary_30 = iso_utc(local_midnight_utc(date(2026, 9, 1) - timedelta(days=30), settings.reporting_tz))
    narrower = start_run(paths, SKILL, StartParams(scope="new", dry_run=True))
    assert narrower.plan["items"] == _eligible_since(paths, boundary_30) < dry.plan["items"]
    override.unlink()
    plan = start(paths, scope="new", batch_size=500)
    assert plan.plan["items"] == dry.plan["items"]
    earliest = scalar(
        paths,
        "SELECT MIN(CASE i.stage WHEN 'open' THEN t.opened_at ELSE t.resolved_at END) FROM ai_batch_item i "
        "JOIN ai_batch b ON b.batch_id = i.batch_id JOIN ticket t ON t.ticket_id = i.item_id WHERE b.run_id = ?",
        plan.run_id,
    )
    assert earliest >= boundary


def test_items_are_ordered_by_event_desc(ops_profile_rw):
    paths = ops_profile_rw.paths
    plan = start(paths, limit=60, batch_size=60)
    rows = query(
        paths,
        "SELECT i.item_id, CASE i.stage WHEN 'open' THEN t.opened_at ELSE t.resolved_at END AS event_at "
        "FROM ai_batch_item i JOIN ai_batch b ON b.batch_id = i.batch_id JOIN ticket t ON t.ticket_id = i.item_id "
        "WHERE b.run_id = ? ORDER BY i.ref",
        plan.run_id,
    )
    keys = [(r["event_at"], r["item_id"]) for r in rows]
    assert keys == sorted(keys, key=lambda k: (-_ts(k[0]), k[1]))


def _ts(value: str) -> float:
    from sed.calendar import parse_utc

    return parse_utc(value).timestamp()


def test_resume_returns_only_non_ingested_batches_and_extends_leases(ops_profile_rw):
    paths = ops_profile_rw.paths
    plan = start(paths, limit=100, batch_size=50)
    ingest_file(paths, plan.run_id, fake_output(plan, plan.inputs[0]))
    conn = db.connect(paths.db)
    try:
        with db.write_tx(conn):
            conn.execute(
                "UPDATE ai_claim SET lease_expires_at = '2000-01-01T00:00:00Z' WHERE run_id = ?", (plan.run_id,)
            )
    finally:
        conn.close()
    resumed = start_run(paths, SKILL, StartParams(resume=plan.run_id))
    assert resumed.run_id == plan.run_id and resumed.status == "running"
    assert [b.batch for b in resumed.inputs] == ["batch_0002"] and resumed.inputs[0] == plan.inputs[1]
    assert resumed.context == plan.context and resumed.plan["items"] == 50
    leases = query(paths, "SELECT lease_expires_at FROM ai_claim WHERE run_id = ?", plan.run_id)
    assert len(leases) == 100 and sum(1 for r in leases if r[0] > "2001") == 50


def test_resume_of_unknown_or_finished_run_exits_4(ops_profile_rw):
    from sed.ai.runs import finish_run

    paths = ops_profile_rw.paths
    with pytest.raises(PreconditionFailed, match="Unknown"):
        start_run(paths, SKILL, StartParams(resume="20260101T000000-triage-batch-0000"))
    plan = start(paths, limit=5)
    finish_run(paths, plan.run_id)
    with pytest.raises(PreconditionFailed):
        start_run(paths, SKILL, StartParams(resume=plan.run_id))


def test_nothing_selected_creates_no_run(ops_profile_rw):
    paths = ops_profile_rw.paths
    plan = start(paths, scope="since:2030-01-01")
    assert plan.run_id is None and plan.plan["items"] == 0 and plan.inputs == []
    assert _counts(paths) == {"ai_run": 0, "ai_batch": 0, "ai_claim": 0}


def test_disabled_module_exits_4(ops_profile_rw):
    paths = ops_profile_rw.paths
    (paths.config / "modules.yaml").write_text("enabled: []\n", encoding="utf-8")
    with pytest.raises(PreconditionFailed, match="disabled"):
        start(paths, limit=5)


def test_unknown_skill_exits_2(ops_profile_rw):
    with pytest.raises(ValidationFailed, match="Unknown skill"):
        start_run(ops_profile_rw.paths, "sed-nope", StartParams(scope="new"))


def test_unicode_line_breaks_inside_text_never_split_a_packet_line():
    breaks = "".join(chr(c) for c in (0x85, 0x2028, 0x2029))
    items = [WorkItem(f"x:{i}", "open", "h", {"desc": f"first{breaks}second"}) for i in range(3)]
    (batch,) = packets.plan_batches(items, PacketLimits(max_items=10, max_chars=5000))
    assert len(batch.text.splitlines()) == 3
    assert all(json.loads(line)["desc"] == f"first{breaks}second" for line in batch.lines)
