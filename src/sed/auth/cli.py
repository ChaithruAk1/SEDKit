"""`sed auth ...`: see and change who can sign in to the dashboard (docs/sign-in.md).

The settings are written to DATA_DIR\\config\\auth.yaml and take effect the next time `sed serve` starts. Changing them
asks for permission in Claude Code (.claude/settings.json), because the allowlist decides who can see the data.
"""

from __future__ import annotations

from typing import Annotated, Any

import typer

from sed.cli_common import DataDirOpt, JsonOpt, ProfileOpt, handle_errors, paths_for
from sed.errors import ValidationFailed
from sed.output import console, emit

auth_app = typer.Typer(no_args_is_help=True, help="Sign-in to the dashboard: providers and who is allowed in")

ProviderArg = Annotated[str, typer.Argument(help="microsoft | google | github")]


def _describe(paths: Any, *, full: bool) -> dict[str, Any]:
    from sed.auth.settings import LABELS, PROVIDERS, load_auth_settings, local_file, resolve_mode

    settings = load_auth_settings(paths)
    try:
        mode = resolve_mode(settings, paths)
    except ValidationFailed as exc:
        mode = f"invalid: {exc.message}"
    providers = []
    for key in PROVIDERS:
        config = getattr(settings.providers, key)
        row: dict[str, Any] = {"provider": key, "label": LABELS[key], "enabled": config.enabled}
        row["client_id_set"] = bool(config.client_id)
        if key == "microsoft":
            row["tenant_id_set"] = bool(config.tenant_id)
            row["redirect"] = f"http://{config.redirect_host}/auth/callback"
        providers.append(row)
    out: dict[str, Any] = {
        "profile": paths.profile,
        "mode": mode,
        "session_hours": settings.session_hours,
        "providers": providers,
        "allowed_emails": len(settings.allow.emails),
        "allowed_organisations": len(settings.allow.microsoft_tenants),
        "problems": settings.problems() if mode == "sign_in" else [],
        "settings_file": local_file(paths).as_posix(),
    }
    if full:
        out["allow"] = {"emails": settings.allow.emails, "microsoft_tenants": settings.allow.microsoft_tenants}
    return out


