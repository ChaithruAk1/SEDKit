"""sed command-line interface.

Every command accepts --json; commands that work on one profile also accept --profile and --data-dir.
Exit codes: 0 ok, 1 internal error (bug; JSON envelope kind=internal), 2 validation or command-line usage error,
3 busy, 4 precondition. With --json every handled outcome prints exactly one JSON object on stdout.
"""

from __future__ import annotations

import re
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Annotated, Any

import typer

from sed import __version__, bootstrap, db, doctor, relocate
from sed.cli_common import (
    DataDirOpt,
    JsonOpt,
    ProfileOpt,
    handle_errors,
    open_db,
    parse_date,
    paths_for,
)
from sed.errors import EXIT_PRECONDITION, PreconditionFailed, SedError, ValidationFailed
from sed.output import console, emit, ensure_utf8_stdio
from sed.salt import fingerprint, read_salt

app = typer.Typer(add_completion=False, no_args_is_help=True, help="SED")
db_app = typer.Typer(no_args_is_help=True, help="Database maintenance")
app.add_typer(db_app, name="db")

# Legacy private names kept for readability of the commands below.
_paths = paths_for
_open = open_db
_parse_date = parse_date


@app.command()
@handle_errors
def version(as_json: JsonOpt = False) -> None:
    """Print the sed version."""
    emit({"version": __version__}, as_json, lambda p: console().print(p["version"]))


@app.command()
@handle_errors
def init(
    profile: ProfileOpt = None,
    data_dir: DataDirOpt = None,
    new_salt: Annotated[bool, typer.Option("--new-salt", help="Create the PII salt (new profiles only)")] = False,
    ai_approval_note: Annotated[
        str | None, typer.Option("--ai-approval-note", help="Record who approved AI use on this profile's data")
    ] = None,
    pii_mode: Annotated[str | None, typer.Option("--pii-mode", help="pseudonymize | drop | keep (synthetic)")] = None,
    no_claude_settings: Annotated[
        bool, typer.Option("--no-claude-settings", help="Do not write .claude/settings.local.json / CLAUDE.md")
    ] = False,
    as_json: JsonOpt = False,
) -> None:
    """Create or upgrade a profile: DATA_DIR, database, meta, salt, Claude Code wiring."""
    result = bootstrap.init_profile(
        _paths(profile, data_dir),
        new_salt=new_salt,
        ai_approval_note=ai_approval_note,
        pii_mode=pii_mode,
        write_claude_settings=not no_claude_settings,
    )

    def human(p: dict[str, Any]) -> None:
        c = console()
        c.print(f"[bold]Profile[/] {p['profile']}  ->  {p['data_dir']}")
        c.print(
            f"DB {p['db']} (created={p['created_db']}, journal={p['journal_mode']}, "
            f"schema v{p['migration']['to_version']})"
        )
        if p["salt_created"]:
            c.print(f"[yellow]New PII salt written to {p['salt_file']}. Back this file up now.[/]")
        for w in p["warnings"]:
            c.print(f"[yellow]warning:[/] {w}")

    emit(result, as_json, human)


@app.command("doctor")
@handle_errors
def doctor_cmd(profile: ProfileOpt = None, data_dir: DataDirOpt = None, as_json: JsonOpt = False) -> None:
    """Check environment, DATA_DIR location, schema, salt, hooks and Claude Code wiring."""
    paths = _paths(profile, data_dir)
    summary = doctor.summarize(doctor.run_checks(paths))
    summary["profile"] = paths.profile

    def human(p: dict[str, Any]) -> None:
        colors = {"ok": "green", "warn": "yellow", "fail": "red"}
        for chk in p["checks"]:
            console().print(f"[{colors[chk['status']]}]{chk['status']:>4}[/]  {chk['name']:<32} {chk['detail']}")
        console().print(f"\n[bold]{p['status'].upper()}[/] {p['counts']}")

    failed = summary["status"] == "fail"
    emit(summary, as_json, human, ok=not failed)
    if failed:
        raise typer.Exit(EXIT_PRECONDITION)


