"""`sed ai ...` and `sed review ...` commands. Signatures are final for M2; bodies are implemented by ws1-ai.

Every command prints exactly one JSON line with --json. Heavy imports stay inside command bodies.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from sed.cli_common import DataDirOpt, JsonOpt, ProfileOpt, handle_errors
from sed.errors import NotImplementedByWorkstream

WS = "ws1-ai"

ai_app = typer.Typer(no_args_is_help=True, help="AI runs through Claude Code (start-run, ingest, finish-run)")
review_app = typer.Typer(no_args_is_help=True, help="Human review of AI runs (samples, verdicts, approval)")
schemas_app = typer.Typer(no_args_is_help=True, help="Output schemas generated from the Pydantic models")
ai_app.add_typer(schemas_app, name="schemas")


@ai_app.command("start-run")
@handle_errors
def ai_start_run(
    skill: Annotated[str, typer.Argument(help="Skill name, e.g. sed-triage-batch")],
    scope: Annotated[str, typer.Option(help="new | since:YYYY-MM-DD | period:<label>")] = "new",
    batch_size: Annotated[int | None, typer.Option("--batch-size", min=1, max=500)] = None,
    max_chars: Annotated[int | None, typer.Option("--max-chars", min=1000, help="Packet size limit")] = None,
    max_items: Annotated[int | None, typer.Option("--max-items", min=1, help="Refuse larger runs")] = None,
    limit: Annotated[int | None, typer.Option(min=1, help="Take at most this many items")] = None,
    resume: Annotated[str | None, typer.Option(help="Resume a run: only batches not yet ingested")] = None,
    invoked_via: Annotated[
        str, typer.Option("--invoked-via", help="interactive | workflow | headless")
    ] = "interactive",
    model: Annotated[str | None, typer.Option(help="Model argument passed to the agents (recorded)")] = None,
    claude_version: Annotated[str | None, typer.Option("--claude-version", help="Output of `claude --version`")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Plan only: no rows, claims or files")] = False,
    profile: ProfileOpt = None,
    data_dir: DataDirOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Select and claim work items, write packets and print the run plan."""
    raise NotImplementedByWorkstream(WS)


@ai_app.command("ingest")
@handle_errors
def ai_ingest(
    run_id: Annotated[str, typer.Argument(help="Run id from start-run")],
    file: Annotated[Path, typer.Argument(help="Agent output file under runs/<run>/out/")],
    profile: ProfileOpt = None,
    data_dir: DataDirOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Validate an agent output file and store it as draft (exit 2 with an error list on any problem)."""
    raise NotImplementedByWorkstream(WS)


@ai_app.command("finish-run")
@handle_errors
def ai_finish_run(
    run_id: Annotated[str, typer.Argument(help="Run id")],
    profile: ProfileOpt = None,
    data_dir: DataDirOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Mark missing batches failed, release claims, draw the review sample and print the review summary."""
    raise NotImplementedByWorkstream(WS)


@ai_app.command("runs")
@handle_errors
def ai_runs(
    skill: Annotated[str | None, typer.Option(help="Filter by skill")] = None,
    status: Annotated[str | None, typer.Option(help="Filter by status")] = None,
    limit: Annotated[int, typer.Option(min=1, max=500)] = 50,
    profile: ProfileOpt = None,
    data_dir: DataDirOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """List AI runs with status, counts and sample accuracy."""
    raise NotImplementedByWorkstream(WS)


@schemas_app.command("export")
@handle_errors
def ai_schemas_export(
    check: Annotated[bool, typer.Option("--check", help="Write nothing; exit 2 when files are out of date")] = False,
    as_json: JsonOpt = False,
) -> None:
    """Write each skill's output_schema.json and the schema blocks in workflow scripts."""
    raise NotImplementedByWorkstream(WS)


@review_app.command("sample")
@handle_errors
def review_sample(
    run_id: Annotated[str, typer.Argument(help="Run id")],
    template: Annotated[Path | None, typer.Option(help="Also write a verdicts template JSON to this file")] = None,
    profile: ProfileOpt = None,
    data_dir: DataDirOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Show the random stratified sample and lowest-confidence items of a finished run."""
    raise NotImplementedByWorkstream(WS)


@review_app.command("verdicts")
@handle_errors
def review_verdicts(
    run_id: Annotated[str, typer.Argument(help="Run id")],
    file: Annotated[Path, typer.Option("--file", help="Verdicts JSON (from sample --template)")],
    profile: ProfileOpt = None,
    data_dir: DataDirOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Record verdicts for sampled items: "correct", {"verdict": "incorrect", ...} or null (skipped)."""
    raise NotImplementedByWorkstream(WS)


@review_app.command("approve-run")
@handle_errors
def review_approve_run(
    run_id: Annotated[str, typer.Argument(help="Run id")],
    note: Annotated[str | None, typer.Option(help="Review note")] = None,
    profile: ProfileOpt = None,
    data_dir: DataDirOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Approve a run once every random-sample verdict is present (stores sample accuracy with a Wilson interval)."""
    raise NotImplementedByWorkstream(WS)


@review_app.command("reject-run")
@handle_errors
def review_reject_run(
    run_id: Annotated[str, typer.Argument(help="Run id")],
    note: Annotated[str, typer.Option(help="Why the run is rejected")],
    profile: ProfileOpt = None,
    data_dir: DataDirOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Reject a run (its labels never reach reports)."""
    raise NotImplementedByWorkstream(WS)
