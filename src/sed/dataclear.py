"""Clear imported data by the system it came from, without touching how SED is set up.

Reloading a source is an ordinary thing to want: an export was wrong, a mapping changed, a system was reconnected.
Until now the only way was to delete the database file by hand, which also took the column mappings, the saved
layouts and the salt with it.

What is removed: rows that came from an import. What is never removed: mappings and config overrides (they live in
files, not the store), saved layouts, the PII salt, branding, and the pseudonym directory — the last because
discarding it would break the link between a pseudonym and the person it stands for, and a later import of the same
data would no longer agree with what came before.

Rows are deleted child-first inside one transaction, so a failure leaves the store exactly as it was.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from sed.errors import ValidationFailed


@dataclass(frozen=True)
class Source:
    key: str
    label: str
    description: str
    # Child tables first: foreign keys are on, so a parent cannot go before the rows pointing at it.
    tables: tuple[str, ...]
    # Columns on tables of *other* sources that point into this one. Cleared to NULL first, so a source can be
    # reloaded on its own; the next import resolves the links again.
    detach: tuple[tuple[str, str], ...] = field(default=())


SOURCES: tuple[Source, ...] = (
    Source(
        "servicenow",
        "ServiceNow",
        "Incidents, requests, changes, problems, SLA records, CI links and support groups",
        ("task_sla", "ci_rel", "ticket", "assignment_group"),
    ),
    Source("jira", "Jira", "Issues", ("work_item",)),
    Source("confluence", "Confluence", "Pages", ("doc_page",)),
    Source(
        "sap",
        "SAP",
        "ChaRM changes, transport imports and IDocs (SAP tickets are ServiceNow tickets, cleared with ServiceNow)",
        ("sap_idoc", "sap_transport_import", "sap_change"),
    ),
    Source(
        "delivery",
        "Delivery",
        "Projects, plan milestones and the RAID log",
        ("delivery_raid", "delivery_milestone", "delivery_project"),
    ),
    Source(
        "commercial",
        "Contracts, licences and costs",
        "The spreadsheets and uploads: contracts, licence inventory and usage, cost actuals and budget",
        ("cost_line", "license_usage", "license", "contract"),
    ),
    Source(
        "portfolio",
        "Applications and vendors",
        "The application list and vendor master. Anything still pointing at them is unlinked, not deleted",
        ("application", "vendor"),
        detach=(
            ("ticket", "app_id"),
            ("ticket", "vendor_id"),
            ("contract", "app_id"),
            ("contract", "vendor_id"),
            ("license", "app_id"),
            ("license", "vendor_id"),
            ("cost_line", "app_id"),
            ("cost_line", "vendor_id"),
            ("ci_rel", "app_id"),
            ("work_item", "app_id"),
        ),
    ),
)
BY_KEY = {s.key: s for s in SOURCES}


def _exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone()
    return row is not None


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(r[1] == column for r in conn.execute(f"PRAGMA table_info({table})"))


def counts(conn: sqlite3.Connection) -> list[dict[str, object]]:
    """What each source currently holds, for a screen that offers to clear it."""
    out = []
    for source in SOURCES:
        rows = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in source.tables if _exists(conn, t)}
        out.append(
            {
                "key": source.key,
                "label": source.label,
                "description": source.description,
                "tables": rows,
                "rows": sum(rows.values()),
            }
        )
    return out


def clear(conn: sqlite3.Connection, key: str, paths: object | None = None) -> dict[str, object]:
    """Delete every row one source imported. Caller wraps this in `db.write_tx`."""
    source = BY_KEY.get(key)
    if source is None:
        raise ValidationFailed(f"Unknown source '{key}'", {"sources": sorted(BY_KEY)})

    detached: dict[str, int] = {}
    for table, column in source.detach:
        if _exists(conn, table) and _has_column(conn, table, column):
            cursor = conn.execute(f"UPDATE {table} SET {column} = NULL WHERE {column} IS NOT NULL")
            if cursor.rowcount:
                detached[f"{table}.{column}"] = cursor.rowcount

    deleted: dict[str, int] = {}
    for table in source.tables:
        if _exists(conn, table):
            cursor = conn.execute(f"DELETE FROM {table}")
            deleted[table] = cursor.rowcount

    # Import history for those tables is part of the data, not the setup: a cleared source must not look imported.
    batches = 0
    if _exists(conn, "import_batch"):
        names = _mapping_names(source, paths)
        if names:
            marks = ",".join("?" for _ in names)
            batches = conn.execute(f"DELETE FROM import_batch WHERE mapping_name IN ({marks})", names).rowcount

    return {
        "source": source.key,
        "label": source.label,
        "deleted": deleted,
        "detached": detached,
        "import_batches": batches,
        "rows": sum(deleted.values()),
    }


def _mapping_names(source: Source, paths: object | None) -> list[str]:
    """Mappings that feed one of this source's tables, so the import history matches what is left in the store."""
    if paths is None:
        return []
    from sed.ingest.mapping import load_all_mappings

    try:
        mappings = load_all_mappings(paths)  # type: ignore[arg-type]
    except Exception:
        return []
    wanted = set(source.tables)
    # A mapping names an ingest target; every target in these groups is a table of the same name.
    return sorted(name for name, spec in mappings.items() if spec.target in wanted)