@db_app.command("migrate")
@handle_errors
def db_migrate(profile: ProfileOpt = None, data_dir: DataDirOpt = None, as_json: JsonOpt = False) -> None:
    """Apply pending schema migrations (takes a backup first)."""
    paths = _paths(profile, data_dir)
    conn = _open(paths)
    try:
        result = db.migrate(conn, paths.db, paths.backups)
    finally:
        conn.close()
    emit(result, as_json)


@db_app.command("backup")
@handle_errors
def db_backup(
    profile: ProfileOpt = None,
    data_dir: DataDirOpt = None,
    keep: Annotated[int, typer.Option(min=1, help="Backups to keep (>= 1)")] = 7,
    as_json: JsonOpt = False,
) -> None:
    """Online backup via the SQLite backup API."""
    paths = _paths(profile, data_dir)
    conn = _open(paths)
    try:
        target = db.backup(conn, paths.backups, reason="manual", keep=keep)
    finally:
        conn.close()
    emit({"backup": str(target)}, as_json, lambda p: console().print(f"Backup written: {p['backup']}"))


@db_app.command("restore")
@handle_errors
def db_restore(
    file: Annotated[Path, typer.Argument(help="Backup file to restore")],
    profile: ProfileOpt = None,
    data_dir: DataDirOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Restore a backup (validated first; refused while `sed serve` runs; takes a safety backup)."""
    paths = _paths(profile, data_dir)
    expected_fp = None
    if paths.db.exists():
        conn = db.connect(paths.db, readonly=True)
        try:
            expected_fp = db.get_meta(conn, "salt_fingerprint")
        finally:
            conn.close()
    if expected_fp is None:
        salt = read_salt(paths.salt_file)
        expected_fp = fingerprint(salt) if salt else None
    result = db.restore(
        file,
        paths.db,
        paths.backups,
        paths.serve_lock,
        expected_data_class=paths.data_class,
        expected_salt_fingerprint=expected_fp,
    )
    emit(result, as_json)


@db_app.command("info")
@handle_errors
def db_info(profile: ProfileOpt = None, data_dir: DataDirOpt = None, as_json: JsonOpt = False) -> None:
    """Show schema version, journal mode, meta and row counts."""
    paths = _paths(profile, data_dir)
    conn = db.connect(paths.db, readonly=True)
    try:
        result = db.info(conn, paths.db)
    finally:
        conn.close()
    emit(result, as_json)


data_app = typer.Typer(no_args_is_help=True, help="Where sed keeps its data")
app.add_typer(data_app, name="data")


@data_app.command("move")
@handle_errors
def data_move(
    to: Annotated[Path, typer.Option("--to", help="New data root: a new or empty local folder")],
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Run the checks and count the files; change nothing")
    ] = False,
    as_json: JsonOpt = False,
) -> None:
    """Copy the whole data root (every profile) to a new folder, verify it and mark the old one as moved."""
    result = relocate.move_data_root(to, dry_run=dry_run)

    def human(p: dict[str, Any]) -> None:
        c = console()
        c.print(f"{'Would copy' if p['dry_run'] else 'Copied'} {p['files']} files ({p['bytes'] / 1_048_576:,.1f} MB)")
        c.print(f"  from {p['from']}")
        if p["physical_from"] != p["from"]:
            c.print(f"       (really {p['physical_from']})")
        c.print(f"  to   {p['to']}")
        for v in p["verified"]:
            c.print(f"Verified profile {v['profile']}: {v['tables']} tables, {v['rows']:,} rows")
        for w in p["warnings"]:
            c.print(f"[yellow]warning:[/] {w}")
        for i, step in enumerate(p.get("next_steps", []), start=1):
            c.print(f"{i}. {step}")

    emit(result, as_json, human)


# ---------------------------------------------------------------------------
# synthetic data, import, mappings, aliases
# ---------------------------------------------------------------------------

mappings_app = typer.Typer(no_args_is_help=True, help="Column mappings")
alias_app = typer.Typer(no_args_is_help=True, help="Alias table (raw names -> canonical ids)")
inbox_app = typer.Typer(no_args_is_help=True, help="Inbox housekeeping")
app.add_typer(mappings_app, name="mappings")
app.add_typer(alias_app, name="alias")
app.add_typer(inbox_app, name="inbox")


@app.command()
@handle_errors
def synth(
    profile: ProfileOpt = None,
    data_dir: DataDirOpt = None,
    seed: Annotated[int, typer.Option(help="Random seed")] = 42,
    as_of: Annotated[str, typer.Option("--as-of", help="Export moment date YYYY-MM-DD")] = "2026-09-01",
    anchor: Annotated[str | None, typer.Option(help="Pattern anchor date (default = as-of)")] = None,
    months: Annotated[int, typer.Option(min=3, max=36, help="Months of history")] = 18,
    scale: Annotated[float, typer.Option(min=0.001, max=5.0, help="Background volume multiplier")] = 1.0,
    no_clean: Annotated[bool, typer.Option("--no-clean", help="Keep previously generated inbox files")] = False,
    module: Annotated[str | None, typer.Option("--module", help="Only this module's generator (default: all)")] = None,
    as_json: JsonOpt = False,
) -> None:
    """Generate synthetic exports (real field names, planted patterns) into the profile inbox."""
    from sed import modules
    from sed.modules.contract import SynthRequest

    paths = _paths(profile, data_dir)
    if not paths.db.exists():
        raise PreconditionFailed(f"Run `sed init --profile {paths.profile}` first.")
    req = SynthRequest(
        seed=seed, as_of=_parse_date(as_of), anchor=_parse_date(anchor), months=months, scale=scale, clean=not no_clean
    )
    targets = [modules.require_enabled(paths, module)] if module else [m for m in modules.enabled(paths) if m.synth]
    if not targets or any(m.synth is None for m in targets):
        raise ValidationFailed("No synthetic data generator for the selected module(s)")
    results = {m.key: modules.load_ref(m.synth.generate)(paths, req) for m in targets}
    result = next(iter(results.values())) if len(results) == 1 else results

    def human(p: dict[str, Any]) -> None:
        for payload in [p] if "files" in p else list(p.values()):
            console().print(f"Wrote {payload['files']} files to {payload['inbox']} {payload['counts']}")

    emit(result, as_json, human)


@app.command("import")
@handle_errors
def import_cmd(
    files: Annotated[
        list[Path] | None, typer.Argument(help="Files to import (default: everything in the inbox)")
    ] = None,
    profile: ProfileOpt = None,
    data_dir: DataDirOpt = None,
    inbox: Annotated[bool, typer.Option("--inbox", help="Also import everything in the inbox")] = False,
    mapping: Annotated[str | None, typer.Option(help="Force a mapping by name")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Map and validate without writing")] = False,
    force: Annotated[bool, typer.Option("--force", help="Allow >20% disappearance in full snapshots")] = False,
    as_of: Annotated[str | None, typer.Option("--as-of", help="Override the snapshot as-of date")] = None,
    allow_unmanifested: Annotated[
        bool, typer.Option("--allow-unmanifested", help="Synthetic profile: accept files not in the generator manifest")
    ] = False,
    keep_files: Annotated[bool, typer.Option("--keep-files", help="Do not move imported files to processed/")] = False,
    as_json: JsonOpt = False,
) -> None:
    """Import exports: map columns, pseudonymize, resolve references, upsert, record data quality.

    `sed import reresolve` re-links rows from stored raw values after aliases change.
    """
    from sed.ingest.loader import ImportOptions, reresolve, run_import

    paths = _paths(profile, data_dir)
    if files and len(files) == 1 and str(files[0]) == "reresolve" and not files[0].exists():
        emit({"reresolved": reresolve(paths)}, as_json)
        return
    opts = ImportOptions(
        files=list(files or []),
        inbox=inbox,
        mapping=mapping,
        dry_run=dry_run,
        force=force,
        as_of=_parse_date(as_of),
        allow_unmanifested=allow_unmanifested,
        move_files=not keep_files,
    )
    result = run_import(paths, opts)

    def human(p: dict[str, Any]) -> None:
        c = console()
        for f in p["files"]:
            if f["status"] == "error":
                c.print(f"[red]error[/]  {f['file']}: {f['error']['message']}")
            else:
                c.print(
                    f"[green]{f['status']:>9}[/]  {f['file']:<42} {f.get('mapping', ''):<28} read={f.get('rows_read')} "
                    f"ins={f.get('inserted', '-')} upd={f.get('updated', '-')} rej={f.get('rows_rejected')}"
                )
        for s in p["skipped"]:
            c.print(f"[yellow]  skipped[/]  {s['file']}: {s['reason']}")
        c.print(f"\n{p['summary']}")

    emit(result, as_json, human, ok=result["summary"]["errors"] == 0)
    if result["summary"]["errors"]:
        raise typer.Exit(2)


@mappings_app.command("list")
@handle_errors
def mappings_list(profile: ProfileOpt = None, data_dir: DataDirOpt = None, as_json: JsonOpt = False) -> None:
    """List effective mappings (repo defaults + DATA_DIR overrides)."""
    from sed.ingest.mapping import load_all_mappings

    paths = _paths(profile, data_dir)
    rows = [
        {
            "name": m.name,
            "target": m.target,
            "load_mode": m.load_mode,
            "glob": m.match.glob,
            "fields": len(m.fields),
            "required": m.required_fields(),
        }
        for m in load_all_mappings(paths).values()
    ]
    emit({"mappings": rows}, as_json)


@mappings_app.command("check")
@handle_errors
def mappings_check(
    file: Annotated[Path, typer.Argument(help="Export file to score against all mappings")],
    profile: ProfileOpt = None,
    data_dir: DataDirOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Score a file against every mapping and show missing required fields."""
    from sed.ingest.mapping import (
        glob_matches,
        header_match_score,
        load_all_mappings,
        read_for_mapping,
        resolve_columns,
    )

    paths = _paths(profile, data_dir)
    out = []
    for spec in load_all_mappings(paths).values():
        try:
            table = read_for_mapping(file, spec)
        except SedError as exc:
            out.append({"mapping": spec.name, "error": exc.message})
            continue
        index, missing = resolve_columns(spec, table.columns)
        out.append(
            {
                "mapping": spec.name,
                "glob_match": glob_matches(spec, file.name),
                "score": round(header_match_score(spec, table.columns), 3),
                "missing_required": missing,
                "unmapped_columns": [c for i, c in enumerate(table.columns) if i not in index.values()][:30],
                "reader_warnings": table.warnings,
            }
        )
    out.sort(key=lambda r: (not r.get("glob_match", False), -r.get("score", 0)))
    emit({"file": str(file), "candidates": out}, as_json)


@alias_app.command("list")
@handle_errors
def alias_list(
    kind: Annotated[str | None, typer.Option(help="Filter by kind")] = None,
    unmapped: Annotated[bool, typer.Option("--unmapped", help="Show unresolved raw values with suggestions")] = False,
    profile: ProfileOpt = None,
    data_dir: DataDirOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """List aliases, or unresolved values with fuzzy suggestions (--unmapped)."""
    paths = _paths(profile, data_dir)
    conn = db.connect(paths.db, readonly=True)
    try:
        if unmapped:
            sql = "SELECT kind, raw_value, occurrences, suggestion, score FROM unmapped_value WHERE resolved = 0"
            params: tuple[Any, ...] = ()
            if kind:
                sql += " AND kind = ?"
                params = (kind,)
            rows = [dict(r) for r in conn.execute(sql + " ORDER BY occurrences DESC LIMIT 500", params)]
        else:
            sql = "SELECT kind, alias_norm, target_id, origin FROM alias"
            params = ()
            if kind:
                sql += " WHERE kind = ?"
                params = (kind,)
            rows = [dict(r) for r in conn.execute(sql + " ORDER BY kind, alias_norm", params)]
    finally:
        conn.close()
    emit({"rows": rows}, as_json)


@alias_app.command("assign")
@handle_errors
def alias_assign(
    kind: Annotated[
        str, typer.Argument(help="app | vendor | group | ci | jira_project | jira_component | confluence_space")
    ],
    raw: Annotated[str, typer.Argument(help="Raw value as it appears in exports")],
    target: Annotated[str, typer.Argument(help="Canonical id (app_id / vendor_id / group name) or its exact name")],
    profile: ProfileOpt = None,
    data_dir: DataDirOpt = None,
    no_reresolve: Annotated[bool, typer.Option("--no-reresolve", help="Do not re-link existing rows")] = False,
    as_json: JsonOpt = False,
) -> None:
    """Assign an alias manually (never overwritten by imports) and re-link existing rows."""
    from sed.ingest.aliases import assign_alias

    result = assign_alias(
        _paths(profile, data_dir), kind, raw, target, reviewer=bootstrap.reviewer_name(), reresolve=not no_reresolve
    )
    emit(result, as_json)


@alias_app.command("suggest")
@handle_errors
def alias_suggest(profile: ProfileOpt = None, data_dir: DataDirOpt = None, as_json: JsonOpt = False) -> None:
    """Recompute fuzzy suggestions for unresolved values."""
    from sed.ingest.resolve import mark_resolved, refresh_suggestions

    paths = _paths(profile, data_dir)
    conn = db.connect(paths.db)
    try:
        with db.write_tx(conn):
            resolved = mark_resolved(conn)
            updated = refresh_suggestions(conn)
    finally:
        conn.close()
    emit({"suggestions_updated": updated, "marked_resolved": resolved}, as_json)


@alias_app.command("reresolve")
@handle_errors
def alias_reresolve(profile: ProfileOpt = None, data_dir: DataDirOpt = None, as_json: JsonOpt = False) -> None:
    """Re-link rows from stored raw values (same as `sed import reresolve`)."""
    from sed.ingest.loader import reresolve

    emit({"reresolved": reresolve(_paths(profile, data_dir))}, as_json)


@inbox_app.command("prune")
@handle_errors
def inbox_prune(
    older_than: Annotated[str, typer.Option("--older-than", help="Age like 90d")] = "90d",
    profile: ProfileOpt = None,
    data_dir: DataDirOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Delete processed raw export files older than the given age (the database keeps the imported data)."""
    import shutil
    import time

    m = re.fullmatch(r"(\d+)d", older_than.strip())
    if not m:
        raise ValidationFailed("--older-than must look like 90d")
    cutoff = time.time() - int(m.group(1)) * 86400
    paths = _paths(profile, data_dir)
    removed = []
    if paths.processed.is_dir():
        for folder in sorted(paths.processed.iterdir()):
            if folder.is_dir() and folder.stat().st_mtime < cutoff:
                shutil.rmtree(folder)
                removed.append(folder.name)
    emit({"removed_folders": removed}, as_json)


# ---------------------------------------------------------------------------
# metrics, analytics, reports
# ---------------------------------------------------------------------------

metrics_app = typer.Typer(no_args_is_help=True, help="Deterministic metrics")
analytics_app = typer.Typer(no_args_is_help=True, help="Rule-origin findings")
app.add_typer(metrics_app, name="metrics")
app.add_typer(analytics_app, name="analytics")

METRIC_NAMES = [
    "overview",
    "sla",
    "mttr",
    "backlog",
    "volumes",
    "changes",
    "renewals",
    "licenses",
    "costs",
    "vendors",
    "quiet-apps",
    "freshness",
    "flow",
]


def _latest_data_date(conn) -> date:
    latest = conn.execute("SELECT MAX(sys_updated_on) FROM ticket").fetchone()[0]
    if not latest:
        return date.today()
    return date.fromisoformat(latest[:10])


@metrics_app.command("show")
@handle_errors
def metrics_show(
    name: Annotated[str, typer.Argument(help=" | ".join(METRIC_NAMES))],
    period: Annotated[str | None, typer.Option(help="2026-W35, 2026-08, 2026-Q3 (default: last full week)")] = None,
    as_of: Annotated[str | None, typer.Option("--as-of", help="YYYY-MM-DD (default: latest ticket update)")] = None,
    app_id: Annotated[list[str] | None, typer.Option("--app", help="Filter by app_id (repeatable)")] = None,
    family: Annotated[str | None, typer.Option(help="Filter by application family")] = None,
    vendor: Annotated[str | None, typer.Option(help="Filter by vendor_id")] = None,
    group: Annotated[str | None, typer.Option(help="Filter by assignment group")] = None,
    kind: Annotated[str, typer.Option(help="Ticket kind")] = "incident",
    days: Annotated[int, typer.Option(help="Window for renewals")] = 180,
    profile: ProfileOpt = None,
    data_dir: DataDirOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Compute one metric family from the store."""
    from sed import metrics
    from sed.calendar import parse_period, week_label
    from sed.settings import load_settings

    if name not in METRIC_NAMES:
        raise ValidationFailed(f"Unknown metric '{name}'", {"available": METRIC_NAMES})
    paths = _paths(profile, data_dir)
    settings = load_settings(paths)
    conn = db.connect(paths.db, readonly=True)
    try:
        ref = _parse_date(as_of) or _latest_data_date(conn)
        label = period or week_label(ref - timedelta(days=ref.isoweekday()))
        p = parse_period(label, settings.reporting_tz, settings.fiscal_year_start)
        f = metrics.Filters(kind=kind, app_ids=list(app_id or []), family=family, vendor_id=vendor, group=group)
        if name == "overview":
            result: Any = {
                "volumes": metrics.volume_trend(conn, f, [p]),
                "sla": metrics.sla(conn, f, p),
                "mttr": metrics.mttr(conn, f, p),
                "backlog_total": metrics.backlog(conn, f, p.end_utc)["total"],
                "quality": metrics.quality(conn, f, p),
                "stale_open": metrics.stale_open_count(conn, f),
            }
        elif name == "sla":
            result = metrics.sla(conn, f, p)
        elif name == "mttr":
            result = metrics.mttr(conn, f, p)
        elif name == "backlog":
            result = metrics.backlog(conn, f, p.end_utc)
        elif name == "volumes":
            result = metrics.volume_trend(conn, f, [p.previous(k) for k in range(11, 0, -1)] + [p])
        elif name == "flow":
            result = metrics.group_flow(conn, f, [p.previous(k) for k in range(9, 0, -1)] + [p])
        elif name == "changes":
            result = metrics.changes(conn, p, list(app_id or []) or None)
        elif name == "renewals":
            result = metrics.renewals(conn, ref, days)
        elif name == "licenses":
            result = metrics.license_utilization(conn, ref)
        elif name == "costs":
            months = (
                [p.start_local.strftime("%Y-%m")]
                if p.kind == "month"
                else [f"{d.year}-{d.month:02d}" for d in (p.start_local, p.last_day)]
            )
            result = metrics.cost_vs_budget(conn, sorted(set(months)), "app")
        elif name == "vendors":
            result = metrics.vendor_sla_trend(conn, ref, settings.reporting_tz)
        elif name == "quiet-apps":
            result = metrics.quiet_apps(conn, ref, 5, 50_000)
        else:
            result = metrics.freshness(conn)
    finally:
        conn.close()
    emit({"metric": name, "period": p.label, "as_of": ref.isoformat(), "result": result}, as_json)


@app.command()
@handle_errors
def attention(
    as_of: Annotated[str | None, typer.Option("--as-of", help="YYYY-MM-DD (default: latest ticket update)")] = None,
    limit: Annotated[int, typer.Option(min=1, max=1000)] = 50,
    profile: ProfileOpt = None,
    data_dir: DataDirOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Open incidents needing attention now (P1/P2, near/past SLA, aged, reopened, ping-pong, unassigned)."""
    from datetime import UTC, datetime

    from sed import metrics
    from sed.settings import load_settings

    paths = _paths(profile, data_dir)
    settings = load_settings(paths)
    conn = db.connect(paths.db, readonly=True)
    try:
        ref = _parse_date(as_of)
        moment = datetime.combine(ref, datetime.min.time(), tzinfo=UTC) if ref else datetime.now(UTC)
        if not ref:
            latest = conn.execute("SELECT MAX(sys_updated_on) FROM ticket").fetchone()[0]
            if latest:
                moment = min(moment, datetime.strptime(latest, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC))
        result = metrics.attention(conn, metrics.Filters(), moment, settings.thresholds, limit=limit)
        freshness = conn.execute("SELECT MAX(imported_at) FROM import_batch WHERE status = 'completed'").fetchone()[0]
    finally:
        conn.close()
    emit({"as_of": moment.isoformat(), "data_as_of_last_import": freshness, **result}, as_json)


@analytics_app.command("refresh")
@handle_errors
def analytics_refresh(
    as_of: Annotated[str | None, typer.Option("--as-of", help="YYYY-MM-DD (default: latest ticket update)")] = None,
    force: Annotated[bool, typer.Option("--force", help="Rewind the persisted state to an older --as-of")] = False,
    profile: ProfileOpt = None,
    data_dir: DataDirOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Recompute the rule-origin findings of every enabled module (ops: renewals, licenses, vendor SLA, cost, quiet
    apps; sap: area backlog)."""
    from sed import rule_findings

    paths = _paths(profile, data_dir)
    conn = db.connect(paths.db)
    try:
        ref = _parse_date(as_of) or _latest_data_date(conn)
        stats = rule_findings.refresh_enabled(conn, paths, ref, force=force)
        published = rule_findings.published(conn, ref)
    finally:
        conn.close()
    emit(
        {
            "as_of": ref.isoformat(),
            "stats": stats,
            "published": [{k: f[k] for k in ("severity", "kind", "title", "stable_key")} for f in published],
        },
        as_json,
    )


@app.command()
@handle_errors
def serve(
    port: Annotated[int | None, typer.Option(help="Port on 127.0.0.1 (default: settings api.port)")] = None,
    no_browser: Annotated[bool, typer.Option("--no-browser", help="Do not open the dashboard in a browser")] = False,
    dev: Annotated[bool, typer.Option("--dev", help="Use SED_DEV_TOKEN for the Vite dev proxy")] = False,
    profile: ProfileOpt = None,
    data_dir: DataDirOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Serve the local API and dashboard on 127.0.0.1 (per-launch token for write requests)."""
    from sed.api.serve import run
    from sed.settings import load_settings

    paths = _paths(profile, data_dir)
    run(paths, port=port or load_settings(paths).api.port, open_browser=not no_browser, dev=dev)


def _mount_core_subapps() -> None:
    from sed import modules
    from sed.ai.cli import ai_app, review_app
    from sed.modules.cli import modules_app
    from sed.reports.cli import report_app

    app.add_typer(report_app, name="report")
    app.add_typer(modules_app, name="modules")
    app.add_typer(ai_app, name="ai")
    app.add_typer(review_app, name="review")
    for module in modules.installed():
        for mount in module.cli:
            app.add_typer(modules.load_ref(mount.app), name=mount.name)


_mount_core_subapps()


def main() -> None:
    ensure_utf8_stdio()
    try:
        app()
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
