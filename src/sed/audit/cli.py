"""`sed audit ...`: check the audit trail (docs/audit.md). Read-only: nothing here changes or removes an entry."""

from __future__ import annotations

from typing import Any

import typer

from sed.cli_common import DataDirOpt, JsonOpt, ProfileOpt, handle_errors, paths_for
from sed.errors import EXIT_PRECONDITION
from sed.output import console, emit

audit_app = typer.Typer(no_args_is_help=True, help="The audit trail: who did what and when (read-only)")


@audit_app.command("verify")
@handle_errors
def verify(
    profile: ProfileOpt = None,
    data_dir: DataDirOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Walk the chain of entries: exit 0 when it is intact, 4 when an entry is missing or was changed outside SED."""
    from sed.audit.record import audit_path
    from sed.audit.store import verify as verify_chain

    paths = paths_for(profile, data_dir)
    result: dict[str, Any] = {"profile": paths.profile, **verify_chain(audit_path(paths))}

    def human(p: dict[str, Any]) -> None:
        if p["intact"]:
            since = f" since {p['first_at']}" if p["first_at"] else ""
            count = f"{p['entries']} {'entry' if p['entries'] == 1 else 'entries'}"
            console().print(f"The audit trail is intact: {count}{since}.", markup=False)
        else:
            console().print(
                f"The audit trail was changed outside SED: entry {p['first_break']} is missing or altered.",
                markup=False,
            )

    emit(result, as_json, human, ok=bool(result["intact"]))
    if not result["intact"]:
        raise typer.Exit(EXIT_PRECONDITION)
