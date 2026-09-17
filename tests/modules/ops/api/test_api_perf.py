"""Performance gate for EVERY GET in contracts/openapi.json (core and every module) on the scale-1.0 synthetic profile.

Run: `uv run pytest -m slow tests/modules/ops/api/test_api_perf.py`
(in an M2 worktree: `wt.sh python -m pytest -m slow ...`).

* Profile: built once (seed 42, pinned salt, about 108k tasks) under SED_PERF_DATA_ROOT, else `<SED_DATA_ROOT>/perf`,
  else `<tempdir>/sed-perf`, and reused while its marker matches. Pending migrations and then
  `src/sed/schema/pending/*.sql` are applied to that database (db.split_sql inside write_tx), as the integrator will
  when numbering them. The synthetic data of every other enabled module with a generator (same seed, as-of and scale)
  is then generated and imported on top on every run (files already imported are skipped by hash, so only new or
  changed module data loads) and its rule findings refreshed.
* Every GET operation must have an entry in PERF_PARAMS (default filters; required parameters only); a missing or stale
  entry fails. Each endpoint gets 20 requests through the in-process client; p95 (nearest rank, the first, cold request
  included) must stay below 1 s, and ticket search (`/api/ops/tickets?q=`) below 300 ms.
* 501 responses (routes of workstreams not merged yet) are recorded as skipped; SED_PERF_REQUIRE_ALL=1 fails on any
  skip.
* Results go to `<perf root>/perf_results.json`.
"""

from __future__ import annotations

import json
import math
import os
import platform
import shutil
import sqlite3
import statistics
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest

from tests.conftest import REPO

pytestmark = pytest.mark.slow

SCALE = 1.0
SEED = 42
AS_OF = "2026-09-01"
REQUESTS = 20
P95_LIMIT_MS = 1000.0
SEARCH_LIMIT_MS = 300.0
# Captured at import: the autouse isolation fixture points SED_DATA_ROOT at a per-test temp dir.
_SESSION_DATA_ROOT = os.environ.get("SED_DATA_ROOT")

# openapi path -> query parameters (defaults; only required parameters are set). Path parameters are filled from ids().
PERF_PARAMS: dict[str, dict[str, Any]] = {
    "/api/health": {},
    "/api/meta": {},
    "/api/nav": {},
    "/api/modules": {},
    "/api/findings": {},
    "/api/imports": {},
    "/api/dq/unmapped": {},
    "/api/alias-targets": {"kind": "app"},
    "/api/runs": {},
    "/api/ops/filters": {},
    "/api/ops/overview": {},
    "/api/ops/attention": {},
    "/api/ops/tickets": {},
    "/api/ops/tickets/volumes": {},
    "/api/ops/tickets/sla": {},
    "/api/ops/tickets/mttr": {},
    "/api/ops/tickets/backlog": {},
    "/api/ops/tickets/{ticket_id}": {},
    "/api/ops/apps": {},
    "/api/ops/apps/{app_id}": {},
    "/api/ops/costs": {},
    "/api/ops/contracts/renewals": {},
    "/api/ops/licenses/utilization": {},
    "/api/ops/vendors/sla-trend": {},
    "/api/sap/overview": {},
    "/api/sap/l3": {},
    "/api/sap/changes": {},
    "/api/sap/idocs": {},
    "/api/delivery/portfolio": {},
    "/api/delivery/projects/{project_id}": {},
    "/api/review/queue": {},
    "/api/runs/{run_id}": {},
    "/api/ai/review-rates": {},
    "/api/reports": {},
    "/api/reports/readiness": {"report": "weekly", "period": "2026-W35"},
    "/api/sources": {},
}
# GET operations that read in-memory state rather than the database: not timed (they would only measure a 412).
NOT_TIMED = {"/api/jobs/{job_id}": "background job status (in-memory lookup)"}
# Full-text searches held to the 300 ms budget: planted multi-word text, a common word, a very broad word, a prefix.
SEARCH_QUERIES = ("interface timeout", "timeout", "error", "time*")


def perf_root() -> Path:
    explicit = os.environ.get("SED_PERF_DATA_ROOT")
    if explicit:
        return Path(explicit)
    if _SESSION_DATA_ROOT:
        return Path(_SESSION_DATA_ROOT) / "perf"
    return Path(tempfile.gettempdir()) / "sed-perf"


def profile_dir(root: Path) -> Path:
    return root / f"scale-{SCALE}-seed-{SEED}"


