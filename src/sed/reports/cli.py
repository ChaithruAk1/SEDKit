"""`sed report ...`: frozen snapshots, report artifacts and PowerPoint template tools.

Heavy imports stay inside command bodies (the CLI must start fast).
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from sed import db
from sed.cli_common import DataDirOpt, JsonOpt, ProfileOpt, handle_errors, paths_for
from sed.errors import NotImplementedByWorkstream
from sed.output import console, emit

report_app = typer.Typer(no_args_is_help=True, help="Report snapshots and artifacts")


@report_app.command("build")
@handle_errors
def report_build(
    report: Annotated[str, typer.Argument(help="Report key (see `sed report list`)")],
    period: Annotated[str, typer.Option(help="Period label, e.g. 2026-W35, 2026-08, 2026-Q3")],
    fmt: Annotated[
        str | None, typer.Option("--format", help="Comma-separated xlsx,md,pptx (default: every format of the report)")
    ] = None,
    ai: Annotated[str, typer.Option("--ai", help="approved | none | draft")] = "approved",
    vendor: Annotated[str | None, typer.Option(help="Vendor id (vendor report)")] = None,
    template_map: Annotated[
        str | None, typer.Option("--template-map", help="PPTX template map name or path (default: settings)")
    ] = None,
    profile: ProfileOpt = None,
    data_dir: DataDirOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Build a report from a fresh frozen snapshot."""
    from sed.reports.build import build_report

    paths = paths_for(profile, data_dir)
    formats = [x.strip().lower() for x in fmt.split(",") if x.strip()] if fmt else None
    result = build_report(paths, report, period, formats, ai, vendor, template_map=template_map)
    emit(result, as_json, lambda p: [console().print(f"{a['format']}: {a['path']}") for a in p["artifacts"]])


@report_app.command("snapshot")
@handle_errors
def report_snapshot(
    report: Annotated[str, typer.Argument(help="Report key (see `sed report list`)")],
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
            "as_of": snap.as_of,
            "data_as_of": snap.data_as_of,
            "facts": snap.facts,
            "tables": {k: len(v["rows"]) for k, v in snap.tables.items()},
            "ai_runs": [r["run_id"] for r in snap.ai_runs],
        },
        as_json,
    )


@report_app.command("list")
@handle_errors
def report_list(profile: ProfileOpt = None, data_dir: DataDirOpt = None, as_json: JsonOpt = False) -> None:
    """List the reports of every enabled module."""
    from sed.modules import reports

    rows = [
        {
            "key": r.key,
            "module": m.key,
            "title": r.title,
            "period_kinds": list(r.period_kinds),
            "needs_vendor": r.needs_vendor,
            "formats": list(r.formats),
        }
        for m, r in reports(paths_for(profile, data_dir))
    ]
    emit({"reports": rows}, as_json)


@report_app.command("template-inspect")
@handle_errors
def report_template_inspect(
    file: Annotated[Path, typer.Argument(help="PowerPoint template (.pptx)")],
    as_json: JsonOpt = False,
) -> None:
    """Print a template's layouts and placeholders (idx, type, size) for writing a template map."""
    raise NotImplementedByWorkstream("ws2-pptx")


@report_app.command("template-proof")
@handle_errors
def report_template_proof(
    template_map: Annotated[str | None, typer.Option("--map", help="Template map name or path")] = None,
    out: Annotated[
        Path | None, typer.Option("--out", help="Output folder (default: DATA_DIR/out/template-proof)")
    ] = None,
    profile: ProfileOpt = None,
    data_dir: DataDirOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Render every slide kind with dummy content for visual sign-off of a template map."""
    raise NotImplementedByWorkstream("ws2-pptx")
