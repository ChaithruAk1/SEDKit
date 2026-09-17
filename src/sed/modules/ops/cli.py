"""`sed ops ...`: ops module commands that are not legacy top-level commands."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

import typer

from sed.cli_common import DataDirOpt, JsonOpt, ProfileOpt, handle_errors, paths_for
from sed.errors import ValidationFailed
from sed.output import console, emit

app = typer.Typer(no_args_is_help=True, help="Application operations (recurring-issue candidates, AI evals)")


@app.command("candidates")
@handle_errors
def candidates(
    months: Annotated[int, typer.Option(min=1, max=36, help="Months of incidents to group")] = 12,
    as_of: Annotated[str | None, typer.Option("--as-of", help="YYYY-MM-DD (default: data as-of date)")] = None,
    app_id: Annotated[list[str] | None, typer.Option("--app", help="Only these app_ids (repeatable)")] = None,
    min_size: Annotated[int, typer.Option("--min-size", min=2, help="Smallest group")] = 5,
    threshold: Annotated[float, typer.Option(min=0.1, max=1.0, help="Cosine similarity threshold")] = 0.6,
    limit: Annotated[int, typer.Option(min=1, max=1000, help="Largest groups to print")] = 50,
    profile: ProfileOpt = None,
    data_dir: DataDirOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Group similar incidents per application (TF-IDF, no AI): size, periodicity, bursts, changes before onset."""
    from sed import db
    from sed.ingest.freshness import data_as_of
    from sed.modules.ops.candidates import as_dict, text_candidates
    from sed.settings import load_settings

    paths = paths_for(profile, data_dir)
    settings = load_settings(paths)
    conn = db.connect(paths.db, readonly=True)
    try:
        if as_of:
            try:
                day = date.fromisoformat(as_of)
            except ValueError as exc:
                raise ValidationFailed(f"Invalid --as-of '{as_of}'") from exc
        else:
            day = data_as_of(conn, settings) or date.today()
        groups = text_candidates(
            conn,
            day,
            settings.reporting_tz,
            months=months,
            min_size=min_size,
            threshold=threshold,
            app_ids=list(app_id or []) or None,
        )
    finally:
        conn.close()

    def human(p: dict[str, Any]) -> None:
        for g in p["groups"]:
            console().print(
                f"{g['tickets']:>5}  {g['periodicity']:<8} {g['app']}: {' / '.join(g['terms'][:3])}", markup=False
            )

    emit(
        {
            "as_of": day.isoformat(),
            "months": months,
            "total": len(groups),
            "groups": [as_dict(g) for g in groups[:limit]],
        },
        as_json,
        human,
    )


def _eval(kind: str, run_id: str, profile: str | None, data_dir: Any, as_json: bool) -> None:
    from sed.modules.ops import evals

    function = {"triage": evals.evaluate_triage, "recurring": evals.evaluate_recurring, "risks": evals.evaluate_risks}
    result = function[kind](paths_for(profile, data_dir), run_id)

    def human(p: dict[str, Any]) -> None:
        console().print(f"run {p['run_id']} ({p['skill']}): {'PASSED' if p['passed'] else 'FAILED'}", markup=False)
        for name, ok in p["checks"].items():
            console().print(f"  {'ok  ' if ok else 'FAIL'} {name}", markup=False)

    emit(result, as_json, human)


@app.command("eval-triage")
@handle_errors
def eval_triage(
    run_id: Annotated[str, typer.Argument(help="A sed-triage-batch or sed-triage-open run id")],
    profile: ProfileOpt = None,
    data_dir: DataDirOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Score triage labels against the synthetic ground truth (scores only; also runs/<run>/eval.json)."""
    _eval("triage", run_id, profile, data_dir, as_json)


@app.command("eval-recurring")
@handle_errors
def eval_recurring(
    run_id: Annotated[str, typer.Argument(help="A sed-find-recurring run id")],
    profile: ProfileOpt = None,
    data_dir: DataDirOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Score issue clusters against the planted P1, P5 and P7 patterns (scores only)."""
    _eval("recurring", run_id, profile, data_dir, as_json)


@app.command("eval-risks")
@handle_errors
def eval_risks(
    run_id: Annotated[str, typer.Argument(help="A sed-assess-risks run id")],
    profile: ProfileOpt = None,
    data_dir: DataDirOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Score risk findings against the planted P2 and P4 patterns and the noisy-vendor control (scores only)."""
    _eval("risks", run_id, profile, data_dir, as_json)
