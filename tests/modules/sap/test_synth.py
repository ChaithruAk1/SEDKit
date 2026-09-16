"""SAP synthetic generator: determinism, absolute pattern counts, superset on a later as-of, per-module manifest,
ground truth outside the inbox, and the real-profile guard."""

from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from sed.errors import PreconditionFailed
from sed.ingest import manifest as inbox_manifest
from sed.modules.contract import SynthRequest
from sed.modules.sap.synth import BLOCK, generate
from sed.paths import Paths

AS_OF = date(2026, 9, 1)


def _paths(root: Path, profile: str = "synthetic") -> Paths:
    paths = Paths(profile=profile, data_dir=root / profile)
    paths.ensure()
    return paths


def _gen(root: Path, **kw: Any) -> tuple[Paths, dict[str, Any]]:
    paths = _paths(root)
    result = generate(paths, SynthRequest(**{"seed": 42, "as_of": AS_OF, "scale": 0.02, **kw}))
    return paths, result


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _patterns(paths: Paths) -> dict[str, Any]:
    return json.loads((paths.ground_truth / "sap" / "patterns.json").read_text(encoding="utf-8"))


def test_same_seed_gives_identical_files(tmp_path):
    a, result = _gen(tmp_path / "a")
    b, _ = _gen(tmp_path / "b")
    files = inbox_manifest.load(a.inbox)["sap"]["files"]
    assert len(files) == result["files"] > 3
    for name in files:
        assert (a.inbox / name).read_bytes() == (b.inbox / name).read_bytes(), name
    c, _ = _gen(tmp_path / "c", seed=7)
    assert inbox_manifest.load(c.inbox)["sap"]["files"] != files


def test_file_set_and_counts(tmp_path):
    paths, result = _gen(tmp_path)
    files = sorted(inbox_manifest.load(paths.inbox)["sap"]["files"])
    assert files[0] == "sap_business_apps.csv"
    assert f"sap_incident_active_{AS_OF.isoformat()}.csv" in files
    months = [f for f in files if f.startswith("sap_incident_2")]
    assert months[0] == "sap_incident_2025-03.csv" and months[-1] == "sap_incident_2026-08.csv" and len(months) == 18
    incidents = sum(len(_rows(paths.inbox / f)) for f in months)
    active = _rows(paths.inbox / f"sap_incident_active_{AS_OF.isoformat()}.csv")
    assert result["counts"] == {"application": 2, "incident": incidents, "open_incident": len(active)}
    assert all(not r["resolved_at"] for r in active)
    truth = _rows(paths.ground_truth / "sap" / "ticket_truth.csv")
    assert len(truth) == incidents
    assert {r["area"] for r in truth} >= {"fi_co", "sd", "mm", "ewm", "unassigned"}
    assert {r["landscape"] for r in truth} == {"ecc", "s4"}


def test_planted_patterns_have_absolute_counts(tmp_path):
    small, _ = _gen(tmp_path / "small", scale=0.02)
    large, _ = _gen(tmp_path / "large", scale=0.3)
    p_small, p_large = _patterns(small), _patterns(large)
    for key in ("SP1", "SP2", "SN1", "SC1", "SC2"):
        assert p_small[key] == p_large[key], key
    assert p_small["SP1"]["tickets"] == 3 * 40  # 3 per business day, 8 weeks
    assert p_small["SP2"]["tickets"] == 6 * 2 * 12  # business days 1-2 of 12 months
    assert p_small["SN1"]["tickets"] == 8 * 15  # 3 weeks
    assert 0 < p_small["SP1"]["open_at_as_of"] < p_small["SP1"]["tickets"]
    assert p_small["SP1"]["weeks_to"] == "2026-08-31"  # the Monday of the anchor week
    assert len(p_small["SP2"]["days"]) == 24


def test_planted_tickets_carry_their_markers(tmp_path):
    paths, _ = _gen(tmp_path)
    truth = {r["number"]: r for r in _rows(paths.ground_truth / "sap" / "ticket_truth.csv")}
    rows = [r for f in sorted(paths.inbox.glob("sap_incident_2*.csv")) for r in _rows(f)]
    by_pattern: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        by_pattern.setdefault(truth[row["number"]]["pattern"], []).append(row)
    assert {r["assignment_group"] for r in by_pattern["SP1"]} == {"SAP-EWM-L3"}
    assert {r["assignment_group"] for r in by_pattern["SP2"]} == {"SAP-FICO-L3"}
    assert {r["priority"] for r in by_pattern["SP2"]} == {"2 - High"}
    assert all(r["short_description"].startswith("Period-end close job failed") for r in by_pattern["SP2"])
    assert {r["assignment_group"] for r in by_pattern["SN1"]} == {"SAP-SD-L3"}
    assert all(r["resolved_at"] for r in by_pattern["SN1"])
    assert {(r["category"], r["assignment_group"]) for r in by_pattern["SC1"]} == {("SAP", "IT-SERVICE-DESK-L1")}
    assert all(r["u_sap_component"] and r["category"] != "SAP" for r in by_pattern["SC2"])
    assert all(not r["u_sap_component"] for p in ("", "SP1", "SP2", "SN1", "SC1") for r in by_pattern[p])
    assert {r["business_service"] for r in rows} == {"SAP ECC", "SAP S/4HANA"}


