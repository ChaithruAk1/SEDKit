"""Multi-process access on Windows: a bulk import (CLI), two rule-refresh writers and a metrics reader run at once.

Asserts no "database is locked" surfaces (writers wait on BEGIN IMMEDIATE + busy_timeout), every process exits 0,
readers stay fast while writes run (WAL), and the WAL file stays bounded and can be checkpointed afterwards.
The AI-ingest and API legs from the plan are added when those modules exist (M2/M4).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

from sed import bootstrap, db
from sed.paths import get_paths
from sed.synth.generate import SynthOptions, generate

WORKER = r"""
import json, sys, time
from datetime import UTC, date, datetime
from pathlib import Path
from sed import analytics, db, metrics
from sed.paths import Paths
from sed.settings import load_settings

mode, data_dir, stop_file = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
paths = Paths("synthetic", data_dir)
settings = load_settings(paths)
out = {"ops": 0, "errors": [], "lat_ms": [], "max_wal": 0}
wal = Path(str(paths.db) + "-wal")
deadline = time.monotonic() + 120
while not stop_file.exists() and time.monotonic() < deadline:
    try:
        start = time.perf_counter()
        conn = db.connect(paths.db, readonly=(mode == "read"))
        try:
            if mode == "refresh":
                analytics.refresh_rule_findings(conn, paths, date(2026, 9, 1), force=True)
            else:
                at = datetime(2026, 8, 31, tzinfo=UTC)
                metrics.backlog(conn, metrics.Filters(kind="incident"), at)
                metrics.attention(conn, metrics.Filters(), at, settings.thresholds, limit=50)
                out["lat_ms"].append((time.perf_counter() - start) * 1000)
        finally:
            conn.close()
        out["ops"] += 1
    except Exception as exc:
        out["errors"].append(f"{type(exc).__name__}: {exc}")
    try:  # a checkpoint can remove the WAL between the check and the stat
        out["max_wal"] = max(out["max_wal"], wal.stat().st_size)
    except OSError:
        pass
    time.sleep(0.02)
print(json.dumps(out))
"""


def _env(root: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.update({"SED_DATA_ROOT": str(root), "PYTHONUTF8": "1", "UV_NO_SYNC": "1"})
    env.pop("SED_PROFILE", None)
    return env


def test_import_refresh_and_reads_run_concurrently(data_root: Path, tmp_path: Path):
    paths = get_paths("synthetic")
    bootstrap.init_profile(paths, new_salt=True, write_claude_settings=False)
    generate(paths, SynthOptions(seed=11, as_of=date(2026, 9, 1), months=6, scale=0.05))
    env = _env(data_root)
    stop = tmp_path / "stop"

    workers = [
        subprocess.Popen(
            [sys.executable, "-c", WORKER, mode, str(paths.data_dir), str(stop)],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        for mode in ("refresh", "refresh", "read")
    ]
    time.sleep(1.0)  # let the workers start hammering before the bulk import begins
    importer = subprocess.run(
        [sys.executable, "-m", "sed", "import", "--inbox", "--profile", "synthetic", "--json"],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
    )
    stop.write_text("stop", encoding="utf-8")
    results = []
    for proc in workers:
        stdout, stderr = proc.communicate(timeout=120)
        assert proc.returncode == 0, stderr[-2000:]
        results.append(json.loads(stdout.strip().splitlines()[-1]))

    assert importer.returncode == 0, (importer.stdout[-2000:], importer.stderr[-2000:])
    summary = json.loads(importer.stdout.strip().splitlines()[-1])["summary"]
    assert summary["errors"] == 0 and summary["imported"] > 20

    for mode, result in zip(("refresh", "refresh", "read"), results, strict=True):
        assert result["errors"] == [], (mode, result["errors"][:3])
        assert result["ops"] >= 3, (mode, result["ops"])
    latencies = sorted(results[2]["lat_ms"])
    p95 = latencies[int(len(latencies) * 0.95) - 1]
    assert p95 < 1000, p95
    assert max(r["max_wal"] for r in results) < 128 * 1024 * 1024

    conn = db.connect(paths.db)
    try:
        busy, _, _ = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        assert busy == 0
        assert conn.execute("SELECT COUNT(*) FROM ticket").fetchone()[0] > 1000
        assert conn.execute("SELECT COUNT(*) FROM import_batch WHERE status = 'failed'").fetchone()[0] == 0
    finally:
        conn.close()
