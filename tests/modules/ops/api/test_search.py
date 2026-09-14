"""Ticket search: FTS5 over scrubbed text with a sanitizer that makes every operator literal, filters, sort whitelist
and pagination with totals."""

from __future__ import annotations

import json
from typing import Any

import pytest

from sed.modules.ops.api_models import TicketPage
from sed.modules.ops.queries.search import MAX_TOKENS, fts_query

URL = "/api/ops/tickets"


def page(client: Any, **params: Any) -> dict[str, Any]:
    r = client.get(URL, params=params)
    assert r.status_code == 200, (params, r.text[:500])
    body = r.json()
    TicketPage.model_validate(body)
    return body


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (None, None),
        ("", None),
        ("   \t\n ", None),
        ("*", None),
        ("timeout", '"timeout"'),
        ("time*", '"time"*'),
        ("interface  timeout", '"interface" "timeout"'),
        ('say "hi"', '"say" """hi"""'),
        ("AND OR NOT NEAR", '"AND" "OR" "NOT" "NEAR"'),
        ("NEAR(a b)", '"NEAR(a" "b)"'),
        ("short_description:x", '"short_description:x"'),
        ("a*b*", '"a*b"*'),
        ("^start -minus +plus", '"^start" "-minus" "+plus"'),
    ],
)
def test_fts_query_sanitizer(text, expected):
    assert fts_query(text) == expected


def test_fts_query_keeps_at_most_ten_tokens():
    text = " ".join(f"w{i}" for i in range(MAX_TOKENS + 5))
    assert fts_query(text) == " ".join(f'"w{i}"' for i in range(MAX_TOKENS))


def test_planted_p1_text_is_found(client, ops_profile):
    truth = json.loads((ops_profile.ground_truth / "patterns.json").read_text(encoding="utf-8"))
    body = page(client, q="interface timeout", page_size=200)
    assert body["total"] >= 50
    planted = [t for t in body["items"] if t["app_name"] == truth["P1"]["app"]]
    assert len(planted) >= 50, "the P1 incidents (absolute counts at any scale) are found by their text"
    scoped = page(client, q="interface timeout", app=planted[0]["app_id"])
    assert len(planted) <= scoped["total"] <= body["total"]
    prefix = page(client, q="timeo*")
    assert prefix["total"] >= page(client, q="timeout")["total"] > 0


@pytest.mark.parametrize("text", ['"', "*", "AND", "(", "NEAR", "", "   ", ")", '""', "OR", "NOT", "-", "^", ":", "'"])
def test_operator_and_empty_inputs_never_fail(client, text):
    body = page(client, q=text)
    assert body["total"] >= 0 and body["page"] == 1


def test_operators_are_literals(client):
    base = page(client, q="timeout")["total"]
    assert base > 0
    # As FTS5 operators these would widen or keep the result; as literal words they must all be present.
    assert page(client, q="timeout OR zzqqxxvv")["total"] == 0
    assert page(client, q="NEAR(timeout interface)")["total"] <= base
    assert page(client, q="short_description:timeout")["total"] == 0
    assert page(client, q='"timeout')["total"] == base, "a stray quote is part of the phrase, not syntax"
    assert page(client, q="'; DROP TABLE ticket; --")["total"] == 0
    assert page(client, q="")["total"] == page(client)["total"]
    assert page(client, q="   ")["total"] == page(client)["total"]


def test_search_combines_with_filters(client):
    everything = page(client, q="timeout", page_size=200)
    incidents = page(client, q="timeout", kind="incident", page_size=200)
    assert incidents["total"] <= everything["total"]
    assert {t["kind"] for t in incidents["items"]} <= {"incident"}
    p2 = page(client, q="timeout", priority=[1, 2], page_size=200)
    assert {t["priority"] for t in p2["items"]} <= {1, 2}
    open_only = page(client, open="true", page_size=200)
    assert open_only["total"] > 0 and all(t["is_open"] for t in open_only["items"])
    closed = page(client, open="false", page_size=1)
    assert open_only["total"] + closed["total"] == page(client)["total"]
    stale = page(client, stale="true", page_size=200)
    assert stale["total"] > 0 and all(t["stale_open"] for t in stale["items"])
    category = everything["items"][0]["sn_category"]
    if category:
        by_category = page(client, q="timeout", sn_category=category, page_size=200)
        assert by_category["total"] > 0 and {t["sn_category"] for t in by_category["items"]} == {category}
    state = page(client, state="zz-no-such-state")
    assert state["total"] == 0 and state["items"] == []
    assert page(client, am_category="Integration")["total"] == 0, "no approved AI labels in the fixture"


def test_pagination_totals_and_bounds(client):
    first = page(client, page_size=200)
    assert first["page_size"] == 200 and len(first["items"]) == 200
    total = first["total"]
    second = page(client, page=2, page_size=200)
    assert second["total"] == total
    assert not {t["ticket_id"] for t in first["items"]} & {t["ticket_id"] for t in second["items"]}
    assert first["items"][-1]["opened_at"] >= second["items"][0]["opened_at"]
    last_page = (total + 199) // 200
    tail = page(client, page=last_page, page_size=200)
    assert 0 < len(tail["items"]) <= 200
    beyond = page(client, page=last_page + 1, page_size=200)
    assert beyond["items"] == [] and beyond["total"] == total
    default = page(client)
    assert default["page_size"] == 50 and len(default["items"]) == 50

    for params in ({"page_size": 201}, {"page_size": 0}, {"page": 0}, {"q": "x" * 201}, {"sort": "rowid; DROP"}):
        r = client.get(URL, params=params)
        assert r.status_code == 422, params
        assert r.json()["ok"] is False and r.json()["error"]["kind"] == "validation"


