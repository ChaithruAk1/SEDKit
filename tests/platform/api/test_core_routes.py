"""Core /api routes on the shared ops profile: every GET answers 200 with its response model, and each route's
content matches the registry, the freshness helpers and the published-findings definition."""

from __future__ import annotations

import json
from datetime import date
from itertools import pairwise

from sed import db, modules
from sed.api.findings import published_findings
from sed.api.models import FindingOut, MetaOut
from sed.api.routes_core import recent_periods
from sed.calendar import shift_label
from sed.ingest.freshness import import_freshness
from sed.paths import Paths
from tests.fixtures.api import api_client
from tests.fixtures.ops_profile import AS_OF
from tests.platform.api.conftest import assert_envelope
from tests.platform.modules.sample_module import MODULE as HELLO


def _read(paths, sql, params=()):
    conn = db.connect(paths.db, readonly=True)
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def test_every_core_get_returns_its_model(ops_profile, core_gets):
    client = api_client(ops_profile.paths, send_token=False)
    paths_seen = {path.split("?")[0] for path, _ in core_gets}
    assert paths_seen == {
        "/api/health",
        "/api/meta",
        "/api/nav",
        "/api/modules",
        "/api/findings",
        "/api/imports",
        "/api/dq/unmapped",
        "/api/alias-targets",
        "/api/runs",
        "/api/review/queue",
    }
    for path, model in core_gets:
        response = client.get(path)
        assert response.status_code == 200, (path, response.text[:500])
        model.model_validate(response.json())


def test_meta_describes_profile_periods_entities_and_definitions(ops_profile):
    paths = ops_profile.paths
    body = api_client(paths).get("/api/meta").json()
    meta = MetaOut.model_validate(body)
    assert (meta.data_class, meta.profile, meta.pii_mode) == ("synthetic", "synthetic", "pseudonymize")
    assert meta.schema_version == db.latest_version() and meta.as_of_default == AS_OF.isoformat()
    assert (meta.reporting_tz, meta.base_currency) == ("Europe/Paris", "EUR")

    assert [len(meta.periods.weeks), len(meta.periods.months), len(meta.periods.quarters)] == [12, 18, 6]
    assert meta.periods.weeks[0] == "2026-W36" and ops_profile.periods["week"] in meta.periods.weeks
    assert meta.periods.months[0] == "2026-09" and ops_profile.periods["month"] in meta.periods.months
    assert meta.periods.quarters[0] == ops_profile.periods["quarter"]
    for labels in (meta.periods.weeks, meta.periods.months, meta.periods.quarters):
        assert all(shift_label(prev, -1) == cur for prev, cur in pairwise(labels))

    enabled = modules.enabled(paths)
    assert set(meta.entities) == {e.key for m in enabled for e in m.entities} == {"app", "vendor", "group"}
    vendors = _read(paths, "SELECT vendor_id, name FROM vendor WHERE is_deleted = 0")
    assert {(o.value, o.label) for o in meta.entities["vendor"]} == {(v["vendor_id"], v["name"]) for v in vendors}
    labels = [o.label.lower() for o in meta.entities["app"]]
    live_apps = _read(paths, "SELECT 1 FROM application WHERE is_deleted = 0")
    assert labels == sorted(labels) and len(labels) == len(live_apps)
    assert ops_profile.ids["vendor_p2"] in {o.value for o in meta.entities["vendor"]}

    registry_definitions = modules.metric_definitions(paths)
    assert registry_definitions and {k: (d.unit, d.text) for k, d in meta.definitions.items()} == registry_definitions
    assert [(m.key, m.title) for m in meta.modules] == [(m.key, m.title) for m in enabled]

    conn = db.connect(paths.db, readonly=True)
    try:
        assert [r.model_dump() for r in meta.freshness] == import_freshness(conn)
    finally:
        conn.close()


def test_recent_periods_cross_years_and_follow_the_fiscal_calendar():
    periods = recent_periods(date(2026, 1, 2), fiscal_year_start=4)
    assert periods.weeks[:3] == ["2026-W01", "2025-W52", "2025-W51"]
    assert periods.months[:3] == ["2026-01", "2025-12", "2025-11"] and periods.months[-1] == "2024-08"
    assert periods.quarters == ["2026-Q4", "2026-Q3", "2026-Q2", "2026-Q1", "2025-Q4", "2025-Q3"]


