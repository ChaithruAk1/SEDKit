"""`sed ai ingest`: whole-batch validation, out/ containment, packet sha, refs from the DB and idempotency."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sed.ai.ingest import ingest_file
from sed.ai.runs import finish_run
from sed.errors import PreconditionFailed, ValidationFailed
from tests.fake_agent.flow import SKILL, context_text, fake_output, query, scalar, start
from tests.fake_agent.triage import label_packet, write_output


@pytest.fixture
def running(ops_profile_rw):
    paths = ops_profile_rw.paths
    return paths, start(paths, limit=20, batch_size=10)


def _labels(paths, run_id) -> int:
    return scalar(paths, "SELECT COUNT(*) FROM ai_ticket_label WHERE run_id = ?", run_id)


def _batch(paths, run_id, name="batch_0001"):
    return query(paths, "SELECT * FROM ai_batch WHERE batch_id = ?", f"{run_id}/{name}")[0]


def test_valid_batch_ingests(running):
    paths, plan = running
    result = ingest_file(paths, plan.run_id, fake_output(plan, plan.inputs[0]))
    assert result["status"] == "ingested" and result["items"] == 10 and result["batch"] == "batch_0001"
    assert set(result) == {"run_id", "batch", "status", "items", "low_confidence", "warnings"}
    assert _labels(paths, plan.run_id) == 10
    batch = _batch(paths, plan.run_id)
    assert batch["status"] == "ingested" and batch["output_sha"] and batch["attempts"] == 1
    assert scalar(paths, "SELECT model_reported FROM ai_run WHERE run_id = ?", plan.run_id) == "fake-agent-1"
    assert _batch(paths, plan.run_id, "batch_0002")["status"] == "planned"


@pytest.mark.parametrize(
    ("mode", "loc_part"),
    [
        ("missing_ref", "missing"),
        ("dup_ref", "times"),
        ("invented_ref", "invented"),
        ("bad_category", "am_category"),
        ("bad_subcategory", "am_subcategory"),
        ("bad_confidence", "confidence"),
    ],
)
def test_defective_batches_are_rejected_whole(running, mode, loc_part):
    paths, plan = running
    out = fake_output(plan, plan.inputs[0], mode=mode)
    with pytest.raises(ValidationFailed) as exc:
        ingest_file(paths, plan.run_id, out)
    assert exc.value.exit_code == 2 and exc.value.message.startswith("Batch rejected: ")
    details = exc.value.details
    assert details and all(set(d) == {"loc", "msg", "ref"} for d in details)
    assert any(loc_part in d["loc"] + d["msg"] for d in details), details
    assert all(d["ref"] for d in details)
    assert _labels(paths, plan.run_id) == 0
    batch = _batch(paths, plan.run_id)
    assert batch["status"] == "planned" and batch["attempts"] == 1 and json.loads(batch["last_errors_json"])


def test_retry_after_rejection_ingests(running):
    paths, plan = running
    with pytest.raises(ValidationFailed):
        ingest_file(paths, plan.run_id, fake_output(plan, plan.inputs[0], mode="bad_category"))
    assert ingest_file(paths, plan.run_id, fake_output(plan, plan.inputs[0]))["status"] == "ingested"
    batch = _batch(paths, plan.run_id)
    assert batch["attempts"] == 2 and batch["last_errors_json"] is None


def test_file_outside_out_dir_is_rejected(running):
    paths, plan = running
    out = fake_output(plan, plan.inputs[0])
    run_dir = Path(plan.run_dir)
    for elsewhere in (
        run_dir / "batch_0001.json",
        run_dir / "in" / "batch_0001.json",
        run_dir.parent / "batch_0001.json",
    ):
        shutil.copyfile(out, elsewhere)
        with pytest.raises(ValidationFailed, match="Batch rejected"):
            ingest_file(paths, plan.run_id, elsewhere)
    traversal = f"{plan.out_dir}/../batch_0001.json"
    with pytest.raises(ValidationFailed):
        ingest_file(paths, plan.run_id, traversal)
    assert _labels(paths, plan.run_id) == 0


def test_output_of_another_runs_folder_is_rejected(ops_profile_rw):
    paths = ops_profile_rw.paths
    first = start(paths, limit=10, batch_size=10)
    second = start(paths, limit=10, batch_size=10)
    out = fake_output(first, first.inputs[0])
    with pytest.raises(ValidationFailed):
        ingest_file(paths, second.run_id, out)
    assert _labels(paths, second.run_id) == 0


def test_unknown_batch_stem_is_rejected(running):
    paths, plan = running
    stray = Path(plan.out_dir) / "batch_0099.json"
    shutil.copyfile(fake_output(plan, plan.inputs[0]), stray)
    with pytest.raises(ValidationFailed, match="Batch rejected"):
        ingest_file(paths, plan.run_id, stray)


def test_invalid_json_is_rejected(running):
    paths, plan = running
    Path(plan.inputs[0].out).write_text("{not json", encoding="utf-8")
    with pytest.raises(ValidationFailed) as exc:
        ingest_file(paths, plan.run_id, plan.inputs[0].out)
    assert "invalid JSON" in exc.value.details[0]["msg"]


def test_tampered_packet_is_rejected(running):
    paths, plan = running
    out = fake_output(plan, plan.inputs[0])
    packet = Path(plan.inputs[0].packet)
    packet.write_bytes(packet.read_bytes().replace(b'"T001"', b'"T001","note":"x"', 1))
    with pytest.raises(ValidationFailed) as exc:
        ingest_file(paths, plan.run_id, out)
    assert exc.value.details[0]["loc"] == "packet"
    assert _labels(paths, plan.run_id) == 0


def test_path_with_backslashes_ingests(running):
    paths, plan = running
    out = fake_output(plan, plan.inputs[0])
    result = ingest_file(paths, plan.run_id, str(out).replace("/", "\\"))
    assert result["status"] == "ingested"


def test_unknown_run_exits_4(running):
    paths, plan = running
    out = fake_output(plan, plan.inputs[0])
    with pytest.raises(PreconditionFailed) as exc:
        ingest_file(paths, "20260101T000000-triage-batch-0000", out)
    assert exc.value.exit_code == 4


def test_finished_run_accepts_no_output(running):
    paths, plan = running
    out = fake_output(plan, plan.inputs[0])
    finish_run(paths, plan.run_id)
    with pytest.raises(PreconditionFailed):
        ingest_file(paths, plan.run_id, out)


def test_reingesting_the_same_file_is_unchanged(running):
    paths, plan = running
    out = fake_output(plan, plan.inputs[0])
    assert ingest_file(paths, plan.run_id, out)["status"] == "ingested"
    again = ingest_file(paths, plan.run_id, out)
    assert again["status"] == "unchanged" and again["items"] == 10
    assert _labels(paths, plan.run_id) == 10 and _batch(paths, plan.run_id)["attempts"] == 1


def test_changed_output_reingests_idempotently(running):
    paths, plan = running
    document = label_packet(Path(plan.inputs[0].packet).read_text(encoding="utf-8"), context_text(plan))
    write_output(plan.inputs[0].out, document)
    ingest_file(paths, plan.run_id, plan.inputs[0].out)
    document["items"][0]["am_category"] = "other"
    document["items"][0]["am_subcategory"] = None
    write_output(plan.inputs[0].out, document)
    assert ingest_file(paths, plan.run_id, plan.inputs[0].out)["status"] == "ingested"
    assert _labels(paths, plan.run_id) == 10
    item = scalar(
        paths, "SELECT item_id FROM ai_batch_item WHERE batch_id = ? AND ref = 'T001'", f"{plan.run_id}/batch_0001"
    )
    row = query(paths, "SELECT am_category FROM ai_ticket_label WHERE run_id = ? AND ticket_id = ?", plan.run_id, item)
    assert row[0][0] == "other"


def test_refs_map_through_the_database_not_the_packet(running):
    paths, plan = running
    batch_id = f"{plan.run_id}/batch_0001"
    mapping = {
        r["ref"]: (r["item_id"], r["input_hash"])
        for r in query(paths, "SELECT * FROM ai_batch_item WHERE batch_id = ?", batch_id)
    }
    packet = Path(plan.inputs[0].packet)
    original = packet.read_bytes()
    lines = original.decode("utf-8").splitlines()
    swapped = [lines[1].replace('"T002"', '"T001"'), lines[0].replace('"T001"', '"T002"'), *lines[2:]]
    packet.write_bytes(("\n".join(swapped) + "\n").encode("utf-8"))
    document = label_packet(packet.read_text(encoding="utf-8"), context_text(plan))
    write_output(plan.inputs[0].out, document)
    with pytest.raises(ValidationFailed):
        ingest_file(paths, plan.run_id, plan.inputs[0].out)
    assert _labels(paths, plan.run_id) == 0
    packet.write_bytes(original)
    by_ref = {i["ref"]: i["am_category"] for i in document["items"]}
    assert ingest_file(paths, plan.run_id, plan.inputs[0].out)["status"] == "ingested"
    for ref, (item_id, input_hash) in mapping.items():
        row = query(
            paths,
            "SELECT am_category, input_hash, batch_id FROM ai_ticket_label WHERE run_id = ? AND ticket_id = ?",
            plan.run_id,
            item_id,
        )[0]
        assert (row["am_category"], row["input_hash"], row["batch_id"]) == (by_ref[ref], input_hash, batch_id)


def test_symptom_key_normalised_with_warning(running):
    paths, plan = running
    document = label_packet(Path(plan.inputs[0].packet).read_text(encoding="utf-8"), context_text(plan))
    document["items"][0]["symptom_key"] = "Queue Messages STUCK!"
    document["items"][1]["rationale"] = " ".join(["word"] * 30)
    write_output(plan.inputs[0].out, document)
    result = ingest_file(paths, plan.run_id, plan.inputs[0].out)
    assert any("normalised to 'queue_messages_stuck'" in w for w in result["warnings"])
    assert any("longer than 25 words" in w for w in result["warnings"])


def test_cli_ingest_prints_the_error_envelope(running):
    from sed.cli import app

    paths, plan = running
    out = fake_output(plan, plan.inputs[0], mode="invented_ref")
    argv = [
        "ai",
        "ingest",
        plan.run_id,
        str(out),
        "--data-dir",
        str(paths.data_dir),
        "--profile",
        "synthetic",
        "--json",
    ]
    result = CliRunner().invoke(app, argv)
    assert result.exit_code == 2
    lines = result.stdout.strip().splitlines()
    payload = json.loads(lines[0])
    assert len(lines) == 1 and payload["ok"] is False
    assert payload["error"]["kind"] == "validation" and payload["error"]["message"] == "Batch rejected: 1 errors"
    assert payload["error"]["details"] == [
        {"loc": "items", "msg": "ref is not in this batch (invented)", "ref": "T999"}
    ]
    ok = CliRunner().invoke(app, [*argv[:3], str(fake_output(plan, plan.inputs[0])), *argv[4:]])
    assert ok.exit_code == 0 and json.loads(ok.stdout)["ok"] is True
    assert SKILL == "sed-triage-batch"


def test_non_finite_numbers_are_rejected(running):
    paths, plan = running
    out = fake_output(plan, plan.inputs[0])
    text = out.read_text(encoding="utf-8")
    first = json.loads(text)["items"][0]["confidence"]
    out.write_bytes(text.replace(f'"confidence": {first}', '"confidence": NaN', 1).encode("utf-8"))
    with pytest.raises(ValidationFailed) as exc:
        ingest_file(paths, plan.run_id, out)
    assert "NaN" in exc.value.details[0]["msg"]
    assert _labels(paths, plan.run_id) == 0