@pytest.mark.parametrize(
    ("sort", "key", "reverse"),
    [("opened_desc", "opened_at", True), ("opened_asc", "opened_at", False)],
)
def test_sort_whitelist_orders(client, sort, key, reverse):
    items = page(client, sort=sort, page_size=200, q="timeout")["items"]
    values = [t[key] for t in items]
    assert values == sorted(values, reverse=reverse)


def test_priority_and_updated_sorts(client):
    by_priority = page(client, sort="priority", page_size=200)["items"]
    priorities = [t["priority"] for t in by_priority]
    assert priorities == sorted(priorities, key=lambda p: (p is None, p))
    updated = page(client, sort="updated_desc", page_size=5)
    assert len(updated["items"]) == 5


def _add_label(conn: Any, run_id: str, status: str, ticket_id: str, stage: str, input_hash: str, category: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO ai_run (run_id, skill, skill_hash, schema_version, invoked_via, profile, status, "
        "started_at) VALUES (?, 'sed-triage-batch', 'test-hash', 1, 'manual', 'synthetic', ?, '2026-09-01T00:00:00Z')",
        (run_id, status),
    )
    conn.execute(
        "INSERT INTO ai_ticket_label (ticket_id, stage, run_id, input_hash, am_category, confidence, created_at) "
        "VALUES (?, ?, ?, ?, ?, 0.9, '2026-09-01T00:00:00Z')",
        (ticket_id, stage, run_id, input_hash, category),
    )


def test_ai_labels_follow_run_status_and_content_hash(ops_profile_rw):
    """Only approved labels on the current content hash are shown; include_drafts adds completed (unreviewed) runs."""
    from urllib.parse import quote

    from sed import db
    from tests.fixtures.api import api_client

    paths = ops_profile_rw.paths
    conn = db.connect(paths.db)
    try:
        ticket_id, open_hash = conn.execute(
            "SELECT ticket_id, open_hash FROM ticket WHERE kind = 'incident' AND is_open = 1 AND open_hash IS NOT NULL "
            "ORDER BY ticket_id LIMIT 1"
        ).fetchone()
        stale_ticket = conn.execute(
            "SELECT ticket_id FROM ticket WHERE kind = 'incident' AND open_hash IS NOT NULL "
            "AND ticket_id != ? ORDER BY ticket_id LIMIT 1",
            (ticket_id,),
        ).fetchone()[0]
        with db.write_tx(conn):
            _add_label(conn, "20260901T000000-triage-test1", "completed", ticket_id, "open", open_hash, "Integration")
            _add_label(conn, "20260901T000000-triage-test2", "approved", stale_ticket, "open", "not-the-hash", "Access")
    finally:
        conn.close()

    client = api_client(paths)
    assert page(client, am_category="Integration")["total"] == 0
    drafts = page(client, am_category="Integration", include_drafts="true")
    assert drafts["total"] == 1 and drafts["items"][0]["ticket_id"] == ticket_id
    assert drafts["items"][0]["label_run_id"] == "20260901T000000-triage-test1"
    assert page(client, am_category="Access", include_drafts="true")["total"] == 0, "label on an outdated hash"

    detail = client.get(f"/api/ops/tickets/{quote(ticket_id, safe='')}").json()
    assert detail["am_category"] is None and detail["labels"] == [], "unreviewed labels need include_drafts"
    detail = client.get(f"/api/ops/tickets/{quote(ticket_id, safe='')}?include_drafts=true").json()
    assert (detail["am_category"], detail["label_run_status"]) == ("Integration", "completed")
    assert [(x["run_status"], x["am_category"]) for x in detail["labels"]] == [("completed", "Integration")]
    stale = client.get(f"/api/ops/tickets/{quote(stale_ticket, safe='')}?include_drafts=true").json()
    assert stale["labels"] == [], "a label on an outdated content hash is not shown"

    conn = db.connect(paths.db)
    try:
        with db.write_tx(conn):
            conn.execute("UPDATE ai_run SET status = 'approved' WHERE run_id = '20260901T000000-triage-test1'")
    finally:
        conn.close()
    approved = page(client, am_category="Integration")
    assert approved["total"] == 1 and approved["items"][0]["am_category"] == "Integration"
    assert approved["items"][0]["label_run_status"] == "approved"
    assert client.get(f"/api/ops/tickets/{quote(ticket_id, safe='')}").json()["am_category"] == "Integration"


def test_hand_edited_urls_get_validation_errors_not_internal_errors(ops_profile):
    from tests.fixtures.api import api_client
    from tests.platform.api.conftest import assert_envelope

    client = api_client(ops_profile.paths)
    cases = [
        ("/api/ops/tickets", {"page": 10**17, "page_size": 200}),
        ("/api/ops/overview", {"period": "0000-01"}),
        ("/api/ops/overview", {"as_of": "0001-01-03"}),
        ("/api/ops/tickets/backlog", {"as_of": "9999-12-31"}),
        ("/api/ops/tickets", {"period": "9999-12"}),
    ]
    for path, params in cases:
        assert_envelope(client.get(path, params=params), 422, "validation")
    for q in ("time\x00out", "\x00"):
        response = client.get("/api/ops/tickets", params={"q": q})
        assert response.status_code == 200, (q, response.text[:200])
