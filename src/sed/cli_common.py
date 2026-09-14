"""Shared CLI plumbing for the core CLI and module sub-apps: common options, error handling, path helpers.

Keep this module import-light (no pandas/fastapi/pptx): every `sed` invocation imports it.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import date
from functools import wraps
from pathlib import Path
from typing import Annotated, Any, TypeVar

import typer

from sed import db
from sed.errors import EXIT_PRECONDITION, PreconditionFailed, SedError, ValidationFailed
from sed.output import emit_error
from sed.paths import Paths, get_paths

ProfileOpt = Annotated[str | None, typer.Option("--profile", "-p", help="synthetic | real | eval-<seed>")]
DataDirOpt = Annotated[Path | None, typer.Option("--data-dir", help="Override DATA_DIR (advanced)")]
JsonOpt = Annotated[bool, typer.Option("--json", help="Machine-readable JSON output")]

F = TypeVar("F", bound=Callable[..., Any])


def handle_errors(func: F) -> F:
    """Map SedError subclasses to their exit codes; with --json every outcome prints one JSON object."""

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        as_json = bool(kwargs.get("as_json"))
        try:
            return func(*args, **kwargs)
        except typer.Exit:
            raise
        except SedError as exc:
            emit_error(exc.to_dict(), as_json)
            raise typer.Exit(exc.exit_code) from exc
        except (sqlite3.DatabaseError, OSError) as exc:
            emit_error({"kind": "precondition", "message": f"{type(exc).__name__}: {exc}"}, as_json)
            raise typer.Exit(EXIT_PRECONDITION) from exc
        except Exception as exc:
            emit_error({"kind": "internal", "message": f"{type(exc).__name__}: {exc}"}, as_json)
            raise typer.Exit(1) from exc

    return wrapper  # type: ignore[return-value]


def paths_for(profile: str | None, data_dir: Path | None) -> Paths:
    return get_paths(profile, data_dir)


def open_db(paths: Paths) -> sqlite3.Connection:
    if not paths.db.exists():
        raise PreconditionFailed(f"No database at {paths.db}; run `sed init --profile {paths.profile}` first.")
    return db.connect(paths.db)


def parse_date(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationFailed(f"Invalid date '{value}' (use YYYY-MM-DD)") from exc
