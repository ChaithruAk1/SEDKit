"""Refactors must not change M1 numbers.

Both baselines were captured on the M1 commit from a pinned-salt synthetic profile:
* weekly 2026-W35 snapshot facts and tables: every baseline key must still exist with an equal value (new keys allowed);
* import content digest per table and column (new tables and columns are ignored).

Rewrite both deliberately with SED_WRITE_BASELINE=1 (and explain why in the commit message).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from sed import db
from sed.reports.snapshot import create_snapshot
from tests.fixtures.ops_profile import baseline_digest

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "synthetic"
WEEKLY = FIXTURES / "weekly_2026-W35_baseline.json"
IMPORT = FIXTURES / "import_digest_baseline.json"
WRITE = os.environ.get("SED_WRITE_BASELINE") == "1"


def _dump(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False, default=str) + "\n"
    path.write_bytes(text.encode("utf-8"))


def _normalise(value):
    return json.loads(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str))


def test_weekly_facts_and_tables_unchanged(ops_profile_rw):
    paths = ops_profile_rw.paths
    conn = db.connect(paths.db)
    try:
        snap = create_snapshot(conn, paths, "weekly", ops_profile_rw.periods["week"])
    finally:
        conn.close()
    current = _normalise({"version": 1, "facts": snap.facts, "tables": snap.tables})
    if WRITE:
        _dump(WEEKLY, current)
    baseline = json.loads(WEEKLY.read_text(encoding="utf-8"))
    missing = [k for k in baseline["facts"] if k not in current["facts"]]
    assert not missing, f"facts removed: {missing}"
    changed = [
        k
        for k, v in baseline["facts"].items()
        if (v["value"], v["unit"]) != (current["facts"][k]["value"], current["facts"][k]["unit"])
    ]
    assert not changed, {k: (baseline["facts"][k]["value"], current["facts"][k]["value"]) for k in changed}
    for name, table in baseline["tables"].items():
        assert name in current["tables"], f"table removed: {name}"
        assert current["tables"][name]["rows"] == table["rows"], f"table rows changed: {name}"


def test_import_digest_unchanged(ops_profile):
    conn = db.connect(ops_profile.paths.db, readonly=True)
    try:
        if WRITE:
            _dump(IMPORT, baseline_digest(conn))
        baseline = json.loads(IMPORT.read_text(encoding="utf-8"))
        current = baseline_digest(conn, baseline)
    finally:
        conn.close()
    missing = sorted(set(baseline["tables"]) - set(current["tables"]))
    assert not missing, f"tables removed: {missing}"
    diffs = {
        t: (v["rows"], current["tables"][t]["rows"])
        for t, v in baseline["tables"].items()
        if v["digest"] != current["tables"][t]["digest"]
    }
    assert not diffs, f"imported content changed (baseline rows, current rows): {diffs}"
