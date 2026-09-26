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
    only: Annotated[
        str | None, typer.Option(help="Only this subset of items, e.g. a triage extension key such as sap")
    ] = None,
    report: Annotated[str | None, typer.Option(help="Report key for report skills, e.g. monthly")] = None,
    vendor: Annotated[str | None, typer.Option(help="Vendor id for vendor reports")] = None,
    subject: Annotated[
        str | None, typer.Option(help="What the run is about for subject skills, e.g. a project id such as PRJ-101")
    ] = None,
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
            only=only,
            report=report,
            vendor=vendor,
            subject=subject,
            resume=resume,
            invoked_via=invoked_via,
            model_arg=model,
            claude_version=claude_version,
            dry_run=dry_run,
        )
    except ValidationError as exc:
        details = [{"loc": ".".join(str(p) for p in e["loc"]), "msg": e["msg"]} for e in exc.errors()]
        raise ValidationFailed("Invalid start-run options", details) from exc
    paths = paths_for(profile, data_dir)
    if dry_run:
        plan = start_run(paths, skill, params)
    else:
        # Ticket text prepared for Claude leaves the laptop: on the audit trail before any packet is written.
        from sed.audit import actions
        from sed.auth.actor import command_line_actor

        with actions.start_ai_run(paths, command_line_actor(), skill, params.model_dump()) as attempt:
            plan = start_run(paths, skill, params)
            actions.ai_run_done(attempt, plan.model_dump())

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