def test_meta_without_imports_has_no_as_of(tmp_path):
    from sed import bootstrap

    paths = Paths("synthetic", tmp_path / "empty")
    bootstrap.init_profile(paths, write_claude_settings=False)
    body = api_client(paths).get("/api/meta").json()
    assert body["as_of_default"] is None and body["freshness"] == []
    assert body["periods"]["weeks"][0] == shift_label(body["periods"]["weeks"][1], 1)
    assert all(options == [] for options in body["entities"].values())


def test_nav_and_modules_follow_the_served_modules(ops_profile):
    paths = ops_profile.paths
    nav = api_client(paths).get("/api/nav").json()["items"]
    expected = [
        {"id": i.id, "module": key, "label": i.label, "path": i.path, "order": i.order, "icon": i.icon}
        for key, i in modules.nav(paths)
    ]
    assert nav == expected and nav[-1]["id"] == "core.data"

    listed = api_client(paths).get("/api/modules").json()["modules"]
    ops = next(m for m in listed if m["key"] == "ops")
    assert ops["enabled"] is True and ops["reports"] == ["weekly", "monthly", "quarterly", "vendor"]
    assert ops["skills"] == [
        "sed-triage-batch",
        "sed-find-recurring",
        "sed-assess-risks",
        "sed-triage-open",
        "sed-draft-report",
    ]

    with_hello = api_client(paths, modules=[modules.get("ops"), HELLO])
    assert "hello.home" in [i["id"] for i in with_hello.get("/api/nav").json()["items"]]
    hello = next(m for m in with_hello.get("/api/modules").json()["modules"] if m["key"] == "hello")
    assert hello["enabled"] is True and hello["reports"] == []
    assert [m["key"] for m in with_hello.get("/api/meta").json()["modules"]] == ["ops", "hello"]

    only_core = api_client(paths, modules=[])
    assert [i["id"] for i in only_core.get("/api/nav").json()["items"]] == ["core.review", "core.runs", "core.data"]
    assert not any(m["enabled"] for m in only_core.get("/api/modules").json()["modules"])
    assert only_core.get("/api/meta").json()["definitions"] == {}


def test_findings_is_published_findings(ops_profile):
    paths = ops_profile.paths
    client = api_client(paths)
    conn = db.connect(paths.db, readonly=True)
    try:
        cases = [
            ("", published_findings(conn, AS_OF)),
            ("?status=all&limit=1000", published_findings(conn, AS_OF, status="all", limit=1000)),
            ("?origin=rule&kind=renewal_risk", published_findings(conn, AS_OF, origin="rule", kind="renewal_risk")),
            ("?as_of=2026-06-30&limit=3", published_findings(conn, date(2026, 6, 30), limit=3)),
        ]
        first = cases[0][1][0]
        cases.append(
            (
                f"?subject_type={first.subject_type}&subject_id={first.subject_id}",
                published_findings(conn, AS_OF, subject_type=first.subject_type, subject_id=first.subject_id),
            )
        )
    finally:
        conn.close()
    assert cases[0][1] and all(f.system_detected for f in cases[0][1] if f.origin == "rule")
    for query, expected in cases:
        response = client.get(f"/api/findings{query}")
        assert response.status_code == 200, (query, response.text)
        assert [FindingOut.model_validate(i) for i in response.json()["items"]] == expected, query
    assert_envelope(client.get("/api/findings?status=draft"), 422, "validation")
    assert_envelope(client.get("/api/findings?limit=0"), 422, "validation")


def test_imports_lists_batches_newest_first_with_parsed_dq(ops_profile):
    paths = ops_profile.paths
    items = api_client(paths).get("/api/imports?limit=5").json()["items"]
    stored = _read(paths, "SELECT batch_id, dq_json FROM import_batch ORDER BY batch_id DESC LIMIT 5")
    assert [i["batch_id"] for i in items] == [r["batch_id"] for r in stored]
    for item, row in zip(items, stored, strict=True):
        assert item["dq"] == json.loads(row["dq_json"]) and item["dq_severity"] == item["dq"].get("severity")
    total = _read(paths, "SELECT COUNT(*) AS n FROM import_batch")[0]["n"]
    assert len(api_client(paths).get("/api/imports?limit=1000").json()["items"]) == total


