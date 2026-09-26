"""Recording: one entry per event, attempts and outcomes tied together, the dashboard note on command-line entries,
sign-in events, and refusing an action whose entry cannot be written."""

from __future__ import annotations

import json

import pytest

from sed.api import serve
from sed.audit import store
from sed.audit.record import (
    AuditUnavailable,
    audit_path,
    auth_recorder,
    clear_dashboard_session,
    dashboard_signed_in,
    record,
    start,
    write_dashboard_session,
)
from sed.auth.actor import command_line_actor, developer_actor
from sed.auth.identity import Identity
from sed.auth.runtime import AuthEvent
from sed.paths import get_paths
from tests.fixtures.auth import OWNER, auth_settings, query, sign_in_runtime


def entries(paths) -> list[dict]:
    path = audit_path(paths)
    if not path.is_file():
        return []
    conn = store.connect(path, readonly=True)
    try:
        rows = [dict(r) for r in conn.execute("SELECT * FROM audit_entry ORDER BY seq")]
    finally:
        conn.close()
    for row in rows:
        row["detail"] = json.loads(row["detail"]) if row["detail"] else {}
        row["changes"] = json.loads(row["changes"]) if row["changes"] else None
    return rows


@pytest.fixture
def paths(data_root, monkeypatch):
    monkeypatch.setenv("USERNAME", "synthetic-user")
    found = get_paths("synthetic")
    found.ensure()
    return found


def test_an_entry_says_who_how_and_what(paths):
    signed_in = Identity("google", "s", emails=(OWNER,), name="Owner", email=OWNER).actor()
    detail = {"rows": 3}
    record(paths, signed_in, "download", summary="Downloaded 3 tickets.", target_type="ticket_workbook", detail=detail)
    record(paths, developer_actor(), "download", summary="Downloaded 1 ticket.")
    first, second = entries(paths)
    assert (first["actor"], first["method"], first["verified"], first["channel"]) == (OWNER, "google", 1, "dashboard")
    assert first["detail"] == {"rows": 3} and first["target_type"] == "ticket_workbook" and first["outcome"] == "done"
    assert (second["actor"], second["method"], second["verified"]) == ("windows:synthetic-user", "developer_mode", 0)
    assert first["profile"] == "synthetic" and first["at"].endswith("Z")


def test_an_attempt_and_its_outcome_share_one_correlation(paths):
    with start(paths, command_line_actor(), "clear", summary="Clear the data imported from Jira") as attempt:
        attempt.done("Clear the data imported from Jira: 12 rows deleted.", detail={"rows": 12})
    with pytest.raises(RuntimeError), start(paths, command_line_actor(), "pull", summary="Pull from Jira"):
        raise RuntimeError("network down")
    started, done, started2, failed = entries(paths)
    assert [e["outcome"] for e in (started, done, started2, failed)] == ["started", "done", "started", "failed"]
    assert started["correlation_id"] == done["correlation_id"] != started2["correlation_id"] == failed["correlation_id"]
    assert failed["summary"] == "Pull from Jira: failed (unexpected RuntimeError)"  # never the error's text
    assert "network down" not in json.dumps(failed)


def test_an_attempt_records_its_outcome_once(paths):
    attempt = start(paths, command_line_actor(), "import", summary="Import x.csv")
    attempt.done()
    attempt.failed("too late")
    assert [e["outcome"] for e in entries(paths)] == ["started", "done"]


def test_command_line_entries_note_who_was_signed_in_to_the_dashboard(paths):
    record(paths, command_line_actor(), "import", summary="Import a.csv")
    serve.acquire_lock(paths, port=0)  # this process plays the running `sed serve`
    try:
        signed_in = Identity("google", "s", emails=(OWNER,), email=OWNER).actor()
        write_dashboard_session(paths, signed_in, expires_at=4_102_444_800.0)
        assert dashboard_signed_in(paths) == OWNER
        record(paths, command_line_actor(), "import", summary="Import b.csv")
        write_dashboard_session(paths, signed_in, expires_at=1.0)  # expired
        assert dashboard_signed_in(paths) is None
    finally:
        serve.release_lock(paths)
        clear_dashboard_session(paths)
    first, second = entries(paths)
    assert first["detail"] == {"dashboard_signed_in": None} and second["detail"] == {"dashboard_signed_in": OWNER}
    assert (second["actor"], second["channel"], second["verified"]) == ("windows:synthetic-user", "command_line", 0)


