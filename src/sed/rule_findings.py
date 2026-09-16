"""Rule-origin findings ("system-detected") for every module: deterministic risks that never depend on the AI.

A module declares `rule_findings`, a function `(conn, paths, as_of) -> list[dict]`, and the `finding_kinds` it owns.
Each computed finding has stable_key (starting with "<kind>:"), kind, subject_type, subject_id, severity, title and
evidence ([{fact_key, value}]). `refresh` upserts one module's `finding` rows (origin='rule'):
* same stable_key and still firing -> evidence/severity refreshed in place;
* acknowledged or suppressed rows stay hidden unless their evidence changed materially (then they re-activate);
* active rows of the module's own kinds whose condition no longer holds -> status 'superseded'.
Other modules' findings are never touched, and each module keeps its own persisted as-of (meta
`<module>.rule_findings_as_of`), so modules refresh independently.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Callable
from datetime import date
from typing import Any

from sed import db, modules
from sed.errors import ValidationFailed
from sed.modules.contract import Module
from sed.paths import Paths

SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def state_key(module_key: str) -> str:
    return f"{module_key}.rule_findings_as_of"


def _finding_id() -> str:
    # Random, not derived from (stable_key, timestamp): a finding can be superseded and re-inserted within one second.
    return "rule-" + uuid.uuid4().hex[:20]


def _provider(module_key: str) -> tuple[Module, Callable[..., list[dict[str, Any]]]]:
    module = modules.get(module_key)
    if not module.rule_findings or not module.finding_kinds:
        raise ValidationFailed(f"Module '{module_key}' declares no rule findings and finding kinds")
    return module, modules.load_ref(module.rule_findings)


def _kind_filter(kinds: tuple[str, ...]) -> tuple[str, list[str]]:
    return f"kind IN ({', '.join('?' for _ in kinds)})", list(kinds)


def compute(conn: sqlite3.Connection, paths: Paths | None, as_of: date, module_key: str) -> list[dict[str, Any]]:
    """The module's findings for `as_of`, checked against the kinds it owns (so a refresh never touches another's)."""
    module, compute_findings = _provider(module_key)
    found = compute_findings(conn, paths, as_of)
    foreign = sorted({f["kind"] for f in found} - set(module.finding_kinds))
    if foreign:
        raise ValidationFailed(f"Module '{module_key}' computed rule findings of kinds it does not declare", foreign)
    unkeyed = sorted(f["stable_key"] for f in found if not f["stable_key"].startswith(f"{f['kind']}:"))
    if unkeyed:
        raise ValidationFailed(
            f"Module '{module_key}': rule finding stable keys must start with '<kind>:'", unkeyed[:10]
        )
    return found


def _material_change(old: dict[str, Any], new: dict[str, Any]) -> bool:
    if SEVERITY_ORDER.get(new["severity"], 0) > SEVERITY_ORDER.get(old.get("severity") or "low", 0):
        return True
    old_ev = {e["fact_key"]: e["value"] for e in json.loads(old.get("payload_json") or "{}").get("evidence", [])}
    for e in new["evidence"]:
        before = old_ev.get(e["fact_key"])
        after = e["value"]
        numeric = isinstance(before, int | float) and isinstance(after, int | float)
        if numeric and before and abs(after - before) / abs(before) > 0.25:
            return True
    return False


def refresh(
    conn: sqlite3.Connection, paths: Paths | None, as_of: date, module_key: str, *, force: bool = False
) -> dict[str, Any]:
    """Upsert one module's persisted rule findings. The persisted state tracks the newest as-of: an older as-of is
    skipped unless `force`, so a report for a past period never supersedes findings that are still current (see
    `as_of_findings`)."""
    key = state_key(module_key)
    state_as_of = db.get_meta(conn, key)
    if state_as_of and as_of.isoformat() < state_as_of and not force:
        return {"skipped": True, "state_as_of": state_as_of}
    computed = {f["stable_key"]: f for f in compute(conn, paths, as_of, module_key)}
    kinds_sql, kinds = _kind_filter(modules.get(module_key).finding_kinds)
    now = db.utc_now()
    stats: dict[str, Any] = {"inserted": 0, "updated": 0, "reactivated": 0, "superseded": 0, "hidden": 0}
    with db.write_tx(conn):
        db.set_meta(conn, key, as_of.isoformat())
        existing = {
            r["stable_key"]: dict(r)
            for r in conn.execute(
                f"SELECT * FROM finding WHERE origin = 'rule' AND {kinds_sql} "
                "AND status IN ('active', 'acknowledged') ORDER BY created_at",
                kinds,
            )
        }
        for stable_key, f in computed.items():
            payload = json.dumps(
                {"evidence": f["evidence"], "as_of": as_of.isoformat()}, ensure_ascii=False, default=str
            )
            old = existing.get(stable_key)
            if old is None:
                conn.execute(
                    "INSERT INTO finding (finding_id, run_id, origin, stable_key, kind, subject_type, subject_id, "
                    "period, "
                    "title, body_md, severity, payload_json, status, created_at) "
                    "VALUES (?, NULL, 'rule', ?, ?, ?, ?, ?, ?, NULL, ?, ?, 'active', ?)",
                    (
                        _finding_id(),
                        stable_key,
                        f["kind"],
                        f["subject_type"],
                        f["subject_id"],
                        as_of.isoformat(),
                        f["title"],
                        f["severity"],
                        payload,
                        now,
                    ),
                )
                stats["inserted"] += 1
                continue
            suppressed = old["status"] == "acknowledged" or (old["suppress_until"] or "") > as_of.isoformat()
            if suppressed and not _material_change(old, f):
                stats["hidden"] += 1
                conn.execute(
                    "UPDATE finding SET payload_json = ?, title = ? WHERE finding_id = ?",
                    (payload, f["title"], old["finding_id"]),
                )
                continue
            if suppressed:
                stats["reactivated"] += 1
            else:
                stats["updated"] += 1
            conn.execute(
                "UPDATE finding SET title = ?, severity = ?, payload_json = ?, status = 'active', "
                "suppress_until = NULL, period = ? WHERE finding_id = ?",
                (f["title"], f["severity"], payload, as_of.isoformat(), old["finding_id"]),
            )
        for stable_key, old in existing.items():
            if stable_key not in computed and old["status"] == "active":
                conn.execute("UPDATE finding SET status = 'superseded' WHERE finding_id = ?", (old["finding_id"],))
                stats["superseded"] += 1
    return stats


def refresh_enabled(
    conn: sqlite3.Connection, paths: Paths | None, as_of: date, *, force: bool = False
) -> dict[str, dict[str, Any]]:
    """Refresh every enabled module that declares rule findings; module key -> refresh stats."""
    return {m.key: refresh(conn, paths, as_of, m.key, force=force) for m in modules.enabled(paths) if m.rule_findings}


def published(conn: sqlite3.Connection, as_of: date, kinds: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
    """Active, unsuppressed rule findings at `as_of` (only these kinds when given), most severe first."""
    sql = (
        "SELECT finding_id, stable_key, kind, subject_type, subject_id, severity, title, payload_json, status, "
        "suppress_until FROM finding WHERE origin = 'rule' AND status = 'active' "
        "AND (suppress_until IS NULL OR suppress_until <= ?)"
    )
    params: list[Any] = [as_of.isoformat()]
    if kinds is not None:
        if not kinds:
            return []
        kinds_sql, kind_params = _kind_filter(kinds)
        sql += f" AND {kinds_sql}"
        params += kind_params
    out = [dict(r) for r in conn.execute(sql, params).fetchall()]
    out.sort(key=lambda r: (-SEVERITY_ORDER.get(r["severity"] or "low", 0), r["kind"], r["title"]))
    return out


def as_of_findings(conn: sqlite3.Connection, paths: Paths | None, as_of: date, module_key: str) -> list[dict[str, Any]]:
    """One module's rule findings as they stood at `as_of`, for report snapshots.

    At or after the module's persisted as-of this refreshes and returns the published rows. For an older as-of the
    rules are computed read-only; human acknowledgements/suppressions still apply, and finding ids are reused by
    stable_key (None when the finding no longer exists in the current state).
    """
    kinds = tuple(modules.get(module_key).finding_kinds)
    state_as_of = db.get_meta(conn, state_key(module_key))
    if not state_as_of or as_of.isoformat() >= state_as_of:
        refresh(conn, paths, as_of, module_key)
        return published(conn, as_of, kinds)
    kinds_sql, kind_params = _kind_filter(kinds)
    persisted = {
        r["stable_key"]: dict(r)
        for r in conn.execute(
            f"SELECT * FROM finding WHERE origin = 'rule' AND {kinds_sql} "
            "AND status IN ('active', 'acknowledged', 'superseded') ORDER BY created_at",
            kind_params,
        )
    }
    out = []
    for f in compute(conn, paths, as_of, module_key):
        old = persisted.get(f["stable_key"])
        if old and old["status"] != "superseded":
            suppressed = old["status"] == "acknowledged" or (old["suppress_until"] or "") > as_of.isoformat()
            if suppressed and not _material_change(old, f):
                continue
        payload = json.dumps({"evidence": f["evidence"], "as_of": as_of.isoformat()}, ensure_ascii=False, default=str)
        out.append(
            {
                "finding_id": old["finding_id"] if old else None,
                "stable_key": f["stable_key"],
                "kind": f["kind"],
                "subject_type": f["subject_type"],
                "subject_id": f["subject_id"],
                "severity": f["severity"],
                "title": f["title"],
                "payload_json": payload,
                "status": "active",
                "suppress_until": None,
            }
        )
    out.sort(key=lambda r: (-SEVERITY_ORDER.get(r["severity"] or "low", 0), r["kind"], r["title"]))
    return out