def apply_pending(conn: sqlite3.Connection) -> list[str]:
    """Apply src/sed/schema/pending/*.sql (index-only, idempotent) inside write_tx."""
    from sed import db

    pending = REPO / "src" / "sed" / "schema" / "pending"
    applied = []
    for path in sorted(pending.glob("*.sql")) if pending.is_dir() else []:
        with db.write_tx(conn):
            for statement in db.split_sql(path.read_text(encoding="utf-8")):
                conn.execute(statement)
        applied.append(path.name)
    return applied


def add_module_data(base: Path, paths: Any) -> list[str]:
    """Generate and import the synthetic data of enabled modules other than ops (idempotent: unchanged files skip)."""
    from datetime import date

    from sed import db, modules, rule_findings
    from sed.ingest.loader import ImportOptions, run_import
    from sed.modules.contract import SynthRequest

    as_of = date.fromisoformat(AS_OF)
    added = []
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("SED_DATA_ROOT", str(base / "sed-data"))
        mp.setenv("SED_CLAUDE_SETTINGS_LOCAL", str(base / "settings.local.json"))
        mp.setenv("SED_CLAUDE_MD", str(base / "CLAUDE.md"))
        for module in modules.enabled(paths):
            if module.key == "ops" or module.synth is None:
                continue
            modules.load_ref(module.synth.generate)(paths, SynthRequest(seed=SEED, as_of=as_of, scale=SCALE))
            result = run_import(paths, ImportOptions(inbox=True))
            if result["summary"]["errors"]:
                raise RuntimeError(f"perf profile: {module.key} import failed: {result['summary']}")
            conn = db.connect(paths.db)
            try:
                rule_findings.refresh(conn, paths, as_of, module.key)
            finally:
                conn.close()
            added.append(module.key)
    return added


def build_or_reuse(root: Path) -> dict[str, Any]:
    """Scale-1.0 profile under `root`, rebuilt only when its marker is missing or different."""
    from sed import db
    from sed.paths import Paths
    from tests.fixtures.ops_profile import build_ops_profile

    base = profile_dir(root)
    marker = base / "perf_profile.json"
    expected = {"scale": SCALE, "seed": SEED, "as_of": AS_OF}
    db_path = base / "sed-data" / "synthetic" / "sed.db"
    reused = False
    stored: dict[str, Any] = {}
    if marker.is_file() and db_path.is_file():
        try:
            stored = json.loads(marker.read_text(encoding="utf-8"))
            reused = {k: stored.get(k) for k in expected} == expected
        except (OSError, ValueError):
            reused = False
    build_seconds = None
    if not reused:
        stored = {}
        if base.exists():
            shutil.rmtree(base)  # only this workstream-owned subfolder, never the root
        start = time.perf_counter()
        build_ops_profile(base, scale=SCALE, seed=SEED)
        build_seconds = round(time.perf_counter() - start, 1)
        stored = {**expected, "build_seconds": build_seconds, "modules": []}
        marker.write_text(json.dumps(stored), encoding="utf-8")
    paths = Paths("synthetic", db_path.parent)
    conn = db.connect(db_path)
    try:
        migrated = db.migrate(conn, db_path, None)["applied"]
        pending = apply_pending(conn)
    finally:
        conn.close()
    added = add_module_data(base, paths)
    if added != stored.get("modules"):
        stored["modules"] = added
        marker.write_text(json.dumps(stored), encoding="utf-8")
    conn = db.connect(db_path)
    try:
        counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("ticket", "task_sla", "finding")}
        indexes = sorted(
            r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'ticket'")
        )
    finally:
        conn.close()
    return {
        "paths": paths,
        "root": str(base),
        "reused": reused,
        "build_seconds": build_seconds,
        "migrated": migrated,
        "pending_applied": pending,
        "modules_added": added,
        "row_counts": counts,
        "ticket_indexes": indexes,
    }


