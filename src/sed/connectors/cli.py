"""`sed pull ...` (connectors), `sed sources` (every source by API or by file) and `sed schedule ...` (weekly automation
files)."""

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
        run_import: Annotated[
            bool, typer.Option("--import", help="Import the files written (same as `sed import` on them)")
        ] = False,
        profile: ProfileOpt = None,
        data_dir: DataDirOpt = None,
        as_json: JsonOpt = False,
    ) -> None:
        from sed.connectors.pull import pull
        from sed.errors import ValidationFailed
        from sed.sources import pull_and_import

        paths = paths_for(profile, data_dir)
        if run_import and dry_run:
            raise ValidationFailed("--import cannot be combined with --dry-run")
        if dry_run:
            result = pull(paths, connector, source=source, since=since, full=full, dry_run=True)
        else:
            from sed.audit import actions
            from sed.auth.actor import command_line_actor

            who = command_line_actor()
            with actions.start_pull(paths, who, connector, source=source, full=full, then_import=run_import) as attempt:
                if run_import:
                    combined = pull_and_import(paths, connector, source=source, since=since, full=full)
                    result = {**combined["pull"], "import": combined["import"]}
                    actions.pull_done(attempt, combined["pull"], combined["import"])
                else:
                    result = pull(paths, connector, source=source, since=since, full=full)
                    actions.pull_done(attempt, result)

        def human(p: dict) -> None:
            for s in p["sources"]:
                capped = " (cap reached)" if s["capped"] else ""
                written = ", ".join(s.get("files") or []) or "nothing written"
                console().print(f"{p['connector']}/{s['source']}: {s['rows']} rows{capped} -> {written}", markup=False)
            summary = (p.get("import") or {}).get("summary")
            if summary:
                console().print(
                    f"imported {summary['imported']} files, {summary['errors']} errors, {summary['rows_read']} rows",
                    markup=False,
                )
            elif p["next"]:
                console().print(f"next: {p['next']}", markup=False)

        emit(result, as_json, human)

    command.__doc__ = f"Pull {connector} sources into the inbox (delta from the watermark)."


for _name in ("servicenow", "jira", "sharepoint", "confluence", "sap"):
    _pull_command(_name)


@pull_app.command("status")
@handle_errors
def pull_status(profile: ProfileOpt = None, data_dir: DataDirOpt = None, as_json: JsonOpt = False) -> None:
    """Configured connectors, where their secret is found (never the value) and their watermarks."""
    from sed.connectors.pull import status

    emit(status(paths_for(profile, data_dir)), as_json)


def sources_command(
    profile: ProfileOpt = None,
    data_dir: DataDirOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Every export of the enabled modules with the connector sources that can pull it and its last import, and each
    connector's readiness (enabled, credential found, profile). No network, no secret values."""
    from sed.sources import overview

    result = overview(paths_for(profile, data_dir))

    def human(p: dict) -> None:
        for c in p["connectors"]:
            state = "ready" if c["can_pull"] else c["reason"]
            console().print(f"{c['connector']:<11} {state}", markup=False)
        for f in p["files"]:
            by_api = ", ".join(f["connector_sources"]) or "file only"
            console().print(f"  {f['mapping']:<34} {by_api:<40} last: {f['last_imported_at'] or 'never'}", markup=False)

    emit(result, as_json, human)


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
