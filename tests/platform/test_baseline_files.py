"""Baseline JSON files are excluded from detect-secrets, so their schema is validated strictly here:
nothing but digests of synthetic data may live in them."""

from __future__ import annotations

import json
import re
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "synthetic"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")


def test_only_known_baseline_files():
    names = sorted(p.name for p in FIXTURES.glob("*_baseline.json"))
    assert names == ["import_digest_baseline.json", "weekly_2026-W35_baseline.json"]


def test_import_digest_schema():
    data = json.loads((FIXTURES / "import_digest_baseline.json").read_text(encoding="utf-8"))
    assert set(data) == {"version", "tables"} and data["version"] == 1
    assert data["tables"]
    for table, entry in data["tables"].items():
        assert IDENT.match(table), table
        assert set(entry) == {"columns", "rows", "digest"}, table
        assert all(isinstance(c, str) and IDENT.match(c) for c in entry["columns"]), table
        assert isinstance(entry["rows"], int) and entry["rows"] >= 0
        assert HEX64.match(entry["digest"]), table


def test_weekly_baseline_schema():
    data = json.loads((FIXTURES / "weekly_2026-W35_baseline.json").read_text(encoding="utf-8"))
    assert set(data) == {"version", "facts", "tables"} and data["version"] == 1
    for key, fact in data["facts"].items():
        assert re.match(r"^[a-z0-9_.]+$", key), key
        assert set(fact) == {"value", "unit", "label", "definition"}, key
    for name, table in data["tables"].items():
        assert IDENT.match(name), name
        assert set(table) == {"title", "columns", "rows"}, name
        assert isinstance(table["rows"], list)
    text = json.dumps(data)
    assert not re.search(r"[0-9a-f]{32,}", text), "weekly baseline must not contain long hex strings"
