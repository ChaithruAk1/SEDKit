"""POST /api/aliases on a writable ops profile: manual alias, decision log and re-linking of existing rows."""

from __future__ import annotations

import pytest

from sed import db
from tests.fixtures.api import api_client
from tests.platform.api.conftest import assert_envelope


def _rows(paths, sql, params=()):
    conn = db.connect(paths.db, readonly=True)
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _linked_counts(paths, raw):
    out = {}
    for table in ("contract", "license"):
        rows = _rows(paths, f"SELECT vendor_id FROM {table} WHERE vendor_raw = ?", (raw,))
        out[table] = (len(rows), [r["vendor_id"] for r in rows])
    return out


def test_assigning_an_unmapped_vendor_relinks_contracts_and_licenses(ops_profile_rw):
    paths = ops_profile_rw.paths
    client = api_client(paths)
    unmapped = client.get("/api/dq/unmapped?kind=vendor").json()["items"]
    assert unmapped, "the synthetic profile has unmapped vendor spellings"

    def weight(item):
        counts = _linked_counts(paths, item["raw_value"])
        return counts["contract"][0] * counts["license"][0]

    item = max(unmapped, key=lambda i: (weight(i), i["raw_value"]))
    raw, suggestion = item["raw_value"], item["suggestion"]
    before = _linked_counts(paths, raw)
    assert before["contract"][0] > 0 and before["license"][0] > 0
    assert all(v is None for _, ids in before.values() for v in ids)

    response = client.post("/api/aliases", json={"kind": "vendor", "raw_value": raw, "target": suggestion})
    assert response.status_code == 200, response.text
    body = response.json()
    target_id = _rows(paths, "SELECT vendor_id FROM vendor WHERE name = ?", (suggestion,))[0]["vendor_id"]
    assert (body["kind"], body["raw_value"], body["target_id"]) == ("vendor", raw, target_id)
    assert body["reresolved"]["contract"] >= before["contract"][0]
    assert body["reresolved"]["license"] >= before["license"][0]
    assert body["reresolved"]["unmapped_marked_resolved"] >= 1

    after = _linked_counts(paths, raw)
    assert after["contract"][1] == [target_id] * before["contract"][0]
    assert after["license"][1] == [target_id] * before["license"][0]
    assert _rows(paths, "SELECT resolved FROM unmapped_value WHERE kind = 'vendor' AND raw_value = ?", (raw,)) == [
        {"resolved": 1}
    ]
    decisions = _rows(
        paths,
        "SELECT decision, payload_json, reviewer FROM review_decision WHERE target_type = 'alias' AND target_id = ?",
        (f"vendor:{raw}",),
    )
    assert len(decisions) == 1 and decisions[0]["decision"] == "approve" and target_id in decisions[0]["payload_json"]
    assert _rows(
        paths,
        "SELECT origin, target_id FROM alias WHERE kind = 'vendor' AND target_id = ? AND origin = ?",
        (target_id, "manual"),
    )
    remaining = {i["raw_value"] for i in client.get("/api/dq/unmapped?kind=vendor").json()["items"]}
    assert raw not in remaining


def test_assigning_an_app_alias_relinks_tickets(ops_profile_rw):
    paths = ops_profile_rw.paths
    tickets = _rows(
        paths,
        "SELECT ticket_id, app_id FROM ticket WHERE app_id IS NOT NULL AND business_service_raw IS NOT NULL "
        "ORDER BY ticket_id LIMIT 3",
    )
    assert len(tickets) == 3
    app_id = tickets[0]["app_id"]
    ids = [t["ticket_id"] for t in tickets]
    conn = db.connect(paths.db)
    try:
        with db.write_tx(conn):  # simulate an export that names the service with an unknown spelling
            conn.executemany(
                "UPDATE ticket SET business_service_raw = 'Legacy Ledger Suite', cmdb_ci_raw = NULL, app_id = NULL "
                "WHERE ticket_id = ?",
                [(i,) for i in ids],
            )
    finally:
        conn.close()

    response = api_client(paths).post(
        "/api/aliases", json={"kind": "app", "raw_value": "Legacy Ledger Suite", "target": app_id}
    )
    assert response.status_code == 200, response.text
    assert response.json()["reresolved"]["ticket"] >= 3
    placeholders = ",".join("?" * len(ids))
    relinked = _rows(paths, f"SELECT DISTINCT app_id FROM ticket WHERE ticket_id IN ({placeholders})", ids)
    assert relinked == [{"app_id": app_id}]


