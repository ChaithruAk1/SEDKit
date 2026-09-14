"""`sed report ...`: frozen snapshots and report artifacts. Heavy imports stay inside command bodies."""

from __future__ import annotations

from typing import Annotated

import typer

from sed import db
from sed.cli_common import DataDirOpt, JsonOpt, ProfileOpt, handle_errors, paths_for
from sed.output import console, emit

report_app = typer.Typer(no_args_is_help=True, help="Report snapshots and artifacts")


@report_app.command("build")
@handle_errors
def report_build(
    report: Annotated[str, typer.Argument(help="weekly (monthly | quarterly | vendor arrive in M2)")],
    period: Annotated[str, typer.Option(help="Period label, e.g. 2026-W35")],
    fmt: Annotated[str, typer.Option("--format", help="Comma-separated: xlsx,md")] = "xlsx,md",
    ai: Annotated[str, typer.Option("--ai", help="approved | none | draft")] = "approved",
    vendor: Annotated[str | None, typer.Option(help="Vendor id (vendor report)")] = None,
    profile: ProfileOpt = None,
    data_dir: DataDirOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Build a report from a fresh frozen snapshot."""
    from sed.reports.build import build_report

    paths = paths_for(profile, data_dir)
    formats = [x.strip().lower() for x in fmt.split(",") if x.strip()]
    result = build_report(paths, report, period, formats, ai, vendor)
    emit(result, as_json, lambda p: [console().print(f"{a['format']}: {a['path']}") for a in p["artifacts"]])


@report_app.command("snapshot")
@handle_errors
def report_snapshot(
    report: Annotated[str, typer.Argument(help="weekly")],
    period: Annotated[str, typer.Option(help="Period label, e.g. 2026-W35")],
    vendor: Annotated[str | None, typer.Option(help="Vendor id (vendor report)")] = None,
    profile: ProfileOpt = None,
    data_dir: DataDirOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Create (or reuse) the frozen facts snapshot for a report period."""
    from sed.reports.snapshot import create_snapshot

    paths = paths_for(profile, data_dir)
    conn = db.connect(paths.db)
    try:
        snap = create_snapshot(conn, paths, report, period, vendor)
    finally:
        conn.close()
    emit(
        {
            "snapshot_id": snap.snapshot_id,
            "sha256": snap.sha256,
            "facts": snap.facts,
            "tables": {k: len(v["rows"]) for k, v in snap.tables.items()},
        },
        as_json,
    )
