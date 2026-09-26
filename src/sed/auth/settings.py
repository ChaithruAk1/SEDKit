"""Sign-in settings: repo config/auth.yaml (every provider off, nobody allowed) overlaid by DATA_DIR\\config\\auth.yaml.

Client IDs are not secrets in these flows (PKCE and device code, no client secret), but the allowlist names real people,
so real values live only in DATA_DIR. `sed auth ...` writes the DATA_DIR file; `sed serve` reads it at launch.

The mode decides whether the dashboard asks anyone to sign in:
* `auto` (default): synthetic profiles run in developer mode, the real profile requires sign-in;
* `sign_in`: always require it (to try the real flow on synthetic data);
* `developer`: never ask. Refused for the real profile, where developer mode exists only for one launch
  (`sed serve --developer-mode`), so it can never become the default there.
"""

from __future__ import annotations

import os
import re
import sqlite3
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import Field, field_validator, model_validator

from sed.errors import ValidationFailed
from sed.paths import Paths
from sed.settings import StrictModel, _validate, deep_merge, load_layered, read_yaml

CONFIG_FILE = "auth.yaml"
PROVIDERS = ("microsoft", "google", "github")  # display order on the sign-in screen
LABELS = {"microsoft": "Microsoft", "google": "Google", "github": "GitHub"}
CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{3,199}$")
GUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
EMAIL_RE = re.compile(r"^[^@\s<>\"'(),;:\\]+@[^@\s<>\"'(),;:\\]+\.[^@\s<>\"'(),;:\\]+$")

Mode = Literal["sign_in", "developer"]


class ProviderSettings(StrictModel):
    enabled: bool = False
    client_id: str = ""


class MicrosoftSettings(ProviderSettings):
    tenant_id: str = ""  # the directory (tenant) ID of the organisation; also the only tenant that may sign in
    # The host of the registered redirect `http://<host>/auth/callback`. Microsoft ignores the port for localhost.
    redirect_host: Literal["localhost", "127.0.0.1"] = "localhost"

    @field_validator("tenant_id")
    @classmethod
    def _tenant(cls, value: str) -> str:
        return value.strip().lower()


class ProvidersSettings(StrictModel):
    microsoft: MicrosoftSettings = MicrosoftSettings()
    google: ProviderSettings = ProviderSettings()
    github: ProviderSettings = ProviderSettings()


class AllowSettings(StrictModel):
    """Who may sign in. Signing in proves an identity; only this list authorises it."""

    emails: list[str] = Field(default_factory=list)  # verified addresses, any provider
    microsoft_tenants: list[str] = Field(default_factory=list)  # everyone in these organisations (Microsoft only)

    @field_validator("emails")
    @classmethod
    def _emails(cls, values: list[str]) -> list[str]:
        out: list[str] = []
        for value in values:
            email = str(value).strip().lower()
            if not EMAIL_RE.match(email):
                raise ValueError(f"'{value}' is not an e-mail address")
            if email not in out:
                out.append(email)
        return out

    @field_validator("microsoft_tenants")
    @classmethod
    def _tenants(cls, values: list[str]) -> list[str]:
        out: list[str] = []
        for value in values:
            tenant = str(value).strip().lower()
            if not GUID_RE.match(tenant):
                raise ValueError(f"'{value}' is not a directory (tenant) ID")
            if tenant not in out:
                out.append(tenant)
        return out


