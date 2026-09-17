"""`sed pull ...` (connectors) and `sed schedule ...` (weekly automation files)."""

from __future__ import annotations

from typing import Annotated

import typer

from sed.cli_common import DataDirOpt, JsonOpt, ProfileOpt, handle_errors, paths_for
from sed.output import console, emit

pull_app = typer.Typer(no_args_is_help=True, help="Read-only API pulls into the inbox (then `sed import --inbox`)")
schedule_app = typer.Typer(no_args_is_help=True, help="Weekly automation files for Windows Task Scheduler")


def _pull_command(connector: str) -> None:
    @pull_app.command(connector)
    @handle_errors
    def command(
        source: Annotated[str | None, typer.Option(help="Only this source key")] = None,
        since: Annotated[
            str | None, typer.Option(help="Start from this ISO date or date-time (UTC unless offset)")
        ] = None,
        full: Annotated[bool, typer.Option("--full", help="Ignore the watermark and pull everything")] = False,
        dry_run: Annotated[bool, typer.Option("--dry-run", help="Read and count, write nothing")] = False,
        profile: ProfileOpt = None,
        data_dir: DataDirOpt = None,
        as_json: JsonOpt = False,
    ) -> None:
        from sed.connectors.pull import pull

        result = pull(paths_for(profile, data_dir), connector, source=source, since=since, full=full, dry_run=dry_run)

        def human(p: dict) -> None:
            for s in p["sources"]:
                capped = " (row cap reached)" if s["capped"] else ""
                console().print(
                    f"{p['connector']}/{s['source']}: {s['rows']} rows{capped} -> {s['file'] or 'nothing written'}",
                    markup=False,
                )
            if p["next"]:
                console().print(f"next: {p['next']}", markup=False)

        emit(result, as_json, human)

    command.__doc__ = f"Pull {connector} sources into the inbox (delta from the watermark)."


for _name in ("servicenow", "jira", "sharepoint", "confluence"):
    _pull_command(_name)


@pull_app.command("status")
@handle_errors
def pull_status(profile: ProfileOpt = None, data_dir: DataDirOpt = None, as_json: JsonOpt = False) -> None:
    """Configured connectors, where their secret is found (never the value) and their watermarks."""
    from sed.connectors.pull import status

    emit(status(paths_for(profile, data_dir)), as_json)


@schedule_app.command("write")
@handle_errors
def schedule_write(
    day: Annotated[str, typer.Option(help="MON..SUN")] = "MON",
    time: Annotated[str, typer.Option(help="HH:MM local time")] = "07:00",
    analyze: Annotated[
        bool, typer.Option("--analyze/--no-analyze", help="Run the sed-analyze workflow headless")
    ] = True,
    profile: ProfileOpt = None,
    data_dir: DataDirOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Write the weekly script and Task Scheduler definition into the data folder and print how to register them.
    Nothing is registered by SED: you run the printed schtasks command yourself."""
    from sed.connectors.schedule import write_schedule

    emit(write_schedule(paths_for(profile, data_dir), day=day, time=time, analyze=analyze), as_json)
