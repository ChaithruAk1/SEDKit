"""M2 concurrency on Windows: AI ingests, a bulk import and API reads hit one database at the same time.

Four subprocesses repeatedly ingest canned agent outputs (`python -m sed ai ingest`), one subprocess imports a re-saved
export, and one subprocess loops over API GETs through the app factory. No "database is locked" may surface, every
process exits 0, reads stay fast, and the WAL file stays bounded and can be checkpointed afterwards.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from sed import db
from tests.conftest import REPO
from tests.fake_agent.flow import fake_output, start

INGEST_LOOP = r"""
import json, subprocess, sys, time
from pathlib import Path
run_id, out_file, data_dir, stop_file = sys.argv[1:5]
result = {"ops": 0, "errors": []}
deadline = time.monotonic() + 120
while not Path(stop_file).exists() and time.monotonic() < deadline:
    proc = subprocess.run(
        [sys.executable, "-m", "sed", "ai", "ingest", run_id, out_file, "--data-dir", data_dir,
         "--profile", "synthetic", "--json"],
        capture_output=True, text=True, encoding="utf-8",
    )
    line = (proc.stdout.strip().splitlines() or ["{}"])[-1]
    if proc.returncode != 0 or "locked" in proc.stdout + proc.stderr:
        result["errors"].append(f"exit {proc.returncode}: {line[:200]} {proc.stderr[-200:]}")
    result["ops"] += 1
print(json.dumps(result))
"""

API_LOOP = r"""
import json, sys, time
from pathlib import Path
from fastapi.testclient import TestClient
from sed.api.app import create_app
from sed.paths import Paths
data_dir, stop_file = Path(sys.argv[1]), Path(sys.argv[2])
client = TestClient(create_app(Paths("synthetic", data_dir), token="test-token"), base_url="http://127.0.0.1")
urls = ["/api/ops/overview", "/api/ops/tickets?page_size=50", "/api/runs", "/api/ops/tickets/backlog", "/api/findings"]
result = {"ops": 0, "errors": [], "lat_ms": [], "max_wal": 0}
wal = data_dir / "sed.db-wal"
deadline = time.monotonic() + 120
while not stop_file.exists() and time.monotonic() < deadline:
    for url in urls:
        start = time.perf_counter()
        response = client.get(url)
        result["lat_ms"].append((time.perf_counter() - start) * 1000)
        if response.status_code != 200:
            result["errors"].append(f"{url} {response.status_code} {response.text[:200]}")
        result["ops"] += 1
    try:  # a checkpoint may remove the WAL between any existence check and stat()
        result["max_wal"] = max(result["max_wal"], wal.stat().st_size)
    except FileNotFoundError:
        pass
print(json.dumps(result))
"""


def _env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in {"SED_PROFILE", "SED_EXTRA_MODULES"}}
    env.update({"PYTHONUTF8": "1", "UV_NO_SYNC": "1"})
    return env


def test_ingest_import_and_api_reads_run_concurrently(ops_profile_rw, tmp_path: Path):
    paths = ops_profile_rw.paths
    jobs = []
    for _ in range(4):  # claims keep the four runs disjoint
        plan = start(paths, scope="period:2026-08", limit=40, batch_size=40)
        assert plan.run_id and len(plan.inputs) == 1, plan.plan
        jobs.append((plan.run_id, fake_output(plan, plan.inputs[0])))

    processed = sorted(p for p in paths.processed.rglob("incident_*.csv") if not p.name.startswith("incident_active"))
    source = processed[0] if processed else None
    stop = tmp_path / "stop"
    env = _env()
    popen = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "cwd": REPO,
        "env": env,
    }
    loops = [
        subprocess.Popen([sys.executable, "-c", INGEST_LOOP, run_id, str(out), str(paths.data_dir), str(stop)], **popen)
        for run_id, out in jobs
    ]
    reader = subprocess.Popen([sys.executable, "-c", API_LOOP, str(paths.data_dir), str(stop)], **popen)
    time.sleep(2.0)

    import_result = None
    if source is not None:
        copy = tmp_path / f"{source.stem}_resaved.csv"
        shutil.copyfile(source, copy)
        with copy.open("a", encoding="utf-8", newline="") as fh:
            fh.write("\n")
        import_result = subprocess.run(
            [sys.executable, "-m", "sed", "import", str(copy), "--allow-unmanifested", "--keep-files",
             "--data-dir", str(paths.data_dir), "--profile", "synthetic", "--json"],
            capture_output=True, text=True, encoding="utf-8", cwd=REPO, env=env, timeout=300,
        )  # fmt: skip
    time.sleep(3.0)
    stop.write_text("stop", encoding="utf-8")

    results = []
    for proc in [*loops, reader]:
        stdout, stderr = proc.communicate(timeout=180)
        assert proc.returncode == 0, stderr[-2000:]
        results.append(json.loads(stdout.strip().splitlines()[-1]))

    if import_result is not None:
        assert import_result.returncode == 0, (import_result.stdout[-1000:], import_result.stderr[-1000:])
        assert "locked" not in import_result.stdout + import_result.stderr
    for result in results:
        assert result["errors"] == [], result["errors"][:3]
        assert result["ops"] >= 2
    latencies = sorted(results[-1]["lat_ms"])
    assert latencies[int(len(latencies) * 0.95) - 1] < 2000
    assert results[-1]["max_wal"] < 128 * 1024 * 1024

    conn = db.connect(paths.db)
    try:
        busy, _, _ = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        assert busy == 0
        ingested = conn.execute(
            f"SELECT COUNT(*) FROM ai_batch WHERE status = 'ingested' AND run_id IN ({','.join('?' * len(jobs))})",
            [run_id for run_id, _ in jobs],
        ).fetchone()[0]
        assert ingested == len(jobs)
        dup = conn.execute(
            "SELECT COUNT(*) FROM (SELECT ticket_id, stage FROM ai_claim GROUP BY ticket_id, stage HAVING COUNT(*) > 1)"
        ).fetchone()[0]
        assert dup == 0
    finally:
        conn.close()