class AuthSettings(StrictModel):
    mode: Literal["auto", "sign_in", "developer"] = "auto"
    session_hours: int = Field(12, ge=1, le=24)  # a sign-in lasts this long, or until `sed serve` stops
    providers: ProvidersSettings = ProvidersSettings()
    allow: AllowSettings = AllowSettings()

    @model_validator(mode="after")
    def _enabled_providers_are_complete(self) -> AuthSettings:
        for key in PROVIDERS:
            provider = getattr(self.providers, key)
            if provider.enabled and not CLIENT_ID_RE.match(provider.client_id):
                raise ValueError(f"providers.{key} is switched on but its client_id is missing or malformed")
        microsoft = self.providers.microsoft
        if microsoft.enabled and not GUID_RE.match(microsoft.tenant_id):
            raise ValueError("providers.microsoft is switched on but its tenant_id is not a directory (tenant) ID")
        return self

    def enabled(self) -> list[str]:
        return [key for key in PROVIDERS if getattr(self.providers, key).enabled]

    def problems(self) -> list[str]:
        """Why nobody could sign in with these settings (empty when someone can)."""
        out = []
        if not self.enabled():
            out.append("no sign-in provider is switched on")
        if not self.allow.emails and not (self.allow.microsoft_tenants and self.providers.microsoft.enabled):
            out.append("nobody is on the list of people allowed in")
        return out


def load_auth_settings(paths: Paths | None) -> AuthSettings:
    return _validate(AuthSettings, load_layered(CONFIG_FILE, paths), CONFIG_FILE)


def effective_data_class(paths: Paths) -> str:
    """The profile's data class, or "real" whenever the database says so: a synthetic profile name pointed at a real
    data folder (`--data-dir`) must not skip sign-in. A database that cannot be read counts as real."""
    if paths.data_class == "real":
        return "real"
    if not paths.db.is_file():
        return paths.data_class
    try:
        conn = sqlite3.connect(f"file:{paths.db.resolve().as_posix()}?mode=ro", uri=True)
        try:
            row = conn.execute("SELECT value FROM meta WHERE key = 'data_class'").fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return "real"
    if row is None:  # a database that has not declared its data class yet
        return paths.data_class
    return "synthetic" if row[0] == "synthetic" else "real"


def resolve_mode(settings: AuthSettings, paths: Paths, *, developer_mode: bool = False) -> Mode:
    if developer_mode:
        return "developer"
    real = effective_data_class(paths) == "real"
    if settings.mode == "developer":
        if real:
            raise ValidationFailed(
                "auth.yaml: developer mode cannot be switched on in config for real data. Start "
                "`sed serve --developer-mode` instead: it lasts one launch and marks every screen."
            )
        return "developer"
    if settings.mode == "sign_in":
        return "sign_in"
    return "sign_in" if real else "developer"


# -- writing DATA_DIR\config\auth.yaml (`sed auth ...`) ------------------------------------------------------------


def local_file(paths: Paths) -> Path:
    return paths.config / CONFIG_FILE


def update_local(
    paths: Paths,
    change: Callable[[dict[str, Any]], None],
    *,
    on_change: Callable[[dict[str, Any], dict[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    """Apply `change` to the DATA_DIR layer of auth.yaml.

    In this order: the merged settings are validated in memory; `on_change(before, after)` puts the change on the audit
    trail and answers the attempt (raising when it cannot, and then nothing is written); the file is replaced
    atomically; the attempt records how it ended. The previous file comes back on any failure. Returns the local layer
    before and after.
    """
    target = local_file(paths)
    previous = target.read_bytes() if target.is_file() else None
    before = read_yaml(target) if previous is not None else {}
    after = yaml.safe_load(yaml.safe_dump(before))  # a deep copy with YAML types only
    change(after)
    _validate(AuthSettings, deep_merge(load_layered(CONFIG_FILE, None), after), CONFIG_FILE)
    text = "# Sign-in settings for this profile, written by `sed auth` (docs/sign-in.md).\n" + yaml.safe_dump(
        after, sort_keys=False, allow_unicode=True
    )
    attempt = on_change(before, after) if on_change is not None and before != after else None
    try:
        _replace(target, text.encode("utf-8"))
        load_auth_settings(paths)  # the saved file reads back as it was validated
    except BaseException as exc:
        if attempt is not None:  # first: putting the old file back can fail the same way
            from sed.audit.record import failure_reason

            attempt.failed(failure_reason(exc))
        if previous is None:
            target.unlink(missing_ok=True)
        else:
            _replace(target, previous)
        raise
    if attempt is not None:
        attempt.done()
    return {"path": target.as_posix(), "before": before, "after": after}


def _replace(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
