"""Rule findings per module (sed.rule_findings): a module's refresh supersedes only its own finding kinds, and each
module keeps its own refresh state, so two modules never undo each other's system-detected risks."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from sed import analytics, db, modules, rule_findings
from sed.errors import ValidationFailed
from sed.modules.contract import Module

FOUND: list[dict[str, Any]] = []


def compute_demo(conn, paths, as_of) -> list[dict[str, Any]]:
    return [dict(f) for f in FOUND]


DEMO = Module(key="demo", title="Demo", finding_kinds=("demo_risk",), rule_findings=f"{__name__}:compute_demo")


def _finding(key: str, *, kind: str = "demo_risk", stable_key: str | None = None) -> dict[str, Any]:
    return {
        "stable_key": stable_key or f"{kind}:{key}",
        "kind": kind,
        "subject_type": "thing",
        "subject_id": key,
        "severity": "medium",
        "title": f"Demo risk {key}",
        "evidence": [{"fact_key": f"demo.{key}.value", "value": 1}],
    }


@pytest.fixture
def conn(tmp_path: Path):
    path = tmp_path / "sed.db"
    c = db.connect(path)
    db.migrate(c, path, None)
    FOUND.clear()
    with modules.use_modules((modules.get("ops"), DEMO)):
        yield c
    c.close()


def _statuses(conn) -> dict[str, str]:
    return {r["stable_key"]: r["status"] for r in conn.execute("SELECT stable_key, status FROM finding")}


def _ops_finding(conn) -> None:
    with db.write_tx(conn):
        conn.execute(
            "INSERT INTO finding (finding_id, origin, stable_key, kind, title, status, created_at) "
            "VALUES ('ops-1', 'rule', 'renewal_risk:notice:C1', 'renewal_risk', 'Notice deadline', 'active', ?)",
            (db.utc_now(),),
        )


def test_a_module_refresh_supersedes_only_its_own_kinds(conn):
    _ops_finding(conn)
    FOUND.append(_finding("a"))
    assert rule_findings.refresh(conn, None, date(2026, 9, 1), "demo")["inserted"] == 1
    FOUND.clear()
    assert rule_findings.refresh(conn, None, date(2026, 9, 2), "demo")["superseded"] == 1
    assert _statuses(conn) == {"renewal_risk:notice:C1": "active", "demo_risk:a": "superseded"}

    FOUND.append(_finding("b"))
    rule_findings.refresh(conn, None, date(2026, 9, 2), "demo")
    assert analytics.refresh_rule_findings(conn, None, date(2026, 9, 2))["superseded"] == 1  # nothing fires on ops
    assert _statuses(conn) == {
        "renewal_risk:notice:C1": "superseded",
        "demo_risk:a": "superseded",
        "demo_risk:b": "active",
    }


def test_each_module_keeps_its_own_refresh_state(conn):
    FOUND.append(_finding("a"))
    rule_findings.refresh(conn, None, date(2026, 9, 2), "demo")
    analytics.refresh_rule_findings(conn, None, date(2026, 8, 1))
    assert db.get_meta(conn, "demo.rule_findings_as_of") == "2026-09-02"
    assert db.get_meta(conn, "ops.rule_findings_as_of") == "2026-08-01"
    assert rule_findings.refresh(conn, None, date(2026, 8, 15), "demo") == {
        "skipped": True,
        "state_as_of": "2026-09-02",
    }
    assert "skipped" not in analytics.refresh_rule_findings(conn, None, date(2026, 8, 15))  # ops state is older

    FOUND.append(_finding("c"))
    past = rule_findings.as_of_findings(conn, None, date(2026, 8, 20), "demo")  # computed read-only
    assert [(f["stable_key"], f["finding_id"] is None) for f in past] == [("demo_risk:a", False), ("demo_risk:c", True)]
    assert "demo_risk:c" not in _statuses(conn)
    assert set(rule_findings.refresh_enabled(conn, None, date(2026, 9, 3))) == {"ops", "demo"}
    assert "demo_risk:c" in _statuses(conn)


def test_published_findings_filter_by_kind(conn):
    _ops_finding(conn)
    FOUND.append(_finding("a"))
    rule_findings.refresh(conn, None, date(2026, 9, 1), "demo")
    day = date(2026, 9, 1)
    assert [f["kind"] for f in rule_findings.published(conn, day, ("demo_risk",))] == ["demo_risk"]
    assert [f["kind"] for f in analytics.published_rule_findings(conn, day)] == ["renewal_risk"]
    assert sorted(f["kind"] for f in rule_findings.published(conn, day)) == ["demo_risk", "renewal_risk"]
    assert rule_findings.published(conn, day, ()) == []


@pytest.mark.parametrize(
    ("finding", "message"),
    [
        (_finding("x", kind="renewal_risk"), "kinds it does not declare"),
        (_finding("x", stable_key="x-without-kind"), "must start with '<kind>:'"),
    ],
)
def test_a_provider_cannot_write_other_kinds_or_unprefixed_keys(conn, finding, message):
    _ops_finding(conn)
    FOUND.append(finding)
    with pytest.raises(ValidationFailed, match=message):
        rule_findings.refresh(conn, None, date(2026, 9, 1), "demo")
    assert _statuses(conn) == {"renewal_risk:notice:C1": "active"}
    assert db.get_meta(conn, "demo.rule_findings_as_of") is None
