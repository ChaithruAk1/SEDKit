"""SAP module health checks, appended to `sed doctor` as sap.*."""

from __future__ import annotations

import sqlite3
from collections import Counter
from typing import Any

from sed.errors import SedError
from sed.modules.sap.charm import OTHER_TYPE, UNKNOWN_STAGE
from sed.modules.sap.idoc import UNKNOWN_GROUP
from sed.modules.sap.scope import UNASSIGNED
from sed.paths import Paths


def checks(paths: Paths) -> list[Any]:
    from sed import db
    from sed.doctor import Check
    from sed.modules import get
    from sed.modules.ops.ai.extensions import unknown_categories
    from sed.modules.ops.ai.taxonomy import load_taxonomy
    from sed.modules.sap.charm import load_charm
    from sed.modules.sap.idoc import load_idoc
    from sed.modules.sap.rules import load_rules
    from sed.modules.sap.scope import load_scope
    from sed.modules.sap.taxonomy import load_sap_taxonomy
    from sed.modules.sap.triage import extension
    from sed.reports.specs import load_report_spec

    try:
        scope = load_scope(paths)
        charm = load_charm(paths, scope)
        idoc = load_idoc(paths, scope)
        load_rules(paths)
        load_sap_taxonomy(paths)
        for rdef in get("sap").reports:
            load_report_spec(rdef.key, paths)
        dropped = unknown_categories(extension(paths), load_taxonomy(paths))
    except SedError as exc:
        return [Check("sap.config_valid", "fail", f"{exc.message}: {exc.details}" if exc.details else exc.message)]
    out = [
        Check(
            "sap.config_valid",
            "ok",
            "sap scope, ChaRM, IDoc and taxonomy settings, risk rules and report specs load",
        ),
        Check(
            "sap.taxonomy_categories_known",
            "warn" if dropped else "ok",
            "SAP subcategories under categories missing from the ops taxonomy (AI triage leaves them out): "
            + ", ".join(f"{s.code} ({s.category})" for s in dropped)
            if dropped
            else "every SAP subcategory sits under a category of the ops taxonomy",
        ),
    ]
    if not scope.configured:
        # Ticket checks need a scope; the ChaRM and IDoc checks below still run.
        out.append(
            Check("sap.scope_configured", "warn", "config/sap/scope.yaml lists no SAP groups, categories or fields")
        )
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
            systems = [r[0] for r in conn.execute("SELECT DISTINCT system_id FROM sap_transport_import ORDER BY 1")]
            raw = {
                column: Counter(
                    dict(conn.execute(f"SELECT {column}, COUNT(*) FROM sap_change GROUP BY {column}").fetchall())
                )
                for column in ("transaction_type", "status_raw", "component_raw")
            }
            idoc_systems = [r[0] for r in conn.execute("SELECT DISTINCT system_id FROM sap_idoc ORDER BY 1")]
            idoc_codes = Counter(
                dict(conn.execute("SELECT status_code, COUNT(*) FROM sap_idoc_status GROUP BY status_code").fetchall())
            )
            idoc_types = Counter(
                dict(conn.execute("SELECT message_type, COUNT(*) FROM sap_idoc GROUP BY message_type").fetchall())
            )
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        return [*out, Check("sap.scope_matches_data", "warn", f"cannot read the database: {exc}")]
    unseen = [g for g in groups if g not in seen]
    if scope.configured:
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
    unknown_systems = [s for s in systems if charm.system(s) is None]
    out.append(
        Check(
            "sap.transport_systems_known",
            "warn" if unknown_systems else "ok",
            f"transport systems not in config/sap/scope.yaml systems: {', '.join(unknown_systems[:10])}"
            if unknown_systems
            else "every transport system is configured"
            if systems
            else "no transports imported yet",
        )
    )
    unmapped = {
        "transaction types": [
            v for v, _ in raw["transaction_type"].most_common() if v and charm.change_type(v) == OTHER_TYPE
        ],
        "statuses": [v for v, _ in raw["status_raw"].most_common() if v and charm.stage(v) == UNKNOWN_STAGE],
        "components": [v for v, _ in raw["component_raw"].most_common() if v and charm.area(v) == UNASSIGNED],
    }
    problems = [f"{what}: {', '.join(values[:10])}" for what, values in unmapped.items() if values]
    has_changes = any(raw[c] for c in raw)
    out.append(
        Check(
            "sap.charm_values_mapped",
            "warn" if problems else "ok",
            "ChaRM values not in config/sap/charm.yaml (most frequent first): " + "; ".join(problems)
            if problems
            else "every ChaRM transaction type, status and component is mapped"
            if has_changes
            else "no ChaRM changes imported yet",
        )
    )
    idoc_problems = [
        f"{what}: {', '.join(values[:10])}"
        for what, values in (
            ("status codes", [c for c, _ in idoc_codes.most_common() if c and idoc.group(c) == UNKNOWN_GROUP]),
            ("message types", [t for t, _ in idoc_types.most_common() if t and idoc.area(t) == UNASSIGNED]),
            ("systems", [s for s in idoc_systems if charm.system(s) is None]),
        )
        if values
    ]
    out.append(
        Check(
            "sap.idoc_values_known",
            "warn" if idoc_problems else "ok",
            "IDoc values not in config/sap/idoc.yaml or scope.yaml (most frequent first): " + "; ".join(idoc_problems)
            if idoc_problems
            else "every IDoc status code, message type and system is configured"
            if idoc_codes
            else "no IDocs imported yet",
        )
    )
    return out


def _marks(values: list[str]) -> str:
    return ", ".join("?" for _ in values) or "NULL"
