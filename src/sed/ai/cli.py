"""`sed ai ...` and `sed review ...` commands (signatures frozen for M2).

Every command prints exactly one JSON line with --json. Exit codes: 0 ok, 2 validation (JSON error list),
3 busy, 4 precondition (unknown or wrong-state run, disabled module, missing approval). Heavy imports stay inside
command bodies.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from sed.cli_common import DataDirOpt, JsonOpt, ProfileOpt, handle_errors, paths_for
from sed.errors import ValidationFailed
from sed.output import console, emit

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
    from pydantic import ValidationError

    from sed.ai.contract import StartParams
    from sed.ai.runs import start_run

    try:
        params = StartParams(
            scope=scope,
            batch_size=batch_size,
            max_chars=max_chars,
            max_items=max_items,
            limit=limit,
            resume=resume,
            invoked_via=invoked_via,
            model_arg=model,
            claude_version=claude_version,
            dry_run=dry_run,
        )
    except ValidationError as exc:
        details = [{"loc": ".".join(str(p) for p in e["loc"]), "msg": e["msg"]} for e in exc.errors()]
        raise ValidationFailed("Invalid start-run options", details) from exc
    plan = start_run(paths_for(profile, data_dir), skill, params)

    def human(p: dict) -> None:
        console().print(f"run {p['run_id']} ({p['status']}): {p['plan']}", markup=False)
        for item in p["inputs"]:
            console().print(
                f"  {item['batch']}: {item['items']} items, {item['chars']} chars -> {item['out']}", markup=False
            )

    emit(plan.model_dump(), as_json, human)


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
    from sed.ai.ingest import ingest_file

    result = ingest_file(paths_for(profile, data_dir), run_id, file)
    emit(result, as_json, lambda p: console().print(f"{p['batch']}: {p['status']} ({p['items']} items)", markup=False))


@ai_app.command("finish-run")
@handle_errors
def ai_finish_run(
    run_id: Annotated[str, typer.Argument(help="Run id")],
    profile: ProfileOpt = None,
    data_dir: DataDirOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Mark missing batches failed, release claims, draw the review sample and print the review summary."""
    from sed.ai.runs import finish_run

    summary = finish_run(paths_for(profile, data_dir), run_id)
    emit(
        summary.model_dump(),
        as_json,
        lambda p: console().print(f"run {p['run_id']}: {p['status']} {p['counts']}", markup=False),
    )


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
    from sed.ai.runs import list_runs

    rows = list_runs(paths_for(profile, data_dir), skill=skill, status=status, limit=limit)

    def human(p: dict) -> None:
        for r in p["runs"]:
            console().print(
                f"{r['run_id']}  {r['status']:<10} {r['skill']}  accuracy={r['sample_accuracy']}", markup=False
            )

    emit({"runs": rows}, as_json, human)


@schemas_app.command("export")
@handle_errors
def ai_schemas_export(
    check: Annotated[bool, typer.Option("--check", help="Write nothing; exit 2 when files are out of date")] = False,
    as_json: JsonOpt = False,
) -> None:
    """Write each skill's output_schema.json and the schema blocks in workflow scripts."""
    from sed.ai.codegen import export
    from sed.paths import repo_root

    drifted, written = export(repo_root(), check=check)
    if check and drifted:
        raise ValidationFailed("Generated AI schemas are out of date; run `sed ai schemas export`", drifted)
    emit({"check": check, "drifted": drifted, "written": written}, as_json)


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
    from sed.ai.review import sample

    result = sample(paths_for(profile, data_dir), run_id, template=template)

    def human(p: dict) -> None:
        for card in p["random"] + p["lowest_confidence"]:
            label, ticket = card["label"], card["ticket"]
            console().print(
                f"[{card['sample_kind']}] {card['item_id']}|{card['stage']}  {label.get('am_category')}/"
                f"{label.get('am_subcategory')}  conf={label.get('confidence')}  {ticket.get('short_description')}",
                markup=False,
            )
        if p.get("template"):
            console().print(f"Verdicts template: {p['template']}", markup=False)

    emit(result, as_json, human)


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
    from sed.ai.review import record_verdicts

    emit(record_verdicts(paths_for(profile, data_dir), run_id, file), as_json)


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
    from sed.ai.review import approve_run
    from sed.bootstrap import reviewer_name

    emit(approve_run(paths_for(profile, data_dir), run_id, reviewer_name(), note), as_json)


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
    from sed.ai.review import reject_run
    from sed.bootstrap import reviewer_name

    emit(reject_run(paths_for(profile, data_dir), run_id, reviewer_name(), note), as_json)
