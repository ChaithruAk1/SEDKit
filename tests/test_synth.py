"""Generator properties: byte determinism, superset for a later as-of, planted counts independent of --scale."""

from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

import pytest

from sed import bootstrap
from sed.errors import PreconditionFailed
from sed.ingest.readers import read_table
from sed.paths import get_paths
from sed.synth.generate import SynthOptions, generate


def _gen(profile: str, **kw) -> Path:
    paths = get_paths(profile)
    bootstrap.init_profile(paths, new_salt=True, write_claude_settings=False)
    generate(paths, SynthOptions(**{"scale": 0.01, "months": 6, **kw}))
    return paths.data_dir


def _incident_numbers(data_dir: Path) -> set[str]:
    """Incident numbers across the monthly exports (CSV + the XLSX month); the active snapshot is excluded."""
    numbers: set[str] = set()
    for f in (data_dir / "inbox").glob("incident_*"):
        if f.name.startswith("incident_active_"):
            continue
        table = read_table(f)
        key = "number" if "number" in table.columns else "Number"
        idx = table.columns.index(key)
        numbers |= {row[idx] for row in table.rows}
    return numbers


def _truth_counts(data_dir: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    with (data_dir / "ground_truth" / "ticket_truth.csv").open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            if row["pattern"] and row["kind"] == "incident":
                counts[row["pattern"]] = counts.get(row["pattern"], 0) + 1
    return counts


def test_csv_bytes_are_deterministic(data_root: Path):
    a = _gen("eval-a")
    b = _gen("eval-b")
    files_a = sorted(p.name for p in (a / "inbox").glob("*.csv"))
    assert files_a == sorted(p.name for p in (b / "inbox").glob("*.csv"))
    for name in files_a:
        assert (a / "inbox" / name).read_bytes() == (b / "inbox" / name).read_bytes(), name
    ma = json.loads((a / "inbox" / "_manifest.json").read_text(encoding="utf-8"))["files"]
    mb = json.loads((b / "inbox" / "_manifest.json").read_text(encoding="utf-8"))["files"]
    assert {k: v for k, v in ma.items() if k.endswith(".csv")} == {k: v for k, v in mb.items() if k.endswith(".csv")}


def test_later_as_of_is_a_superset(data_root: Path):
    first = _gen("eval-first", as_of=date(2026, 9, 1))
    later = _gen("eval-later", as_of=date(2026, 9, 15), anchor=date(2026, 9, 1))
    before, after = _incident_numbers(first), _incident_numbers(later)
    assert before and before <= after and len(after) > len(before)


def test_planted_counts_do_not_depend_on_scale(data_root: Path):
    small = _truth_counts(_gen("eval-small", scale=0.01))
    bigger = _truth_counts(_gen("eval-bigger", scale=0.05))
    for pattern in ("P1", "P2", "P5", "P6", "P7", "P13"):
        assert small[pattern] == bigger[pattern], pattern
    assert small["P1"] == 230 and small["P7"] == 161  # parent + 160 children
    assert small["P6"] == 700 and small["P13"] == 400


def test_synth_refuses_real_profile(data_root: Path):
    paths = get_paths("real")
    bootstrap.init_profile(paths, new_salt=True, write_claude_settings=False)
    with pytest.raises(PreconditionFailed):
        generate(paths, SynthOptions(scale=0.01, months=3))