@ai_app.command("packet")
@handle_errors
def ai_packet(
    run_id: Annotated[str, typer.Argument(help="Run id")],
    batch: Annotated[str | None, typer.Option(help="Only this batch, e.g. batch_0001")] = None,
    text: Annotated[bool, typer.Option("--text", help="Include the file contents")] = False,
    profile: ProfileOpt = None,
    data_dir: DataDirOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Audit exactly what text a run hands to the agents: run files, sizes and manifest checks (--text: contents)."""
    from sed.ai.audit import packet_audit

    result = packet_audit(paths_for(profile, data_dir), run_id, batch, text=text)

    def human(p: dict) -> None:
        for f in p["context"] + [f for b in p["batches"] for f in b["files"]]:
            state = "ok" if f.get("sha256_ok") is not False and f["exists"] else "CHANGED OR MISSING"
            console().print(f"{f['file']:<40} {f.get('chars', 0):>8} chars  {state}", markup=False)
            if text and f.get("text"):
                console().print(f["text"], markup=False)
        console().print(f"total {p['total_chars']} chars; {p['note']}", markup=False)

    emit(result, as_json, human)


@ai_app.command("review-rates")
@handle_errors
def ai_review_rates(
    skill: Annotated[str | None, typer.Option(help="Only this skill")] = None,
    profile: ProfileOpt = None,
    data_dir: DataDirOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """How reviewers decided per skill version (skill hash) and month: approved, edited, rejected, sample accuracy."""
    from sed import db
    from sed.ai.rates import review_rates

    paths = paths_for(profile, data_dir)
    conn = db.connect(paths.db, readonly=True)
    try:
        result = review_rates(conn, skill=skill)
    finally:
        conn.close()

    def human(p: dict) -> None:
        for r in p["rows"]:
            console().print(
                f"{r['month']} {r['skill']:<20} {r['skill_hash'][:12]} runs={r['runs']} "
                f"drafted={r['findings_drafted']} "
                f"approved={r['findings_approved']} edited={r['findings_edited']} rejected={r['findings_rejected']} "
                f"accuracy={r['sample_accuracy']}",
                markup=False,
            )

    emit(result, as_json, human)


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


@review_app.command("list")
@handle_errors
def review_list(
    kind: Annotated[str | None, typer.Option(help="Only this finding kind, e.g. issue_cluster")] = None,
    no_rule: Annotated[bool, typer.Option("--no-rule", help="Leave out active rule findings")] = False,
    limit: Annotated[int, typer.Option(min=1, max=1000)] = 200,
    profile: ProfileOpt = None,
    data_dir: DataDirOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Findings waiting for a person: AI drafts, stale inputs and wording updates, then active rule findings."""
    from sed.ai.findings import queue
    from sed.ai.runs import open_db

    conn = open_db(paths_for(profile, data_dir), readonly=True)
    try:
        items = queue(conn, kind=kind, include_rule=not no_rule, limit=limit)
    finally:
        conn.close()

    def human(p: dict) -> None:
        for f in p["items"]:
            console().print(
                f"{f['finding_id']}  {f['status']:<14} {f['severity'] or '-':<8} {f['title']}", markup=False
            )

    emit({"items": items, "count": len(items)}, as_json, human)


def _findings_action(
    paths_args: tuple, ids: list[str], action: str, *, note=None, body=None, until=None, as_json=False
) -> None:
    from sed.ai.review import review_findings
    from sed.bootstrap import reviewer_name

    result = review_findings(paths_for(*paths_args), ids, action, reviewer_name(), note=note, body_md=body, until=until)
    emit(result, as_json)


@review_app.command("approve")
@handle_errors
def review_approve(
    finding_ids: Annotated[list[str], typer.Argument(help="Finding ids (draft or stale_input)")],
    note: Annotated[str | None, typer.Option(help="Review note")] = None,
    profile: ProfileOpt = None,
    data_dir: DataDirOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Approve AI findings; earlier approved findings with the same stable key are superseded."""
    _findings_action((profile, data_dir), finding_ids, "approve", note=note, as_json=as_json)


@review_app.command("reject")
@handle_errors
def review_reject(
    finding_ids: Annotated[list[str], typer.Argument(help="Finding ids")],
    note: Annotated[str, typer.Option(help="Why the finding is rejected")],
    profile: ProfileOpt = None,
    data_dir: DataDirOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Reject AI findings (they never reach reports)."""
    _findings_action((profile, data_dir), finding_ids, "reject", note=note, as_json=as_json)


@review_app.command("edit")
@handle_errors
def review_edit(
    finding_id: Annotated[str, typer.Argument(help="Finding id")],
    file: Annotated[Path, typer.Option("--file", help="Markdown file with the corrected body")],
    note: Annotated[str | None, typer.Option(help="Review note")] = None,
    profile: ProfileOpt = None,
    data_dir: DataDirOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Replace an AI finding's body with your text and approve it (the AI text is kept as original_body_md)."""
    try:
        body = Path(str(file).replace("\\", "/")).read_bytes().decode("utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValidationFailed(f"Cannot read {file}: {exc}") from exc
    _findings_action((profile, data_dir), [finding_id], "edit", note=note, body=body, as_json=as_json)


@review_app.command("approve-update")
@handle_errors
def review_approve_update(
    finding_ids: Annotated[list[str], typer.Argument(help="Finding ids in status update_pending")],
    note: Annotated[str | None, typer.Option(help="Review note")] = None,
    profile: ProfileOpt = None,
    data_dir: DataDirOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Publish the new wording of carried-forward findings (update_pending)."""
    _findings_action((profile, data_dir), finding_ids, "approve_update", note=note, as_json=as_json)


@review_app.command("acknowledge")
@handle_errors
def review_acknowledge(
    finding_ids: Annotated[list[str], typer.Argument(help="Rule finding ids")],
    note: Annotated[str, typer.Option(help="Why no action is needed")],
    profile: ProfileOpt = None,
    data_dir: DataDirOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Acknowledge rule findings: hidden until their evidence changes materially."""
    _findings_action((profile, data_dir), finding_ids, "acknowledge", note=note, as_json=as_json)


@review_app.command("suppress")
@handle_errors
def review_suppress(
    finding_ids: Annotated[list[str], typer.Argument(help="Rule finding ids")],
    until: Annotated[str, typer.Option(help="Hide until this date (YYYY-MM-DD)")],
    note: Annotated[str, typer.Option(help="Why the finding is suppressed")],
    profile: ProfileOpt = None,
    data_dir: DataDirOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Suppress rule findings until a date (they return earlier when their evidence changes materially)."""
    from datetime import date

    try:
        day = date.fromisoformat(until)
    except ValueError as exc:
        raise ValidationFailed(f"Invalid --until '{until}' (use YYYY-MM-DD)") from exc
    _findings_action((profile, data_dir), finding_ids, "suppress_until", note=note, until=day, as_json=as_json)


@review_app.command("correct")
@handle_errors
def review_correct(
    ticket_id: Annotated[str, typer.Argument(help="Ticket id, e.g. incident:INC0012345")],
    stage: Annotated[str, typer.Option(help="open | resolved")],
    category: Annotated[str, typer.Option(help="Correct category code")],
    subcategory: Annotated[str | None, typer.Option(help="Correct subcategory code")] = None,
    symptom_key: Annotated[str | None, typer.Option("--symptom-key", help="Symptom key")] = None,
    misfiled_as: Annotated[str | None, typer.Option("--misfiled-as", help="none | request | change | problem")] = None,
    skill: Annotated[str, typer.Option(help="Skill whose taxonomy validates the label")] = "sed-triage-batch",
    profile: ProfileOpt = None,
    data_dir: DataDirOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Record a human label for one ticket stage (auto-approved manual run; always wins over AI labels)."""
    from sed.ai.review import correct_label
    from sed.bootstrap import reviewer_name

    correction = {
        k: v
        for k, v in {
            "category": category,
            "subcategory": subcategory,
            "symptom_key": symptom_key,
            "misfiled_as": misfiled_as,
        }.items()
        if v is not None
    }
    emit(
        correct_label(paths_for(profile, data_dir), ticket_id, stage, correction, reviewer_name(), skill=skill), as_json
    )