def _count(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _print(p: dict[str, Any]) -> None:
    say = console().print
    mode = {"sign_in": "sign-in required", "developer": "developer mode (nobody signs in)"}.get(p["mode"], p["mode"])
    say(f"Profile '{p['profile']}': {mode}; a sign-in lasts {p['session_hours']} hours.", markup=False)
    for row in p["providers"]:
        state = "on " if row["enabled"] else "off"
        extra = f"  redirect {row['redirect']}" if row["provider"] == "microsoft" and row["enabled"] else ""
        say(f"  {row['label']:<10} {state}{extra}", markup=False)
    people = _count(p["allowed_emails"], "address", "addresses")
    organisations = _count(p["allowed_organisations"], "organisation", "organisations")
    say(f"Allowed: {people}, {organisations}.", markup=False)
    for value in (p.get("allow") or {}).get("emails", []) + (p.get("allow") or {}).get("microsoft_tenants", []):
        say(f"  {value}", markup=False)
    for problem in p["problems"]:
        say(f"Nobody can sign in yet: {problem}.", markup=False)


@auth_app.command("show")
@handle_errors
def show(
    list_people: Annotated[
        bool, typer.Option("--list", help="Also list the allowed addresses and organisations")
    ] = False,
    profile: ProfileOpt = None,
    data_dir: DataDirOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """How sign-in is set up for this profile (never prints the allowlist unless --list)."""
    emit(_describe(paths_for(profile, data_dir), full=list_people), as_json, _print)


@auth_app.command("mode")
@handle_errors
def mode(
    value: Annotated[
        str, typer.Argument(help="auto | sign_in | developer (developer is refused for the real profile)")
    ],
    profile: ProfileOpt = None,
    data_dir: DataDirOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Whether the dashboard asks for sign-in: auto (synthetic no, real yes), sign_in (always) or developer (never)."""
    from sed.auth.settings import AuthSettings, resolve_mode, update_local

    choice = value.strip().lower().replace("-", "_")
    if choice not in ("auto", "sign_in", "developer"):
        raise ValidationFailed(f"Unknown mode '{value}' (use auto, sign_in or developer)")
    paths = paths_for(profile, data_dir)
    resolve_mode(AuthSettings(mode=choice), paths)  # refuses developer for the real profile before anything is written
    update_local(paths, lambda data: data.__setitem__("mode", choice))
    emit(_describe(paths, full=False), as_json, _print)


@auth_app.command("provider")
@handle_errors
def provider(
    name: ProviderArg,
    client_id: Annotated[str | None, typer.Option("--client-id", help="The client ID the provider gave SED")] = None,
    tenant_id: Annotated[
        str | None, typer.Option("--tenant-id", help="Microsoft only: the directory (tenant) ID")
    ] = None,
    redirect_host: Annotated[
        str | None, typer.Option("--redirect-host", help="Microsoft only: localhost (default) or 127.0.0.1")
    ] = None,
    off: Annotated[bool, typer.Option("--off", help="Switch this provider off")] = False,
    profile: ProfileOpt = None,
    data_dir: DataDirOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Switch a provider on (with its client ID) or off. Takes effect when `sed serve` next starts."""
    from sed.auth.settings import PROVIDERS, update_local

    key = name.strip().lower()
    if key not in PROVIDERS:
        raise ValidationFailed(f"Unknown provider '{name}' (use {', '.join(PROVIDERS)})")
    if key != "microsoft" and (tenant_id or redirect_host):
        raise ValidationFailed("--tenant-id and --redirect-host apply to microsoft only")
    if off and (client_id or tenant_id or redirect_host):
        raise ValidationFailed("--off cannot be combined with other settings")

    def change(data: dict[str, Any]) -> None:
        section = data.setdefault("providers", {}).setdefault(key, {})
        if off:
            section["enabled"] = False
            return
        section["enabled"] = True
        if client_id is not None:
            section["client_id"] = client_id.strip()
        if tenant_id is not None:
            section["tenant_id"] = tenant_id.strip().lower()
        if redirect_host is not None:
            section["redirect_host"] = redirect_host.strip().lower()

    paths = paths_for(profile, data_dir)
    update_local(paths, change)
    emit(_describe(paths, full=False), as_json, _print)


def _allow_list_change(value: str, *, add: bool) -> tuple[str, Any]:
    from sed.auth.settings import EMAIL_RE, GUID_RE

    item = value.strip().lower()
    if GUID_RE.match(item):
        key = "microsoft_tenants"
    elif EMAIL_RE.match(item):
        key = "emails"
    else:
        raise ValidationFailed(f"'{value}' is neither an e-mail address nor a Microsoft directory (tenant) ID")

    def change(data: dict[str, Any]) -> None:
        allow = data.setdefault("allow", {})
        current = [str(v).strip().lower() for v in allow.get(key) or []]
        if add and item not in current:
            current.append(item)
        if not add:
            if item not in current:
                raise ValidationFailed(f"'{item}' is not on the list")
            current.remove(item)
        allow[key] = current

    return item, change


@auth_app.command("allow")
@handle_errors
def allow(
    value: Annotated[str, typer.Argument(help="An e-mail address, or a Microsoft directory (tenant) ID")],
    profile: ProfileOpt = None,
    data_dir: DataDirOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Let a person (by e-mail address) or a whole Microsoft organisation (by tenant ID) sign in."""
    from sed.auth.settings import update_local

    _, change = _allow_list_change(value, add=True)
    paths = paths_for(profile, data_dir)
    update_local(paths, change)
    emit(_describe(paths, full=False), as_json, _print)


@auth_app.command("disallow")
@handle_errors
def disallow(
    value: Annotated[str, typer.Argument(help="An e-mail address or tenant ID on the list")],
    profile: ProfileOpt = None,
    data_dir: DataDirOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Take a person or organisation off the list. A session already open lasts until it ends or SED restarts."""
    from sed.auth.settings import update_local

    _, change = _allow_list_change(value, add=False)
    paths = paths_for(profile, data_dir)
    update_local(paths, change)
    emit(_describe(paths, full=False), as_json, _print)