def ids(paths: Any) -> dict[str, str]:
    """Worst-case path parameters: the application with the most tickets, the newest ticket, the first delivery
    project and the newest AI run (routes needing a run are skipped when the profile has none)."""
    from sed import db

    conn = db.connect(paths.db, readonly=True)
    try:
        app_id = conn.execute(
            "SELECT app_id FROM ticket WHERE app_id IS NOT NULL GROUP BY app_id ORDER BY COUNT(*) DESC, app_id LIMIT 1"
        ).fetchone()[0]
        newest = conn.execute("SELECT ticket_id FROM ticket ORDER BY opened_at DESC, ticket_id LIMIT 1").fetchone()
        ticket_id = newest[0]
        project = conn.execute(
            "SELECT project_id FROM delivery_project WHERE is_deleted = 0 ORDER BY project_id LIMIT 1"
        ).fetchone()
        run = conn.execute("SELECT run_id FROM ai_run ORDER BY run_seq DESC LIMIT 1").fetchone()
    finally:
        conn.close()
    return {
        "app_id": quote(app_id, safe=""),
        "ticket_id": quote(ticket_id, safe=""),
        "project_id": quote(project[0], safe="") if project else "PRJ-101",
        "run_id": quote(run[0], safe="") if run else "",
    }


def nearest_rank(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(pct / 100 * len(ordered)) - 1)]


def measure(client: Any, url: str, params: dict[str, Any], n: int = REQUESTS) -> dict[str, Any]:
    times: list[float] = []
    for _ in range(n):
        start = time.perf_counter()
        response = client.get(url, params=params or None)
        elapsed = (time.perf_counter() - start) * 1000
        if response.status_code == 501:
            return {"status": 501, "skipped": True}
        if response.status_code != 200:
            return {"status": response.status_code, "skipped": False, "error": response.text[:300]}
        times.append(elapsed)
    return {
        "status": 200,
        "skipped": False,
        "requests": n,
        "cold_ms": round(times[0], 1),
        "p50_ms": round(statistics.median(times), 1),
        "p95_ms": round(nearest_rank(times, 95), 1),
        "max_ms": round(max(times), 1),
        "bytes": len(response.content),
    }


def run_all(paths: Any) -> list[dict[str, Any]]:
    from tests.fixtures.api import api_client

    client = api_client(paths)
    spec = json.loads((REPO / "contracts" / "openapi.json").read_text(encoding="utf-8"))
    gets = sorted(p for p, item in spec["paths"].items() if "get" in item)
    missing = [p for p in gets if p not in PERF_PARAMS and p not in NOT_TIMED]
    stale = [p for p in [*PERF_PARAMS, *NOT_TIMED] if p not in gets]
    assert not missing, f"GET operations without a PERF_PARAMS entry: {missing}"
    assert not stale, f"PERF_PARAMS entries that are not GET operations in openapi.json: {stale}"
    path_ids = ids(paths)
    results = []
    for path in [p for p in gets if p not in NOT_TIMED]:
        if "{run_id}" in path and not path_ids.get("run_id"):
            results.append({"path": path, "params": {}, "limit_ms": P95_LIMIT_MS, "status": 501, "skipped": True})
            continue
        url = path.format(**path_ids)
        results.append(
            {
                "path": path,
                "params": PERF_PARAMS[path],
                "limit_ms": P95_LIMIT_MS,
                **measure(client, url, PERF_PARAMS[path]),
            }
        )
    for q in SEARCH_QUERIES:
        params = {"q": q}
        results.append(
            {
                "path": "/api/ops/tickets",
                "params": params,
                "limit_ms": SEARCH_LIMIT_MS,
                **measure(client, "/api/ops/tickets", params),
            }
        )
    return results


def test_every_get_meets_the_p95_budget():
    profile = build_or_reuse(perf_root())
    results = run_all(profile["paths"])
    skipped = [r["path"] for r in results if r["skipped"]]
    errors = [r for r in results if not r["skipped"] and r["status"] != 200]
    slow = [r for r in results if r["status"] == 200 and r["p95_ms"] >= r["limit_ms"]]
    report = {
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "python": platform.python_version(),
        "sqlite": sqlite3.sqlite_version,
        "machine": platform.platform(),
        "cpu_count": os.cpu_count(),
        "require_all": os.environ.get("SED_PERF_REQUIRE_ALL") == "1",
        **{k: v for k, v in profile.items() if k != "paths"},
        "skipped": skipped,
        "results": results,
    }
    out = Path(profile["root"]).parent / "perf_results.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    for r in results:
        label = f"{r['path']} {r['params'] or ''}".strip()
        timing = "skipped (501)" if r["skipped"] else f"p95 {r.get('p95_ms')} ms, cold {r.get('cold_ms')} ms"
        print(f"{label:<60} {timing}")
    assert not errors, errors
    assert not slow, slow
    if report["require_all"]:
        assert not skipped, f"SED_PERF_REQUIRE_ALL=1 but these GETs still return 501: {skipped}"
