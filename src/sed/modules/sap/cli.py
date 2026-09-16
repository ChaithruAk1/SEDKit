"""`sed sap ...`: SAP module commands."""

from __future__ import annotations

from typing import Annotated, Any

import typer

from sed.cli_common import DataDirOpt, JsonOpt, ProfileOpt, handle_errors, paths_for
from sed.output import console, emit

app = typer.Typer(no_args_is_help=True, help="SAP application support (evals)")


@app.command("eval-triage")
@handle_errors
def eval_triage(
    run_id: Annotated[str, typer.Argument(help="A sed-triage-batch run id")],
    profile: ProfileOpt = None,
    data_dir: DataDirOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Score a triage run's SAP subcategories against the synthetic ground truth (scores only)."""
    from sed.modules.sap.evals import evaluate_triage

    result = evaluate_triage(paths_for(profile, data_dir), run_id)

    def human(p: dict[str, Any]) -> None:
        def pct(rate: dict[str, Any]) -> str:
            if rate["accuracy"] is None:
                return "n/a"
            return f"{100 * rate['accuracy']:.1f}% ({100 * rate['ci_low']:.1f}–{100 * rate['ci_high']:.1f}%)"

        console().print(
            f"run {p['run_id']}: {p['scored']} SAP tickets scored of {p['labels']} labels; "
            f"category {pct(p['category'])}, subcategory {pct(p['subcategory'])}; "
            f"{'PASSED' if p['passed'] else 'FAILED'}",
            markup=False,
        )
        for code, row in p["by_subcategory"].items():
            console().print(f"  {code:<24} {row['correct']}/{row['n']}", markup=False)

    emit(result, as_json, human)