def test_an_action_the_trail_cannot_record_is_refused(paths, monkeypatch):
    def broken(path, row, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(store, "append", broken)
    with pytest.raises(AuditUnavailable, match="did not do it"):
        record(paths, command_line_actor(), "download", summary="Downloaded 1 ticket.")
    with pytest.raises(AuditUnavailable):
        start(paths, command_line_actor(), "clear", summary="Clear the data imported from Jira")


def test_unknown_actions_are_a_programming_error(paths):
    with pytest.raises(ValueError, match="unknown audit action"):
        record(paths, command_line_actor(), "teleport", summary="?")


def test_sign_in_events_go_on_the_trail(paths):
    on_event = auth_recorder(paths)
    owner = Identity("google", "s", emails=(OWNER,), name="Owner", email=OWNER)
    on_event(AuthEvent("sign_in", "google", owner, "owner is on the list.", {"expires_at": 4_102_444_800.0}))
    stranger = Identity("github", "7", emails=("stranger@example.org",), login="octo")
    on_event(AuthEvent("sign_in_refused", "github", stranger, "stranger@example.org is not on the list."))
    on_event(AuthEvent("sign_in_failed", "microsoft", None, "Sign-in was cancelled.", {"error": "access_denied"}))
    on_event(AuthEvent("session_replaced", "google", owner, "Session ended: someone else signed in."))
    on_event(AuthEvent("sign_out", "google", owner, "Signed out."))
    rows = entries(paths)
    assert [(r["action"], r["outcome"], r["actor"], r["verified"]) for r in rows] == [
        ("sign_in", "done", OWNER, 1),
        ("sign_in", "refused", "stranger@example.org", 1),
        ("sign_in", "failed", "unknown", 0),
        ("sign_out", "done", OWNER, 1),
        ("sign_out", "done", OWNER, 1),
    ]
    assert rows[0]["detail"]["expires_at"] == "2100-01-01T00:00:00Z" and rows[2]["detail"]["error"] == "access_denied"
    assert not (paths.audit / "dashboard-session.json").exists()  # removed by the sign-out


def test_a_sign_in_is_refused_when_the_trail_cannot_record_it(paths, monkeypatch):
    runtime, fake, _ = sign_in_runtime(auth_settings())
    runtime.on_event = auth_recorder(paths)
    url, binding = runtime.start_redirect("google", "http://127.0.0.1:8123")
    params = query(url)
    fake.google(params["nonce"])
    monkeypatch.setattr(store, "append", lambda path, row, **kwargs: (_ for _ in ()).throw(OSError("disk full")))
    outcome = runtime.finish_redirect(
        state=params["state"], code="code-1", error=None, binding=binding, here="http://127.0.0.1:8123"
    )
    assert outcome.status == "failed" and outcome.token is None and "audit trail" in outcome.message


def test_failure_reasons_never_carry_the_errors_text(paths):
    from sed.audit.record import failure_reason
    from sed.errors import PreconditionFailed, ValidationFailed

    assert failure_reason(ValidationFailed("row 1: Fictional Person cannot reach the VPN")) == "the input was refused"
    assert (
        failure_reason(PreconditionFailed("C:/Users/someone/secret/pii_salt.txt is missing"))
        == "a condition was not met"
    )
    assert failure_reason(KeyError("ticket text")) == "unexpected KeyError"


def test_import_details_keep_names_and_counts_only():
    from sed.audit.actions import _import_detail

    result = {
        "files": [
            {
                "file": "C:/data/inbox/incident_2026-09.csv",
                "status": "error",
                "error": {
                    "kind": "validation",
                    "message": "row: Fictional Person cannot log in",
                    "details": {"columns": ["Fictional Person"]},
                },
            },
            {"file": "change.csv", "status": "completed", "rows_read": 4, "rows_rejected": 0},
        ],
        "summary": {"imported": 1, "errors": 1, "rows_read": 4},
    }
    detail = _import_detail(result)
    assert detail["files"] == [
        {"status": "error", "file": "incident_2026-09.csv", "error": "validation"},
        {"status": "completed", "rows_read": 4, "rows_rejected": 0, "file": "change.csv"},
    ]
    assert "Fictional Person" not in json.dumps(detail) and "C:/data" not in json.dumps(detail)


def test_an_entry_written_after_the_data_folder_moved_lands_in_the_new_place(paths, tmp_path):
    from sed.paths import MOVED_MARKER, Paths

    attempt = start(paths, command_line_actor(), "import", summary="Import a.csv")
    new_root = tmp_path / "new-root"
    (paths.data_dir.parent / MOVED_MARKER).write_text(json.dumps({"moved_to": str(new_root)}), encoding="utf-8")
    attempt.done()
    moved = Paths(paths.profile, new_root / paths.profile)
    assert [e["outcome"] for e in entries(paths)] == ["started"]
    assert [e["outcome"] for e in entries(moved)] == ["done"]


def test_the_dashboard_note_is_a_convenience_and_ignores_a_reused_process_number(paths, monkeypatch):
    import sed.audit.record as rec

    signed_in = Identity("google", "s", emails=(OWNER,), email=OWNER).actor()
    serve.acquire_lock(paths, port=0)
    try:
        write_dashboard_session(paths, signed_in, expires_at=4_102_444_800.0)
        note = json.loads((paths.audit / "dashboard-session.json").read_text(encoding="utf-8"))
        assert note["pid"] and "started" in note
        note["started"] = "another-process"  # the same number, handed to a different process since
        (paths.audit / "dashboard-session.json").write_text(json.dumps(note), encoding="utf-8")
        assert dashboard_signed_in(paths) is None
        monkeypatch.setattr(rec.os, "replace", lambda *a: (_ for _ in ()).throw(PermissionError("in use")))
        write_dashboard_session(paths, signed_in, expires_at=4_102_444_800.0)  # logged, never raised
    finally:
        serve.release_lock(paths)
        clear_dashboard_session(paths)
