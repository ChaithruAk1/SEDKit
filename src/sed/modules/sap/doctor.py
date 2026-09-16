"""SAP module health checks, appended to `sed doctor` as sap.*."""

from __future__ import annotations

import sqlite3
from typing import Any

from sed.errors import SedError
from sed.paths import Paths


def checks(paths: Paths) -> list[Any]:
    from sed import db
    from sed.doctor import Check
    from sed.modules import get
    from sed.modules.sap.rules import load_rules
    from sed.modules.sap.scope import load_scope
    from sed.reports.specs import load_report_spec

    try:
        scope = load_scope(paths)
        load_rules(paths)
        for rdef in get("sap").reports:
            load_report_spec(rdef.key, paths)
    except SedError as exc:
        return [Check("sap.config_valid", "fail", f"{exc.message}: {exc.details}" if exc.details else exc.message)]
    out = [Check("sap.config_valid", "ok", "sap scope, risk rules and report specs load")]
    if not scope.configured:
        out.append(
            Check("sap.scope_configured", "warn", "config/sap/scope.yaml lists no SAP groups, categories or fields")
        )
        return out
    if not paths.db.is_file():
        return out
    groups = [g.name for g in scope.config.groups]
    apps = [a for x in scope.config.landscapes for a in x.apps]
    try:
        conn = db.connect(paths.db, readonly=True)
        try:
            has_tickets = conn.execute("SELECT 1 FROM ticket LIMIT 1").fetchone() is not None
            seen = {
                r[0]
                for r in conn.execute(
                    f"SELECT DISTINCT assignment_group FROM ticket WHERE assignment_group IN ({_marks(groups)})", groups
                )
            }
            has_apps = conn.execute("SELECT 1 FROM application LIMIT 1").fetchone() is not None
            known = {
                r[0] for r in conn.execute(f"SELECT app_id FROM application WHERE app_id IN ({_marks(apps)})", apps)
            }
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        return [*out, Check("sap.scope_matches_data", "warn", f"cannot read the database: {exc}")]
    unseen = [g for g in groups if g not in seen]
    out.append(
        Check(
            "sap.scope_groups_seen",
            "warn" if has_tickets and unseen else "ok",
            f"SAP groups never seen on a ticket (check exact names): {', '.join(unseen)}"
            if has_tickets and unseen
            else "every configured SAP group appears on tickets"
            if has_tickets
            else "no tickets imported yet",
        )
    )
    missing = [a for a in apps if a not in known]
    out.append(
        Check(
            "sap.landscape_apps_known",
            "warn" if has_apps and missing else "ok",
            f"landscape applications not in the portfolio: {', '.join(missing)}"
            if has_apps and missing
            else "every landscape application is in the portfolio"
            if has_apps
            else "no applications imported yet",
        )
    )
    return out


def _marks(values: list[str]) -> str:
    return ", ".join("?" for _ in values) or "NULL"
