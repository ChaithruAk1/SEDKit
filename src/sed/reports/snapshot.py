"""Frozen report snapshots: every number a report shows comes from one immutable facts+tables document.

A snapshot is stored in report_snapshot with a sha256 over its canonical JSON. Builders (XLSX/MD/PPTX) render only
from the snapshot, so narrative, charts and tables in one artifact always agree.

Report content is module-owned: `create_snapshot` resolves the ReportDef through the registry and calls its builder
with a `SnapshotRequest`; the builder returns `SnapshotParts`. AI provenance (runs, AI-derived facts and tables) sits
outside facts/tables so `--ai none` can exclude AI content at render time (`render_view`) without changing the snapshot.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import sqlite3
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any, TypedDict

from sed import __version__, db
from sed.calendar import Period, parse_period
from sed.errors import ValidationFailed
from sed.paths import Paths, repo_root
from sed.settings import Settings, load_settings

UNITS = ("text", "count", "number", "pct", "pp", "ratio", "hours", "eur", "date", "datetime")


class AiRunProvenance(TypedDict):
    run_id: str
    skill: str
    skill_hash: str
    status: str
    model_reported: str | None
    reviewed_by: str | None
    reviewed_at: str | None
    sample_n: int | None
    sample_accuracy: float | None
    sample_ci_low: float | None
    sample_ci_high: float | None
    used_for: list[str]


@dataclass
class Snapshot:
    snapshot_id: str
    report_key: str
    period: str
    vendor_id: str | None
    as_of: str
    data_class: str
    reporting_tz: str
    sla_source: str | None
    facts: dict[str, dict[str, Any]]
    tables: dict[str, dict[str, Any]]
    freshness: list[dict[str, Any]]
    input_batches: list[dict[str, Any]]
    sha256: str
    created_at: str
    git_commit: str | None
    ai_runs: list[AiRunProvenance] = field(default_factory=list)
    ai_derived_tables: list[str] = field(default_factory=list)
    ai_derived_facts: list[str] = field(default_factory=list)
    period_end: str = ""
    data_as_of: str | None = None
    suppressed_findings: list[dict[str, Any]] = field(default_factory=list)
    base_currency: str = "EUR"  # the currency of every "eur"-unit amount (settings.base_currency)


@dataclass(frozen=True)
class SnapshotRequest:
    conn: sqlite3.Connection
    paths: Paths
    settings: Settings
    report: Any  # sed.modules.contract.ReportDef
    period: Period
    vendor_id: str | None
    as_of: date  # min(period.end_local, data_as_of): as_of-dependent calls (findings, renewals, licenses)
    data_as_of: date | None
    window: Period  # the period with end_local clamped to as_of: "to date" aggregates


@dataclass
class SnapshotParts:
    facts: dict[str, dict[str, Any]]
    tables: dict[str, dict[str, Any]]
    sla_source: str | None = None
    freshness: list[dict[str, Any]] | None = None
    ai_runs: list[AiRunProvenance] = field(default_factory=list)
    ai_derived_tables: list[str] = field(default_factory=list)
    ai_derived_facts: list[str] = field(default_factory=list)
    # When set, the provenance lists only suppressed findings about these (subject_type, subject_id) pairs, so a vendor
    # review never shows other vendors' acknowledged findings. None lists every suppressed finding.
    suppressed_subjects: set[tuple[str, str]] | None = None


@dataclass(frozen=True)
class AiParts:
    facts: dict[str, dict[str, Any]]
    ai_runs: list[AiRunProvenance]
    ai_derived_tables: tuple[str, ...]
    ai_derived_facts: tuple[str, ...]


SnapshotBuilder = Callable[[SnapshotRequest], SnapshotParts]


@dataclass(frozen=True)
class RenderView:
    facts: dict[str, dict[str, Any]]
    tables: dict[str, dict[str, Any]]
    excluded_facts: list[str]
    excluded_tables: list[str]
    ai_runs: list[AiRunProvenance]
    note: str | None


def fact(value: Any, unit: str, label: str, definition: str | None = None) -> dict[str, Any]:
    return {"value": value, "unit": unit, "label": label, "definition": definition}


def table(title: str, columns: list[tuple[str, str, str]], rows: list[dict[str, Any]]) -> dict[str, Any]:
    """columns: (key, label, format) with format in text|count|number|pct|ratio|hours|eur|date|datetime."""
    return {
        "title": title,
        "columns": [{"key": k, "label": lbl, "format": fmt} for k, lbl, fmt in columns],
        "rows": [{c[0]: r.get(c[0]) for c in columns} for r in rows],
    }


def format_provenance_line(run: AiRunProvenance) -> str:
    """One human-readable line per AI run, e.g. for the Provenance sheet and deck notes."""
    if run.get("sample_accuracy") is None:
        accuracy = "sample accuracy n/a"
    else:
        accuracy = f"sample accuracy {100 * run['sample_accuracy']:.1f}%"
        if run.get("sample_ci_low") is not None and run.get("sample_ci_high") is not None:
            accuracy += f" ({100 * run['sample_ci_low']:.1f}–{100 * run['sample_ci_high']:.1f}%)"
        if run.get("sample_n"):
            accuracy += f", n={run['sample_n']}"
    return (
        f"{run['skill']} {str(run.get('skill_hash') or '')[:12]} run {run['run_id']}, "
        f"approved by {run.get('reviewed_by') or 'n/a'} at {run.get('reviewed_at') or 'n/a'}: {accuracy}"
    )


def render_view(snapshot: Snapshot, ai_mode: str) -> RenderView:
    """What a renderer may show. `none` drops AI-derived facts and tables; `approved` and `draft` show everything."""
    if ai_mode != "none":
        return RenderView(dict(snapshot.facts), dict(snapshot.tables), [], [], list(snapshot.ai_runs), None)
    excluded_facts = [k for k in snapshot.ai_derived_facts if k in snapshot.facts]
    excluded_tables = [k for k in snapshot.ai_derived_tables if k in snapshot.tables]
    return RenderView(
        {k: v for k, v in snapshot.facts.items() if k not in excluded_facts},
        {k: v for k, v in snapshot.tables.items() if k not in excluded_tables},
        excluded_facts,
        excluded_tables,
        [],
        "AI content excluded (--ai none)",
    )


CURRENCY_SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£"}


def money_prefix(currency: str | None) -> str:
    """Prefix for amounts in the base currency: a symbol for EUR, USD and GBP, else the ISO code and a space."""
    code = (currency or "EUR").upper()
    return CURRENCY_SYMBOLS.get(code, f"{code} ")


def money_num_format(currency: str | None) -> str:
    """Excel / chart number format for whole amounts in the base currency."""
    prefix = money_prefix(currency)
    return f"{prefix}#,##0" if prefix.strip() in CURRENCY_SYMBOLS.values() else f'"{prefix}"#,##0'


def _git_commit() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_root(),
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _suppressed_findings(
    conn: sqlite3.Connection,
    as_of: date,
    subjects: set[tuple[str, str]] | None = None,
    kinds: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    """Acknowledged or suppressed rule findings for a report's provenance: only the report module's kinds (when given)
    and, for subject-scoped reports, only those subjects."""
    rows = conn.execute(
        "SELECT stable_key, kind, title, status, suppress_until, subject_type, subject_id FROM finding "
        "WHERE origin = 'rule' AND (status = 'acknowledged' OR (status = 'active' AND suppress_until > ?)) "
        "ORDER BY kind, title",
        (as_of.isoformat(),),
    ).fetchall()
    keys = ("stable_key", "kind", "title", "status", "suppress_until")
    return [
        {k: r[k] for k in keys}
        for r in rows
        if (subjects is None or (r["subject_type"], r["subject_id"]) in subjects)
        and (kinds is None or r["kind"] in kinds)
    ]


def build_request(
    conn: sqlite3.Connection, paths: Paths, report_key: str, period_label: str, vendor_id: str | None = None
) -> SnapshotRequest:
    """Validate a report request and derive as_of/window. Raises exit 2 (validation) or 4 (module disabled)."""
    from sed.ingest.freshness import data_as_of
    from sed.modules import report, require_enabled

    module, rdef = report(report_key)
    require_enabled(paths, module.key)
    settings = load_settings(paths)
    period = parse_period(period_label, settings.reporting_tz, settings.fiscal_year_start)
    if period.kind not in rdef.period_kinds:
        raise ValidationFailed(
            f"The {report_key} report needs a {' or '.join(rdef.period_kinds)} period (got {period.label})"
        )
    if rdef.needs_vendor and not vendor_id:
        raise ValidationFailed(f"The {report_key} report needs --vendor")
    if vendor_id and not rdef.needs_vendor:
        raise ValidationFailed(f"The {report_key} report does not take --vendor")
    if vendor_id and not conn.execute("SELECT 1 FROM vendor WHERE vendor_id = ?", (vendor_id,)).fetchone():
        raise ValidationFailed(f"Unknown vendor '{vendor_id}'")
    data_date = data_as_of(conn, settings)
    as_of = min(period.end_local, data_date) if data_date else period.end_local
    window = dataclasses.replace(period, end_local=max(period.start_local, min(period.end_local, as_of)))
    return SnapshotRequest(conn, paths, settings, rdef, period, vendor_id, as_of, data_date, window)


def create_snapshot(
    conn: sqlite3.Connection, paths: Paths, report_key: str, period_label: str, vendor_id: str | None = None
) -> Snapshot:
    from sed.ingest.freshness import import_freshness
    from sed.modules import load_ref, report

    req = build_request(conn, paths, report_key, period_label, vendor_id)
    module_kinds = tuple(report(report_key)[0].finding_kinds)
    parts: SnapshotParts = load_ref(req.report.builder)(req)
    meta = db.all_meta(conn)
    freshness = parts.freshness if parts.freshness is not None else import_freshness(conn)
    batches = [
        dict(r)
        for r in conn.execute(
            "SELECT batch_id, file_name, file_sha256, mapping_name, imported_at FROM import_batch "
            "WHERE status = 'completed' ORDER BY batch_id"
        )
    ]
    provenance = {
        "ai_runs": parts.ai_runs,
        "ai_derived_tables": list(parts.ai_derived_tables),
        "ai_derived_facts": list(parts.ai_derived_facts),
        "period_end": req.period.end_local.isoformat(),
        "data_as_of": req.data_as_of.isoformat() if req.data_as_of else None,
        "suppressed_findings": _suppressed_findings(conn, req.as_of, parts.suppressed_subjects, module_kinds),
        "base_currency": req.settings.base_currency,
    }
    body = {
        "report_key": report_key,
        "period": req.period.label,
        "vendor_id": vendor_id,
        "as_of": req.as_of.isoformat(),
        "data_class": meta.get("data_class", "synthetic"),
        "reporting_tz": req.settings.reporting_tz,
        "sla_source": parts.sla_source,
        "facts": parts.facts,
        "tables": parts.tables,
        "provenance": provenance,
        # A new import always gives a new snapshot, so the stored freshness and batches match what was rendered.
        "input_batches": [b["batch_id"] for b in batches],
    }
    canonical = json.dumps(body, sort_keys=True, ensure_ascii=False, default=str)
    sha = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    created = db.utc_now()
    snapshot_id = f"snap-{report_key}-{req.period.label}{'-' + vendor_id if vendor_id else ''}-{sha[:12]}"
    snap = Snapshot(
        snapshot_id=snapshot_id,
        report_key=report_key,
        period=req.period.label,
        vendor_id=vendor_id,
        as_of=body["as_of"],
        data_class=body["data_class"],
        reporting_tz=req.settings.reporting_tz,
        sla_source=parts.sla_source,
        facts=parts.facts,
        tables=parts.tables,
        freshness=freshness,
        input_batches=batches,
        sha256=sha,
        created_at=created,
        git_commit=_git_commit(),
        ai_runs=list(parts.ai_runs),
        ai_derived_tables=list(parts.ai_derived_tables),
        ai_derived_facts=list(parts.ai_derived_facts),
        period_end=provenance["period_end"],
        data_as_of=provenance["data_as_of"],
        suppressed_findings=provenance["suppressed_findings"],
        base_currency=req.settings.base_currency,
    )
    with db.write_tx(conn):
        cur = conn.execute(
            "INSERT OR IGNORE INTO report_snapshot (snapshot_id, report_key, period, vendor_id, as_of, created_at, "
            "git_commit, facts_json, tables_json, sla_source, reporting_tz, freshness_json, input_batches_json, "
            "data_class, sha256, provenance_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                snapshot_id,
                report_key,
                req.period.label,
                vendor_id,
                snap.as_of,
                created,
                snap.git_commit,
                json.dumps(parts.facts, ensure_ascii=False, default=str),
                json.dumps(parts.tables, ensure_ascii=False, default=str),
                parts.sla_source,
                req.settings.reporting_tz,
                json.dumps(freshness, default=str),
                json.dumps([b["batch_id"] for b in batches]),
                snap.data_class,
                sha,
                json.dumps(provenance, ensure_ascii=False, default=str),
            ),
        )
        if cur.rowcount == 0:  # the same snapshot exists: artifacts must show what is stored for it
            stored = conn.execute(
                "SELECT created_at, git_commit, freshness_json FROM report_snapshot WHERE snapshot_id = ?",
                (snapshot_id,),
            ).fetchone()
            snap = dataclasses.replace(
                snap,
                created_at=stored["created_at"],
                git_commit=stored["git_commit"],
                freshness=json.loads(stored["freshness_json"] or "[]"),
            )
    return snap


def sed_version() -> str:
    return __version__
