"""Review: reproducible stratified sample, weights, Wilson interval, verdict files, approve-run and reject-run."""

from __future__ import annotations

import json
from collections import Counter

import pytest

from sed.ai.contract import SampleCandidate
from sed.ai.review import approve_run, record_verdicts, reject_run, sample
from sed.ai.runs import draw_sample
from sed.ai.stats import proportional_allocation, weighted_accuracy, wilson_interval
from sed.errors import PreconditionFailed, ValidationFailed
from tests.fake_agent.flow import finished_run, label_all, query, scalar, start
from tests.fake_agent.verdicts import sample_keys, verdicts_for


@pytest.fixture
def finished(ops_profile_rw):
    paths = ops_profile_rw.paths
    plan, summary = finished_run(paths)
    return paths, plan.run_id, summary


def _write(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_wilson_interval_values():
    low, high = wilson_interval(1.0, 30)
    assert round(low, 4) == 0.8865 and high == 1.0
    low, high = wilson_interval(27 / 30, 30)
    assert (round(low, 4), round(high, 4)) == (0.7438, 0.9654)
    assert wilson_interval(0.0, 10)[0] == 0.0
    with pytest.raises(ValueError):
        wilson_interval(0.5, 0)


def test_weighted_accuracy():
    assert weighted_accuracy([(1.0, True), (1.0, False)]) == 0.5
    assert weighted_accuracy([(3.0, True), (1.0, False)]) == 0.75
    assert weighted_accuracy([]) is None


def test_proportional_allocation():
    alloc = proportional_allocation({"a": 50, "b": 30, "c": 15, "d": 5}, 30)
    assert sum(alloc.values()) == 30 and (alloc["a"], alloc["b"]) == (15, 9)
    assert {alloc["c"], alloc["d"]} in ({4, 2}, {5, 1}) and alloc["c"] + alloc["d"] == 6
    tiny = proportional_allocation({"big": 990, "rare": 10}, 30)
    assert tiny["rare"] >= 1 and sum(tiny.values()) == 30
    assert proportional_allocation({"a": 3, "b": 4}, 30) == {"a": 3, "b": 4}
    many = proportional_allocation({f"s{i}": 10 for i in range(40)}, 30)
    assert all(v == 1 for v in many.values())


def test_seeded_sample_is_reproducible(finished):
    paths, run_id, summary = finished
    from sed.modules import handler

    conn_rows = query(
        paths, "SELECT ticket_id, stage, am_category, confidence FROM ai_ticket_label WHERE run_id = ?", run_id
    )
    candidates = [SampleCandidate(r[0], r[1], r[2], r[3]) for r in conn_rows]
    first = draw_sample(candidates, run_id, random_n=30, lowest_n=30)
    second = draw_sample(list(reversed(candidates)), run_id, random_n=30, lowest_n=30)
    as_keys = lambda s: ([(c.item_id, c.stage, w) for c, w in s[0]], [(c.item_id, c.stage) for c in s[1]])  # noqa: E731
    assert as_keys(first) == as_keys(second)
    stored = {
        (r["item_id"], r["stage"], r["weight"])
        for r in query(paths, "SELECT * FROM ai_sample WHERE run_id = ? AND sample_kind = 'random'", run_id)
    }
    assert stored == set(as_keys(first)[0])
    other = draw_sample(candidates, "20260101T000000-triage-batch-ffff", random_n=30, lowest_n=30)
    assert as_keys(other)[0] != as_keys(first)[0]
    assert handler("sed-triage-batch").sample_candidates is not None
    assert summary.counts["random_sample"] == 30 and summary.counts["lowest_conf_sample"] == 30


def test_stratum_weights(finished):
    paths, run_id, _ = finished
    sizes = Counter(r[0] for r in query(paths, "SELECT am_category FROM ai_ticket_label WHERE run_id = ?", run_id))
    rows = query(paths, "SELECT stratum, weight FROM ai_sample WHERE run_id = ? AND sample_kind = 'random'", run_id)
    taken = Counter(r["stratum"] for r in rows)
    assert len(rows) == 30 and set(taken) == set(sizes)
    for r in rows:
        assert r["weight"] == pytest.approx(sizes[r["stratum"]] / taken[r["stratum"]])
    assert sum(r["weight"] for r in rows) == pytest.approx(100)
    lowest = query(
        paths,
        "SELECT s.item_id, l.confidence FROM ai_sample s JOIN ai_ticket_label l ON l.ticket_id = s.item_id "
        "AND l.stage = s.stage AND l.run_id = s.run_id WHERE s.run_id = ? AND s.sample_kind = 'lowest_conf'",
        run_id,
    )
    all_conf = sorted(r[0] for r in query(paths, "SELECT confidence FROM ai_ticket_label WHERE run_id = ?", run_id))
    assert sorted(r["confidence"] for r in lowest) == all_conf[:30]


def test_template_has_null_placeholders_and_nulls_are_not_recorded(finished, tmp_path):
    paths, run_id, _ = finished
    template = tmp_path / "review" / "verdicts.json"
    cards = sample(paths, run_id, template=template)
    data = json.loads(template.read_text(encoding="utf-8"))
    assert set(data) == set(sample_keys(cards)) and all(v is None for v in data.values())
    assert cards["template_items"] == len(data) >= 30 and len(cards["random"]) == 30
    card = cards["random"][0]
    assert set(card) == {"item_id", "stage", "sample_kind", "stratum", "weight", "verdict", "label", "ticket"}
    assert set(card["label"]) == {
        "am_category",
        "am_subcategory",
        "symptom_key",
        "misfiled_as",
        "confidence",
        "rationale",
    }
    assert set(card["ticket"]) == {"number", "kind", "priority", "app", "sn_category", "short_description"}
    assert cards["matrix"] and sum(m["n"] for m in cards["matrix"]) == 100
    untouched = record_verdicts(paths, run_id, template, reviewer="tester")
    assert untouched["recorded"] == 0 and untouched["skipped"] == len(data)
    assert scalar(paths, "SELECT COUNT(*) FROM ai_sample WHERE run_id = ? AND verdict IS NOT NULL", run_id) == 0
    keys = sorted(data)
    half = {k: ("correct" if i % 2 == 0 else None) for i, k in enumerate(keys)}
    result = record_verdicts(paths, run_id, _write(template, half), reviewer="tester")
    assert result["recorded"] == len([v for v in half.values() if v])
    recorded = query(
        paths, "SELECT DISTINCT item_id || '|' || stage FROM ai_sample WHERE run_id = ? AND verdict IS NOT NULL", run_id
    )
    assert {r[0] for r in recorded} == {k for k, v in half.items() if v}
    again = sample(paths, run_id)
    assert {c["verdict"] for c in again["random"]} <= {"correct", None}


def test_unknown_keys_and_bad_values_exit_2_without_writes(finished, tmp_path):
    paths, run_id, _ = finished
    keys = sample_keys(sample(paths, run_id))
    with pytest.raises(ValidationFailed) as exc:
        record_verdicts(
            paths, run_id, _write(tmp_path / "v.json", {keys[0]: "correct", "incident:NOPE|open": "correct"})
        )
    assert exc.value.exit_code == 2 and exc.value.details[0]["loc"] == "incident:NOPE|open"
    for bad in ("wrong", {"verdict": "maybe"}, {"verdict": "incorrect", "colour": "red"}, 3):
        with pytest.raises(ValidationFailed):
            record_verdicts(paths, run_id, _write(tmp_path / "v.json", {keys[0]: bad}))
    with pytest.raises(ValidationFailed):
        record_verdicts(paths, run_id, _write(tmp_path / "v.json", ["not", "an", "object"]))
    assert scalar(paths, "SELECT COUNT(*) FROM ai_sample WHERE run_id = ? AND verdict IS NOT NULL", run_id) == 0


def test_incorrect_verdict_keeps_the_correction(finished, tmp_path):
    paths, run_id, _ = finished
    key = sample_keys(sample(paths, run_id))[0]
    verdict = {"verdict": "incorrect", "category": "defect", "subcategory": "regression"}
    record_verdicts(paths, run_id, _write(tmp_path / "v.json", {key: verdict}), reviewer="tester")
    item_id, stage = key.rsplit("|", 1)
    row = query(
        paths,
        "SELECT verdict, correction_json, reviewer FROM ai_sample WHERE run_id = ? AND item_id = ? AND stage = ?",
        run_id,
        item_id,
        stage,
    )[0]
    assert row["verdict"] == "incorrect" and json.loads(row["correction_json"]) == {
        "category": "defect",
        "subcategory": "regression",
    }
    assert row["reviewer"] == "tester"
    cards = sample(paths, run_id)
    assert any(c["verdict"] == verdict for c in cards["random"] + cards["lowest_confidence"])


def test_approve_requires_every_random_verdict(finished, tmp_path):
    paths, run_id, _ = finished
    cards = sample(paths, run_id)
    verdicts = verdicts_for(cards, incorrect_every=0)
    missing = f"{cards['random'][0]['item_id']}|{cards['random'][0]['stage']}"
    verdicts[missing] = None
    record_verdicts(paths, run_id, _write(tmp_path / "v.json", verdicts), reviewer="tester")
    with pytest.raises(PreconditionFailed) as exc:
        approve_run(paths, run_id, "tester")
    assert exc.value.exit_code == 4 and missing in exc.value.details["missing"]
    assert scalar(paths, "SELECT status FROM ai_run WHERE run_id = ?", run_id) == "completed"


def test_approve_needs_a_completed_run(ops_profile_rw):
    paths = ops_profile_rw.paths
    plan = start(paths, limit=10, batch_size=10)
    label_all(paths, plan)
    with pytest.raises(PreconditionFailed, match="running"):
        approve_run(paths, plan.run_id, "tester")
    with pytest.raises(PreconditionFailed):
        approve_run(paths, "20260101T000000-triage-batch-0000", "tester")
    with pytest.raises(PreconditionFailed):
        sample(paths, plan.run_id)


def test_approve_stores_statistics_and_decision(finished, tmp_path):
    paths, run_id, _ = finished
    cards = sample(paths, run_id)
    verdicts = verdicts_for(cards, incorrect_every=10)
    for card in cards["random"][:2]:
        verdicts[f"{card['item_id']}|{card['stage']}"] = {"verdict": "incorrect", "category": "other"}
    record_verdicts(paths, run_id, _write(tmp_path / "v.json", verdicts), reviewer="tester")
    decisions_before = scalar(paths, "SELECT COUNT(*) FROM review_decision")
    result = approve_run(paths, run_id, "tester", "looks right")
    rows = query(
        paths,
        "SELECT item_id, stage, weight, verdict FROM ai_sample WHERE run_id = ? AND sample_kind = 'random'",
        run_id,
    )
    expected = sum(r["weight"] for r in rows if r["verdict"] == "correct") / sum(r["weight"] for r in rows)
    low, high = wilson_interval(expected, 30)
    run = query(paths, "SELECT * FROM ai_run WHERE run_id = ?", run_id)[0]
    assert run["status"] == "approved" and run["sample_n"] == 30 and result["sample_n"] == 30
    assert run["sample_accuracy"] == pytest.approx(expected) and expected < 1.0
    assert (run["sample_ci_low"], run["sample_ci_high"]) == (pytest.approx(low), pytest.approx(high))
    assert run["reviewed_by"] == "tester" and run["reviewed_at"] and run["review_note"] == "looks right"
    lowest = query(paths, "SELECT verdict FROM ai_sample WHERE run_id = ? AND sample_kind = 'lowest_conf'", run_id)
    assert run["lowest_conf_error_rate"] == pytest.approx(sum(r[0] == "incorrect" for r in lowest) / len(lowest))
    decision = query(paths, "SELECT * FROM review_decision WHERE decision = 'approve_run'")[0]
    corrected = sorted(
        {
            (r["item_id"], r["stage"])
            for r in query(paths, "SELECT * FROM ai_sample WHERE run_id = ? AND verdict = 'incorrect'", run_id)
            if r["correction_json"] and json.loads(r["correction_json"]).get("category")
        }
    )
    assert result["corrections_applied"] == len(corrected) == 2  # only the verdicts that name a category
    assert scalar(paths, "SELECT COUNT(*) FROM review_decision") == decisions_before + 1 + len(corrected)
    assert (decision["target_type"], decision["target_id"], decision["decision"]) == ("run", run_id, "approve_run")
    assert json.loads(decision["payload_json"])["sample_n"] == 30
    manual = query(
        paths, "SELECT l.* FROM ai_ticket_label l JOIN ai_run r ON r.run_id = l.run_id WHERE r.skill = 'manual'"
    )
    assert sorted((r["ticket_id"], r["stage"]) for r in manual) == corrected
    assert {r["am_category"] for r in manual} == {"other"}
    shown = query(paths, "SELECT run_id FROM v_label_current WHERE ticket_id = ? AND stage = ?", *corrected[0])
    assert shown[0]["run_id"].startswith("manual-")  # the human correction wins
    with pytest.raises(PreconditionFailed):
        approve_run(paths, run_id, "tester")


def test_views_pick_the_approved_run(finished, tmp_path):
    paths, run_id, _ = finished
    labelled = {r[0] for r in query(paths, "SELECT ticket_id FROM ai_ticket_label WHERE run_id = ?", run_id)}
    assert scalar(paths, "SELECT COUNT(*) FROM v_ticket WHERE label_run_id = ?", run_id) == 0
    cards = sample(paths, run_id)
    record_verdicts(paths, run_id, _write(tmp_path / "v.json", verdicts_for(cards)), reviewer="tester")
    approve_run(paths, run_id, "tester")
    in_view = {r[0] for r in query(paths, "SELECT ticket_id FROM v_ticket WHERE label_run_id = ?", run_id)}
    assert in_view == labelled
    current = query(paths, "SELECT COUNT(*) FROM v_label_current WHERE run_id = ?", run_id)[0][0]
    assert current == len(labelled)


def test_reject_run(finished, tmp_path):
    paths, run_id, _ = finished
    with pytest.raises(ValidationFailed):
        reject_run(paths, run_id, "tester", "  ")
    result = reject_run(paths, run_id, "tester", "labels are unusable")
    assert result["status"] == "rejected"
    assert scalar(paths, "SELECT status FROM ai_run WHERE run_id = ?", run_id) == "rejected"
    decision = query(paths, "SELECT target_id, decision FROM review_decision ORDER BY decision_id DESC LIMIT 1")[0]
    assert tuple(decision) == (run_id, "reject_run")
    assert scalar(paths, "SELECT COUNT(*) FROM v_ticket WHERE label_run_id = ?", run_id) == 0
    with pytest.raises(PreconditionFailed):
        approve_run(paths, run_id, "tester")
    again = start(paths, limit=100, batch_size=50)
    assert again.plan["items"] == 100  # labels of a rejected run no longer block re-labelling