def test_unmapped_lists_unresolved_values_by_frequency(ops_profile):
    paths = ops_profile.paths
    client = api_client(paths)
    items = client.get("/api/dq/unmapped").json()["items"]
    stored = _read(paths, "SELECT kind, raw_value FROM unmapped_value WHERE resolved = 0")
    assert items and {(i["kind"], i["raw_value"]) for i in items} == {(r["kind"], r["raw_value"]) for r in stored}
    counts = [i["occurrences"] for i in items]
    assert counts == sorted(counts, reverse=True)
    assert all(i["kind"] == "vendor" for i in client.get("/api/dq/unmapped?kind=vendor").json()["items"])
    assert client.get("/api/dq/unmapped?kind=app").json()["items"] == []
    assert len(client.get("/api/dq/unmapped?limit=2").json()["items"]) == 2
    assert_envelope(client.get("/api/dq/unmapped?kind=planet"), 422, "validation")


def test_alias_targets_search_by_kind(ops_profile):
    paths = ops_profile.paths
    client = api_client(paths)
    vendor = ops_profile.ids["vendor_p2"]
    name = _read(paths, "SELECT name FROM vendor WHERE vendor_id = ?", (vendor,))[0]["name"]
    hits = client.get("/api/alias-targets", params={"kind": "vendor", "q": name[:6].lower()}).json()["items"]
    assert {"id": vendor, "name": name} in hits
    assert len(client.get("/api/alias-targets?kind=app&limit=3").json()["items"]) == 3
    groups = client.get("/api/alias-targets?kind=group").json()["items"]
    assert groups and all(g["id"] == g["name"] for g in groups)
    assert_envelope(client.get("/api/alias-targets"), 422, "validation")
    assert_envelope(client.get("/api/alias-targets?kind=planet"), 422, "validation")


def test_runs_lists_ai_runs_with_sample_statistics(ops_profile_rw):
    paths = ops_profile_rw.paths
    client = api_client(paths)
    assert client.get("/api/runs").json() == {"items": []}
    conn = db.connect(paths.db)
    try:
        with db.write_tx(conn):
            for seq, (run_id, status) in enumerate((("run-a", "approved"), ("run-b", "completed")), start=1):
                conn.execute(
                    "INSERT INTO ai_run (run_id, skill, skill_hash, schema_version, invoked_via, profile, status, "
                    "counts_json, sample_accuracy, sample_ci_low, sample_ci_high, sample_n, started_at, finished_at, "
                    "reviewed_by, reviewed_at) VALUES (?, 'sed-triage-batch', 'hash', 1, 'workflow', 'synthetic', ?, "
                    "?, 0.9, 0.8, 0.95, 30, ?, NULL, 'reviewer', NULL)",
                    (run_id, status, json.dumps({"items": 10 * seq, "note": "text", "flag": True}), f"2026-09-0{seq}"),
                )
    finally:
        conn.close()
    items = client.get("/api/runs").json()["items"]
    assert [i["run_id"] for i in items] == ["run-b", "run-a"]
    assert items[1]["counts"] == {"items": 10} and items[1]["sample_n"] == 30 and items[1]["sample_accuracy"] == 0.9
    assert [i["run_id"] for i in client.get("/api/runs?status=approved").json()["items"]] == ["run-a"]
    assert client.get("/api/runs?skill=sed-other").json()["items"] == []
    assert len(client.get("/api/runs?limit=1").json()["items"]) == 1


def test_database_routes_need_an_initialised_profile(tmp_path):
    client = api_client(Paths("synthetic", tmp_path / "missing"))
    assert client.get("/api/health").status_code == 200
    for path in ("/api/meta", "/api/findings", "/api/imports", "/api/runs"):
        assert_envelope(client.get(path), 412, "precondition")
