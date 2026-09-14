"""amkit command-line interface.

Every command accepts --profile, --data-dir and --json.
Exit codes: 0 ok, 1 internal error (bug; JSON envelope kind=internal), 2 validation or command-line usage error,
3 busy, 4 precondition. With --json every handled outcome prints exactly one JSON object on stdout.
"""

from __future__ import annotations

import sqlite3
import sys
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import Annotated, Any, TypeVar

import typer

from amkit import __version__, bootstrap, db, doctor
from amkit.errors import EXIT_PRECONDITION, AmkitError, PreconditionFailed
from amkit.output import console, emit, emit_error, ensure_utf8_stdio
from amkit.paths import Paths, get_paths
from amkit.salt import fingerprint, read_salt

app = typer.Typer(add_completion=False, no_args_is_help=True, help="App Owner Toolkit")
db_app = typer.Typer(no_args_is_help=True, help="Database maintenance")
app.add_typer(db_app, name="db")

ProfileOpt = Annotated[str | None, typer.Option("--profile", "-p", help="synthetic | real | eval-<seed>")]
DataDirOpt = Annotated[Path | None, typer.Option("--data-dir", help="Override DATA_DIR (advanced)")]
JsonOpt = Annotated[bool, typer.Option("--json", help="Machine-readable JSON output")]

F = TypeVar("F", bound=Callable[..., Any])


def handle_errors(func: F) -> F:
    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        as_json = bool(kwargs.get("as_json"))
        try:
            return func(*args, **kwargs)
        except typer.Exit:
            raise
        except AmkitError as exc:
            emit_error(exc.to_dict(), as_json)
            raise typer.Exit(exc.exit_code) from exc
        except (sqlite3.DatabaseError, OSError) as exc:
            emit_error({"kind": "precondition", "message": f"{type(exc).__name__}: {exc}"}, as_json)
            raise typer.Exit(EXIT_PRECONDITION) from exc
        except Exception as exc:
            emit_error({"kind": "internal", "message": f"{type(exc).__name__}: {exc}"}, as_json)
            raise typer.Exit(1) from exc

    return wrapper  # type: ignore[return-value]


def _paths(profile: str | None, data_dir: Path | None) -> Paths:
    return get_paths(profile, data_dir)


@app.command()
@handle_errors
def version(as_json: JsonOpt = False) -> None:
    """Print the amkit version."""
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


def _open(paths: Paths):
    if not paths.db.exists():
        raise PreconditionFailed(f"No database at {paths.db}; run `amkit init --profile {paths.profile}` first.")
    return db.connect(paths.db)


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
    """Restore a backup (validated first; refused while `amkit serve` runs; takes a safety backup)."""
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


def main() -> None:
    ensure_utf8_stdio()
    try:
        app()
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
