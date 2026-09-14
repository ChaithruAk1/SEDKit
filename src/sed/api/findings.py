"""Published findings: the single definition used by every API route (and later the dashboard review pages).

Published = rule findings that are active and not suppressed at `as_of`, plus AI findings that are approved or
update_pending (the previously approved body_md stays published until the update is approved).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from typing import Literal

from sed.api.models import Evidence, FindingOut

SEVERITY_RANK = "CASE severity WHEN 'critical' THEN 3 WHEN 'high' THEN 2 WHEN 'medium' THEN 1 ELSE 0 END"


def published_findings(
    conn: sqlite3.Connection,
    as_of: date,
    *,
    kind: str | None = None,
    origin: Literal["rule", "ai"] | None = None,
    status: Literal["published", "all"] = "published",
    subject_type: str | None = None,
    subject_id: str | None = None,
    limit: int = 100,
) -> list[FindingOut]:
    where: list[str] = []
    params: list[object] = []
    if status == "published":
        where.append(
            "((origin = 'rule' AND status = 'active' AND (suppress_until IS NULL OR suppress_until <= ?)) "
            "OR (origin = 'ai' AND status IN ('approved', 'update_pending')))"
        )
        params.append(as_of.isoformat())
    for column, value in (
        ("kind", kind),
        ("origin", origin),
        ("subject_type", subject_type),
        ("subject_id", subject_id),
    ):
        if value is not None:
            where.append(f"{column} = ?")
            params.append(value)
    sql = (
        "SELECT finding_id, origin, kind, severity, title, subject_type, subject_id, status, body_md, payload_json, "
        "run_id, reviewed_by, reviewed_at FROM finding"
        + (" WHERE " + " AND ".join(where) if where else "")
        + f" ORDER BY {SEVERITY_RANK} DESC, kind, title LIMIT ?"
    )
    params.append(int(limit))
    out = []
    for r in conn.execute(sql, params).fetchall():
        try:
            payload = json.loads(r["payload_json"] or "{}")
        except json.JSONDecodeError:
            payload = {}
        evidence = [
            Evidence(fact_key=str(e.get("fact_key")), value=e.get("value", e.get("value_at_run")))
            for e in payload.get("evidence", [])
            if isinstance(e, dict) and e.get("fact_key")
        ]
        out.append(
            FindingOut(
                finding_id=r["finding_id"],
                origin=r["origin"],
                kind=r["kind"],
                severity=r["severity"],
                title=r["title"],
                subject_type=r["subject_type"],
                subject_id=r["subject_id"],
                status=r["status"],
                body_md=r["body_md"],
                evidence=evidence,
                system_detected=r["origin"] == "rule",
                run_id=r["run_id"],
                reviewed_by=r["reviewed_by"],
                reviewed_at=r["reviewed_at"],
            )
        )
    return out
