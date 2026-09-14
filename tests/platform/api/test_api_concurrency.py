"""API readers keep working while a bulk import runs in another process (WAL readers, per-request connections)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from datetime import date

from sed import bootstrap
from sed.paths import get_paths
from sed.synth.generate import SynthOptions, generate
from tests.fixtures.api import api_client

READ_PATHS = (
    "/api/meta",
    "/api/imports?limit=20",
    "/api/dq/unmapped",
    "/api/findings?status=all",
    "/api/alias-targets?kind=vendor",
    "/api/runs",
)


def test_api_readers_during_a_subprocess_import_see_no_lock_errors(data_root, tmp_path):
    paths = get_paths("synthetic")
    bootstrap.init_profile(paths, new_salt=True, write_claude_settings=False)
    generate(paths, SynthOptions(seed=7, as_of=date(2026, 9, 1), months=4, scale=0.03))
    client = api_client(paths, send_token=False)
    stop = threading.Event()
    results: dict[str, list] = {"ok": [], "errors": [], "latency_ms": []}

    def reader() -> None:
        i = 0
        while not stop.is_set():
            path = READ_PATHS[i % len(READ_PATHS)]
            i += 1
            start = time.perf_counter()
            try:
                response = client.get(path)
            except Exception as exc:  # a transport-level failure is an error too
                results["errors"].append((path, f"{type(exc).__name__}: {exc}"))
                continue
            results["latency_ms"].append((time.perf_counter() - start) * 1000)
            if response.status_code != 200 or "locked" in response.text.lower():
                results["errors"].append((path, response.status_code, response.text[:300]))
            else:
                results["ok"].append(path)
            time.sleep(0.01)

    threads = [threading.Thread(target=reader, name=f"api-reader-{n}", daemon=True) for n in range(2)]
    for thread in threads:
        thread.start()
    time.sleep(0.5)
    env = {**os.environ, "SED_DATA_ROOT": str(data_root), "PYTHONUTF8": "1", "UV_NO_SYNC": "1"}
    env.pop("SED_PROFILE", None)
    try:
        importer = subprocess.run(
            [sys.executable, "-m", "sed", "import", "--inbox", "--profile", "synthetic", "--json"],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=300,
        )
        imported_during_reads = len(results["ok"])
    finally:
        stop.set()
        for thread in threads:
            thread.join(timeout=60)

    assert importer.returncode == 0, (importer.stdout[-2000:], importer.stderr[-2000:])
    summary = json.loads(importer.stdout.strip().splitlines()[-1])["summary"]
    assert summary["errors"] == 0 and summary["imported"] > 20
    assert results["errors"] == [], results["errors"][:5]
    assert imported_during_reads >= 2 * len(READ_PATHS), imported_during_reads
    after = client.get("/api/imports?limit=1000").json()["items"]
    assert len(after) >= summary["imported"] and all(i["status"] == "completed" for i in after)
