"""`sed ai packet`: every run file with its size and manifest check, contents on request, changed files flagged."""

from __future__ import annotations

from pathlib import Path

import pytest

from sed.ai.audit import packet_audit
from sed.errors import PreconditionFailed
from tests.fake_agent.flow import start


def test_packet_audit_lists_run_files_and_flags_changes(ops_profile_rw):
    paths = ops_profile_rw.paths
    plan = start(paths, limit=40, batch_size=20)
    result = packet_audit(paths, plan.run_id)
    assert [b["batch"] for b in result["batches"]] == ["batch_0001", "batch_0002"]
    assert result["all_files_match_manifest"] and result["total_chars"] > 1000
    assert [f["file"] for f in result["context"]] == ["in/context.md"]
    assert all("text" not in f for b in result["batches"] for f in b["files"])

    one = packet_audit(paths, plan.run_id, "batch_0002", text=True)
    packet = one["batches"][0]["files"][0]
    assert packet["file"] == "in/batch_0002.jsonl" and packet["lines"] == 20
    assert packet["text"] == Path(plan.inputs[1].packet).read_text(encoding="utf-8")

    Path(plan.inputs[0].packet).write_text("tampered\n", encoding="utf-8")
    tampered = packet_audit(paths, plan.run_id, "batch_0001")
    assert tampered["batches"][0]["files"][0]["sha256_ok"] is False and not tampered["all_files_match_manifest"]
    with pytest.raises(PreconditionFailed, match="no batch 'batch_0009'"):
        packet_audit(paths, plan.run_id, "batch_0009")
    with pytest.raises(PreconditionFailed):
        packet_audit(paths, "20260101T000000-nope-0000")
