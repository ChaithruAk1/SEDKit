"""Claims: exactly the trimmed items, never shared between concurrent runs, reclaimable after expiry, released."""

from __future__ import annotations

import json
import os
import subprocess
import sys

from sed import db
from sed.ai.runs import finish_run
from tests.conftest import REPO
from tests.fake_agent.flow import SKILL, query, scalar, start


def _claims(paths, run_id):
    return {(r[0], r[1]) for r in query(paths, "SELECT ticket_id, stage FROM ai_claim WHERE run_id = ?", run_id)}


def _items(paths, run_id):
    return {
        (r[0], r[1])
        for r in query(
            paths,
            "SELECT i.item_id, i.stage FROM ai_batch_item i JOIN ai_batch b ON b.batch_id = i.batch_id "
            "WHERE b.run_id = ?",
            run_id,
        )
    }


def test_period_with_limit_claims_exactly_the_trimmed_items(ops_profile_rw):
    paths = ops_profile_rw.paths
    dry = start(paths, dry_run=True)
    assert dry.plan["items"] > 100
    plan = start(paths, limit=100)
    assert scalar(paths, "SELECT COUNT(*) FROM ai_claim") == 100
    assert _claims(paths, plan.run_id) == _items(paths, plan.run_id)
    leases = {r[0] for r in query(paths, "SELECT lease_expires_at FROM ai_claim")}
    assert len(leases) == 1 and next(iter(leases)) > db.utc_now()


def test_a_second_run_skips_live_claims(ops_profile_rw):
    paths = ops_profile_rw.paths
    first = start(paths, limit=100)
    second = start(paths, limit=100)
    assert not _items(paths, first.run_id) & _items(paths, second.run_id)
    assert len(_claims(paths, second.run_id)) == 100


def test_concurrent_start_runs_never_claim_the_same_item(ops_profile_rw):
    paths = ops_profile_rw.paths
    env = {**os.environ, "PYTHONUTF8": "1"}
    argv = [sys.executable, "-m", "sed", "ai", "start-run", SKILL, "--scope", "period:2026-08", "--limit", "100"]
    argv += ["--batch-size", "50", "--data-dir", str(paths.data_dir), "--profile", "synthetic", "--json"]
    procs = [
        subprocess.Popen(
            argv, cwd=REPO, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8"
        )
        for _ in range(2)
    ]
    outputs = [p.communicate(timeout=180) for p in procs]
    plans = []
    for proc, (out, err) in zip(procs, outputs, strict=True):
        assert proc.returncode == 0, (out, err)
        lines = out.strip().splitlines()
        assert len(lines) == 1
        plans.append(json.loads(lines[0]))
    run_a, run_b = (p["run_id"] for p in plans)
    assert run_a != run_b and all(p["plan"]["claimed"] == 100 for p in plans)
    items_a, items_b = _items(paths, run_a), _items(paths, run_b)
    assert len(items_a) == len(items_b) == 100 and not items_a & items_b
    assert _claims(paths, run_a) == items_a and _claims(paths, run_b) == items_b
    assert scalar(paths, "SELECT COUNT(*) FROM ai_claim") == 200


def test_expired_lease_is_reclaimable(ops_profile_rw):
    paths = ops_profile_rw.paths
    first = start(paths, limit=100)
    conn = db.connect(paths.db)
    try:
        with db.write_tx(conn):
            conn.execute(
                "UPDATE ai_claim SET lease_expires_at = '2000-01-01T00:00:00Z' WHERE run_id = ?", (first.run_id,)
            )
    finally:
        conn.close()
    second = start(paths, limit=100)
    assert _items(paths, second.run_id) == _items(paths, first.run_id)
    assert _claims(paths, second.run_id) == _items(paths, first.run_id)
    assert _claims(paths, first.run_id) == set()


def test_finish_run_releases_claims(ops_profile_rw):
    paths = ops_profile_rw.paths
    plan = start(paths, limit=100, batch_size=50)
    summary = finish_run(paths, plan.run_id)
    assert summary.counts["claims_released"] == 100 and summary.counts["claimed"] == 100
    assert scalar(paths, "SELECT COUNT(*) FROM ai_claim WHERE run_id = ?", plan.run_id) == 0
    assert summary.status == "failed" and summary.failed_batches == ["batch_0001", "batch_0002"]
    released = start(paths, limit=100)
    assert _items(paths, released.run_id) == _items(paths, plan.run_id)
