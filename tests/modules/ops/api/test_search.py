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
