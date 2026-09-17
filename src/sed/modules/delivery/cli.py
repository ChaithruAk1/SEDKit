"""`sed delivery ...`: the delivery portfolio in the terminal."""

from __future__ import annotations

from datetime import date
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