@pytest.mark.xfail(
    reason="reresolve (ws7 loader.py, M1 behaviour) derives ticket vendors from assignment_group.vendor_id and never "
    "re-resolves assignment_group.vendor_raw; see docs/m2/contract-requests/ws4-api-platform.md item 2",
    strict=False,
)
def test_vendor_alias_relinks_tickets_of_groups_with_that_vendor_spelling(ops_profile_rw):
    paths = ops_profile_rw.paths
    groups = _rows(
        paths, "SELECT name, vendor_raw FROM assignment_group WHERE vendor_id IS NULL AND vendor_raw IS NOT NULL"
    )
    assert groups
    group, raw = groups[0]["name"], groups[0]["vendor_raw"]
    target = _rows(paths, "SELECT suggestion FROM unmapped_value WHERE kind = 'vendor' AND raw_value = ?", (raw,))
    response = api_client(paths).post(
        "/api/aliases", json={"kind": "vendor", "raw_value": raw, "target": target[0]["suggestion"]}
    )
    assert response.status_code == 200, response.text
    unlinked = _rows(
        paths, "SELECT COUNT(*) AS n FROM ticket WHERE assignment_group = ? AND vendor_id IS NULL", (group,)
    )
    assert unlinked[0]["n"] == 0


def test_invalid_alias_requests_use_the_validation_envelope_and_write_nothing(ops_profile_rw):
    paths = ops_profile_rw.paths
    client = api_client(paths)
    vendor = ops_profile_rw.ids["vendor_p2"]
    cases = [
        {"kind": "planet", "raw_value": "Mars Corp", "target": vendor},
        {"kind": "vendor", "raw_value": "Mars Corp", "target": "No Such Vendor"},
        {"kind": "vendor", "raw_value": "", "target": vendor},
        {"kind": "vendor", "raw_value": "x" * 301, "target": vendor},
        {"kind": "vendor", "raw_value": "Mars Corp", "target": "t" * 201},
        {"kind": "vendor", "raw_value": "Mars Corp"},
        {"kind": "vendor", "raw_value": "Mars Corp", "target": vendor, "extra": 1},
    ]
    for body in cases:
        assert_envelope(client.post("/api/aliases", json=body), 422, "validation")
    assert_envelope(
        client.post("/api/aliases", content=b"{not json", headers={"Content-Type": "application/json"}),
        422,
        "validation",
    )
    unauthorised = api_client(paths, send_token=False).post(
        "/api/aliases", json={"kind": "vendor", "raw_value": "Mars Corp", "target": vendor}
    )
    assert_envelope(unauthorised, 403, "forbidden")
    assert _rows(paths, "SELECT COUNT(*) AS n FROM review_decision")[0]["n"] == 0
    assert _rows(paths, "SELECT COUNT(*) AS n FROM alias WHERE origin = 'manual'")[0]["n"] == 0


def test_busy_database_gives_409_with_retry_after(ops_profile_rw, monkeypatch):
    paths = ops_profile_rw.paths
    monkeypatch.setattr(db, "BUSY_TIMEOUT_MS", 200)
    client = api_client(paths)
    body = {"kind": "vendor", "raw_value": "NORDWIND MS", "target": ops_profile_rw.ids["vendor_p2"]}
    holder = db.connect(paths.db)
    try:
        holder.execute("BEGIN IMMEDIATE")
        busy = client.post("/api/aliases", json=body)
        assert_envelope(busy, 409, "busy")
        assert busy.headers["retry-after"] == "2"
        holder.execute("ROLLBACK")
    finally:
        holder.close()
    assert _rows(paths, "SELECT COUNT(*) AS n FROM review_decision")[0]["n"] == 0
    retried = client.post("/api/aliases", json=body)
    assert retried.status_code == 200, retried.text
    assert retried.json()["target_id"] == ops_profile_rw.ids["vendor_p2"]
