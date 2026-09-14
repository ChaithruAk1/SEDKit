"""`sed modules list | show | check`: inspect installed modules and their declared surfaces."""

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
