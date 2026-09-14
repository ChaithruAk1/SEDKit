"""P13: the fake names, emails and phone numbers injected into synthetic ticket descriptions never appear in any
/api/ops GET response (list views, detail views with description and close notes, App 360 for every application)."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote


def _responses(client: Any) -> dict[str, str]:
    out: dict[str, str] = {}

    def fetch(url: str, **params: Any) -> dict[str, Any]:
        r = client.get(url, params=params or None)
        assert r.status_code == 200, (url, params, r.text[:300])
        out[f"{url}?{sorted(params.items())}"] = r.text
        return r.json()

    filters = fetch("/api/ops/filters")
    fetch("/api/ops/overview")
    fetch("/api/ops/attention", limit=1000)
    for granularity in ("week", "month"):
        fetch("/api/ops/tickets/volumes", granularity=granularity, n=18)
        fetch("/api/ops/tickets/sla", granularity=granularity, n=18)
        fetch("/api/ops/tickets/mttr", granularity=granularity, n=18)
    fetch("/api/ops/tickets/backlog")
    for group_by in ("app", "vendor", "category", "app_category"):
        fetch("/api/ops/costs", group_by=group_by, months=24)
    fetch("/api/ops/contracts/renewals", days=1095)
    fetch("/api/ops/licenses/utilization")
    fetch("/api/ops/vendors/sla-trend", months=24)
    for app in filters["apps"]:
        fetch(f"/api/ops/apps/{app['app_id']}")
    fetch("/api/ops/apps")

    # The P13 descriptions read "Please contact <name> (<email>, <phone>) who can reproduce it."
    injected = fetch("/api/ops/tickets", q="contact reproduce", page_size=200)
    assert injected["total"] > 0, "the fixture contains the P13 tickets"
    fetch("/api/ops/tickets", page_size=200, open="true")
    tickets = {t["ticket_id"] for t in injected["items"]}
    tickets |= {t["ticket_id"] for t in fetch("/api/ops/tickets", page_size=200, sort="priority")["items"]}
    for ticket_id in sorted(tickets):
        fetch(f"/api/ops/tickets/{quote(ticket_id, safe='')}")
    return out


def test_no_injected_pii_in_ops_json(client, ops_profile):
    injections = json.loads((ops_profile.ground_truth / "pii_injections.json").read_text(encoding="utf-8"))
    assert len(injections) > 100
    leaks: dict[str, list[str]] = {}
    for key, text in _responses(client).items():
        decoded = json.dumps(json.loads(text), ensure_ascii=False)
        found = [s for s in injections if s in text or s in decoded]
        if found:
            leaks[key] = found[:5]
    assert leaks == {}
