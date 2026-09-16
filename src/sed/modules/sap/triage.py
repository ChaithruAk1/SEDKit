"""The SAP contribution to AI triage (extension point `ops.triage`, see sed.modules.ops.ai.extensions).

SAP tickets (config/sap/scope.yaml) get a `sap` object in their packet line with the labels of their SAP area and
landscape (left out when unassigned or unknown), and may take the SAP subcategories of config/sap/taxonomy.yaml.
The skill hash covers the subcategories, the guide and the area and landscape labels; group names and the other scope
criteria only decide which tickets are SAP tickets.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from sed.modules.ops.ai.extensions import Subcategory, TriageExtension
from sed.modules.sap.scope import UNASSIGNED, UNKNOWN, load_scope
from sed.modules.sap.taxonomy import load_sap_taxonomy
from sed.paths import Paths

KEY = "sap"
FIELDS = (
    ("area", "SAP area of the L3 team holding the ticket, e.g. FI/CO or EWM; missing when not known"),
    ("landscape", "SAP landscape of the application, e.g. ECC or S/4HANA; missing when not known"),
)


def extension(paths: Paths) -> TriageExtension:
    scope = load_scope(paths)
    taxonomy = load_sap_taxonomy(paths)

    def context(conn: sqlite3.Connection, ticket_ids: list[str]) -> dict[str, dict[str, Any]]:
        if not scope.configured or not ticket_ids:
            return {}
        wanted = set(ticket_ids)
        out: dict[str, dict[str, Any]] = {}
        for ticket_id, group, app_id in scope.resolve(conn).tickets or ():
            if ticket_id not in wanted:
                continue
            area = scope.area_of(group)
            landscape = scope.app_landscape.get(app_id or "", UNKNOWN)
            obj: dict[str, Any] = {}
            if area != UNASSIGNED:
                obj["area"] = scope.area_labels[area]
            if landscape != UNKNOWN:
                obj["landscape"] = scope.landscape_labels[landscape]
            out[ticket_id] = obj
        return out

    return TriageExtension(
        key=KEY,
        title="SAP",
        subcategories=tuple(Subcategory(s.code, s.category, s.description) for s in taxonomy.subcategories),
        fields=FIELDS,
        guide=taxonomy.guide,
        context=context,
        config={
            "areas": dict(sorted(scope.area_labels.items())),
            "landscapes": dict(sorted(scope.landscape_labels.items())),
        },
    )