def test_number_blocks_stay_clear_of_ops_blocks(tmp_path):
    from sed.synth.tickets import BLOCK as OPS_BLOCK

    ops_used = {v for k, v in OPS_BLOCK.items() if k != "SPECIAL"}
    assert max(ops_used) + 1000 <= min(BLOCK.values()) and max(BLOCK.values()) + 1000 <= OPS_BLOCK["SPECIAL"]
    paths, _ = _gen(tmp_path, scale=5.0, months=3)
    suffixes = [int(r["number"][-4:]) for f in paths.inbox.glob("sap_incident_2*.csv") for r in _rows(f)]
    assert min(suffixes) >= BLOCK["background"] and max(suffixes) < OPS_BLOCK["SPECIAL"]
    background = [s for s in suffixes if s < BLOCK["SP1"]]
    assert max(background) < BLOCK["SP1"] - 900  # background never runs into the pattern blocks


def test_later_as_of_is_a_superset(tmp_path):
    first, _ = _gen(tmp_path / "first", scale=0.5)
    later, _ = _gen(tmp_path / "later", scale=0.5, as_of=date(2026, 9, 15), anchor=AS_OF)
    before = {r["number"]: r for f in first.inbox.glob("sap_incident_2*.csv") for r in _rows(f)}
    after = {r["number"]: r for f in later.inbox.glob("sap_incident_2*.csv") for r in _rows(f)}
    assert set(before) < set(after)
    for number, row in before.items():
        assert after[number]["opened_at"] == row["opened_at"]
        assert after[number]["sys_updated_on"] >= row["sys_updated_on"]
    stable = ("tickets", "weeks_from", "weeks_to")  # open_at_as_of drops as tickets resolve after the first as-of
    assert {k: _patterns(first)["SP1"][k] for k in stable} == {k: _patterns(later)["SP1"][k] for k in stable}
    assert _patterns(first)["SP1"]["open_at_as_of"] >= _patterns(later)["SP1"]["open_at_as_of"]


def test_manifest_section_leaves_other_modules_alone(tmp_path):
    paths = _paths(tmp_path)
    (paths.inbox / "incident_2026-08.csv").write_text("number\n", encoding="utf-8")
    inbox_manifest.save_section(paths.inbox, "ops", {"files": {"incident_2026-08.csv": "sha-ops"}})

    generate(paths, SynthRequest(seed=42, as_of=AS_OF, scale=0.02))
    sections = inbox_manifest.load(paths.inbox)
    assert set(sections) == {"ops", "sap"}
    manifest = json.loads((paths.inbox / "_manifest.json").read_text(encoding="utf-8"))
    assert manifest["data_class"] == "synthetic"
    assert set(manifest["files"]) == {"incident_2026-08.csv", *sections["sap"]["files"]}
    assert sections["sap"]["seed"] == 42 and sections["sap"]["as_of"] == AS_OF.isoformat()

    stray = paths.inbox / "sap_incident_1999-01.csv"
    stray.write_text("number\n", encoding="utf-8")  # not listed: a regenerate never deletes unlisted files
    generate(paths, SynthRequest(seed=42, as_of=date(2026, 8, 3), scale=0.02))
    sections = inbox_manifest.load(paths.inbox)
    assert (paths.inbox / "incident_2026-08.csv").is_file() and stray.is_file()
    assert sections["ops"] == {"files": {"incident_2026-08.csv": "sha-ops"}}
    assert "sap_incident_active_2026-09-01.csv" not in sections["sap"]["files"]
    assert not (paths.inbox / "sap_incident_active_2026-09-01.csv").exists()


def test_ground_truth_stays_outside_the_inbox(tmp_path):
    paths, result = _gen(tmp_path)
    truth = paths.ground_truth / "sap"
    assert Path(result["ground_truth"]) == truth
    assert {p.name for p in truth.iterdir()} == {"ticket_truth.csv", "patterns.json", "pii_injections.json"}
    assert not any("truth" in p.name or "pattern" in p.name for p in paths.inbox.iterdir())
    pii = json.loads((truth / "pii_injections.json").read_text(encoding="utf-8"))
    assert any("@example.com" in v for v in pii) and any(v.startswith("+33 6 ") for v in pii)


def test_refuses_the_real_profile(tmp_path):
    with pytest.raises(PreconditionFailed, match="never on the real profile"):
        generate(_paths(tmp_path, "real"), SynthRequest())


def test_as_of_before_anchor_is_refused(tmp_path):
    with pytest.raises(PreconditionFailed, match="on or after the pattern anchor"):
        generate(_paths(tmp_path), SynthRequest(as_of=date(2026, 8, 1), anchor=AS_OF))
