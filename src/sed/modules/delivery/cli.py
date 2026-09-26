"""`sed delivery ...`: the delivery portfolio in the terminal and exports of approved AI drafts."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Annotated

import typer

from sed.cli_common import DataDirOpt, JsonOpt, ProfileOpt, handle_errors, paths_for
from sed.output import console, emit

app = typer.Typer(no_args_is_help=True, help="Delivery management of new business applications")


@app.command("portfolio")
@handle_errors
def portfolio_cmd(
    as_of: Annotated[str | None, typer.Option("--as-of", help="YYYY-MM-DD (default: data as-of date)")] = None,
    profile: ProfileOpt = None,
    data_dir: DataDirOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Projects with computed health, worst milestone slip, open high RAID items and forecast finish."""
    from sed import db
    from sed.ingest.freshness import data_as_of
    from sed.modules.delivery.queries import portfolio as P
    from sed.settings import load_settings

    paths = paths_for(profile, data_dir)
    conn = db.connect(paths.db, readonly=True)
    try:
        day = date.fromisoformat(as_of) if as_of else (data_as_of(conn, load_settings(paths)) or date.today())
        rows = []
        for item in P.portfolio(conn, paths, day):
            project, prog = item["project"], item["progress"]
            open_tasks = [t for t in item["plan"]["tasks"] if t["is_milestone"] and not t["actual_finish"]]
            rows.append(
                {
                    "project_id": project["project_id"],
                    "name": project["name"],
                    "computed_rag": item["health"]["rag"],
                    "reported_rag": (project["rag_raw"] or "").lower() or None,
                    "reasons": item["health"]["reasons"],
                    "worst_slip_days": max((t["slip_days"] or 0 for t in open_tasks), default=0),
                    "open_high_raid": sum(
                        1 for i in item["raid"] if i["open"] and i["severity"] in ("high", "critical")
                    ),
                    "forecast_finish": prog.forecast_finish.isoformat() if prog.forecast_finish else None,
                    "target_date": project["target_date"],
                }
            )
    finally:
        conn.close()

    def human(p: dict) -> None:
        for r in p["projects"]:
            console().print(
                f"{r['project_id']} {r['computed_rag']:<6} (reported {r['reported_rag']}) {r['name']}: "
                + ("; ".join(r["reasons"]) or "on track"),
                markup=False,
            )

    emit({"as_of": day.isoformat(), "projects": rows}, as_json, human)


@app.command("export")
@handle_errors
def export_cmd(
    what: Annotated[str, typer.Argument(help="stories | adr | test-plan | release-notes")],
    project: Annotated[str, typer.Option("--project", help="Delivery project id, e.g. PRJ-101")],
    period: Annotated[str | None, typer.Option(help="Release notes of one period only, e.g. 2026-08")] = None,
    profile: ProfileOpt = None,
    data_dir: DataDirOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Write approved drafts as files (Jira CSV, Markdown) under DATA_DIR/out/delivery/<project>/. Nothing is sent
    to Jira or Confluence."""
    from sed.audit import actions
    from sed.auth.actor import command_line_actor
    from sed.modules.delivery.ai.export import export

    paths = paths_for(profile, data_dir)
    summary = f"Export the approved {what} of {project}" + (f" for {period}" if period else "")
    detail = {"what": what, "project": project, "period": period}
    with actions.start_export(paths, command_line_actor(), summary, "delivery_project", project, detail) as attempt:
        result = export(paths, what, project, period=period)
        files = [Path(str(f)).name for f in result.get("files") or []]
        attempt.done(f"{summary}: {len(files)} files written.", detail={"files": files, "items": result.get("items")})

    def human(p: dict) -> None:
        console().print(
            f"{p['export']} for {p['project_id']}: {p['drafts']} approved drafts, {p['items']} items", markup=False
        )
        for path in p["files"]:
            console().print(f"  {path}", markup=False)

    emit(result, as_json, human)
