"""`sed modules list | show | check | new | gate`: inspect installed modules, scaffold a new one (app factory) and run
the module quality gate."""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated, Any

import typer

from sed.cli_common import DataDirOpt, JsonOpt, ProfileOpt, handle_errors, paths_for
from sed.errors import ValidationFailed
from sed.output import console, emit

modules_app = typer.Typer(no_args_is_help=True, help="Installed modules (ops, ...)")


def _summary(module: Any, enabled: bool) -> dict[str, Any]:
    return {
        "key": module.key,
        "title": module.title,
        "description": module.description,
        "version": module.version,
        "enabled": enabled,
        "reports": [r.key for r in module.reports],
        "skills": [s.name for s in module.skills],
        "cli": [c.name for c in module.cli] + list(module.legacy_cli),
        "api": f"/api/{module.key}" if module.api else None,
        "nav": [item.path for item in module.nav],
    }


@modules_app.command("list")
@handle_errors
def modules_list(profile: ProfileOpt = None, data_dir: DataDirOpt = None, as_json: JsonOpt = False) -> None:
    """List installed modules and whether each is enabled for the profile."""
    from sed import modules

    paths = paths_for(profile, data_dir)
    keys = modules.enabled_keys(paths)
    rows = [_summary(m, m.key in keys) for m in modules.installed()]

    def human(p: dict[str, Any]) -> None:
        for row in p["modules"]:
            state = "[green]enabled[/]" if row["enabled"] else "[yellow]disabled[/]"
            console().print(f"{row['key']:<10} {state:<18} {row['title']}  reports={','.join(row['reports'])}")

    emit({"modules": rows}, as_json, human)


@modules_app.command("show")
@handle_errors
def modules_show(
    key: Annotated[str, typer.Argument(help="Module key, e.g. ops")],
    profile: ProfileOpt = None,
    data_dir: DataDirOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Show a module's full manifest."""
    from sed import modules

    paths = paths_for(profile, data_dir)
    module = modules.get(key)
    emit({"module": asdict(module), "enabled": key in modules.enabled_keys(paths)}, as_json)


@modules_app.command("check")
@handle_errors
def modules_check(profile: ProfileOpt = None, data_dir: DataDirOpt = None, as_json: JsonOpt = False) -> None:
    """Validate manifests, import every declared reference and look for collisions (exit 2 on problems)."""
    from sed import modules

    paths = paths_for(profile, data_dir)
    problems = modules.validate()
    for m in modules.installed():
        for ref in modules.declared_refs(m):
            try:
                modules.load_ref(ref)
            except ValidationFailed as exc:
                problems.append(f"{m.key}: {exc.message}")
    try:
        modules.mapping_index(paths)
        modules.ingest_targets(paths)
        warnings = modules.mapping_glob_overlaps(paths)
    except ValidationFailed as exc:
        problems.append(exc.message)
        warnings = []
    if problems:
        raise ValidationFailed("Module declarations are invalid", problems)
    emit({"modules": [m.key for m in modules.installed()], "problems": [], "warnings": warnings}, as_json)


@modules_app.command("new")
@handle_errors
def modules_new(
    key: Annotated[str, typer.Argument(help="New module key: lowercase letters and digits, e.g. crm")],
    title: Annotated[str | None, typer.Option(help="Module title (default: the key)")] = None,
    description: Annotated[str | None, typer.Option(help="One-line description")] = None,
    depends_on: Annotated[
        list[str] | None, typer.Option("--depends-on", help="Module it builds on (repeatable)")
    ] = None,
    order: Annotated[int, typer.Option(min=1, max=799, help="Navigation order")] = 90,
    root: Annotated[str | None, typer.Option(help="Repository root (default: this checkout)")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="List the files without writing")] = False,
    profile: ProfileOpt = None,
    data_dir: DataDirOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Scaffold a module that passes `sed modules check` and the gate: manifest, API, CLI, page, fixture, test, docs."""
    from pathlib import Path

    from sed.modules import scaffold
    from sed.paths import repo_root

    base = Path(root) if root else repo_root()
    files = scaffold.plan(base, key, title=title, description=description, depends_on=depends_on, order=order)
    written = [] if dry_run else scaffold.write(base, files)
    payload = {
        "module": key,
        "dry_run": dry_run,
        "files": sorted(files),
        "written": written,
        "next": [
            "uv run python scripts/codegen.py",
            f"uv run sed modules gate {key} --json",
            f"uv run pytest tests/modules/{key} -q",
        ],
    }

    def human(p: dict[str, Any]) -> None:
        verb = "would write" if p["dry_run"] else "wrote"
        console().print(f"{p['module']}: {verb} {len(p['files'])} files", markup=False)
        for rel in p["files"]:
            console().print(f"  {rel}", markup=False)
        console().print("next: " + "; ".join(p["next"]), markup=False)

    emit(payload, as_json, human)


@modules_app.command("gate")
@handle_errors
def modules_gate(
    key: Annotated[str, typer.Argument(help="Module key, e.g. delivery")],
    run_tests: Annotated[bool, typer.Option("--run-tests", help="Also run pytest tests/modules/<key>")] = False,
    root: Annotated[str | None, typer.Option(help="Repository root (default: this checkout)")] = None,
    profile: ProfileOpt = None,
    data_dir: DataDirOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Quality gate of one module: manifest, boundaries, owned paths, config, skills, web fixtures, tests, docs."""
    from pathlib import Path

    from sed.modules import gate
    from sed.paths import repo_root

    report = gate.gate(Path(root) if root else repo_root(), key, run_tests=run_tests)
    if not report["passed"]:
        failed = [f"{c['name']}: {p}" for c in report["checks"] for p in c["problems"]]
        raise ValidationFailed(f"Module '{key}' does not pass the gate", failed)

    def human(p: dict[str, Any]) -> None:
        for check in p["checks"]:
            console().print(f"{check['name']:<12} ok", markup=False)

    emit(report, as_json, human)
