"""A stand-in for Microsoft, Google and GitHub, so sign-in tests never touch the network.

Import explicitly: `from tests.fixtures.auth import FakeProviders, Clock, auth_settings, sign_in_runtime`.
Every value is synthetic; ID tokens are built at run time (never written as literals) and are not signed, which is what
SED expects of a token that comes straight from the token endpoint.
"""

from __future__ import annotations

import base64
import json
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

from sed.auth.runtime import AuthRuntime
from sed.auth.settings import AuthSettings

GOOGLE_CLIENT = "test-google-client.apps.example"
GITHUB_CLIENT = "test-github-client"
MICROSOFT_CLIENT = "00000000-0000-4000-8000-000000000001"
TENANT = "00000000-0000-4000-8000-0000000000aa"
OTHER_TENANT = "00000000-0000-4000-8000-0000000000bb"
OWNER = "owner@example.com"
T0 = 1_790_000_000.0  # a fixed "now" for the fake clock


def auth_settings(**allow: Any) -> AuthSettings:
    """All three providers on, with the owner allowed unless `emails`/`microsoft_tenants` say otherwise."""
    return AuthSettings.model_validate(
        {
            "mode": "sign_in",
            "providers": {
                "microsoft": {"enabled": True, "client_id": MICROSOFT_CLIENT, "tenant_id": TENANT},
                "google": {"enabled": True, "client_id": GOOGLE_CLIENT},
                "github": {"enabled": True, "client_id": GITHUB_CLIENT},
            },
            "allow": {"emails": allow.get("emails", [OWNER]), "microsoft_tenants": allow.get("microsoft_tenants", [])},
        }
    )


@dataclass
class Clock:
    now: float = T0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def id_token(claims: dict[str, Any]) -> str:
    def part(data: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode("utf-8")).rstrip(b"=").decode("ascii")

    return f"{part({'alg': 'RS256', 'typ': 'JWT'})}.{part(claims)}.unsigned"


@dataclass
class FakeProviders:
    """Answers token and API calls like the real endpoints. Set `claims` (ID token) or the GitHub fields per test."""

    clock: Clock
    claims: dict[str, Any] = field(default_factory=dict)
    token_status: int = 200
    token_error: dict[str, Any] | None = None
    github_polls: list[dict[str, Any]] = field(default_factory=list)  # answers to successive device polls
    github_user: dict[str, Any] = field(default_factory=lambda: {"id": 42, "login": "octo-owner", "name": "Owner"})
    github_emails: list[dict[str, Any]] = field(
        default_factory=lambda: [{"email": OWNER, "verified": True, "primary": True}]
    )
    calls: list[tuple[str, str, dict[str, str]]] = field(default_factory=list)

    def post_form(self, url: str, form: dict[str, str], headers: dict[str, str] | None = None) -> tuple[int, Any]:
        self.calls.append(("POST", url, dict(form)))
        if url == "https://github.com/login/device/code":
            return 200, {
                "device_code": "device-code-1",
                "user_code": "WDJB-MJHT",
                "verification_uri": "https://github.com/login/device",
                "expires_in": 900,
                "interval": 5,
            }
        if url == "https://github.com/login/oauth/access_token":
            return 200, self.github_polls.pop(0) if self.github_polls else {"error": "authorization_pending"}
        if self.token_error is not None:
            return self.token_status, self.token_error
        return self.token_status, {"access_token": "at", "id_token": id_token(self.claims)}

    def get_json(self, url: str, headers: dict[str, str] | None = None) -> tuple[int, Any]:
        self.calls.append(("GET", url, {}))
        if url == "https://api.github.com/user":
            return 200, self.github_user
        if url == "https://api.github.com/user/emails":
            return 200, self.github_emails
        return 404, None

    def forms(self, url_part: str) -> list[dict[str, str]]:
        return [form for method, url, form in self.calls if method == "POST" and url_part in url]

    # -- ID token claims as each provider would send them --------------------------------------------------------

    def google(self, nonce: str, /, **overrides: Any) -> None:
        self.claims = {
            "iss": "https://accounts.google.com",
            "aud": GOOGLE_CLIENT,
            "sub": "google-sub-1",
            "email": OWNER,
            "email_verified": True,
            "name": "Owner",
            "exp": self.clock.now + 3600,
            "iat": self.clock.now,
            "nonce": nonce,
            **overrides,
        }

    def microsoft(self, nonce: str, /, **overrides: Any) -> None:
        tenant = overrides.pop("tid", TENANT)
        self.claims = {
            "iss": f"https://login.microsoftonline.com/{tenant}/v2.0",
            "aud": MICROSOFT_CLIENT,
            "tid": tenant,
            "oid": "microsoft-oid-1",
            "preferred_username": OWNER,
            "name": "Owner",
            "exp": self.clock.now + 3600,
            "iat": self.clock.now,
            "nonce": nonce,
            **overrides,
        }


def query(url: str) -> dict[str, str]:
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))


def sign_in_runtime(settings: AuthSettings | None = None, events: list | None = None, **kwargs: Any):
    """(runtime, fake providers, clock) for a sign-in-required runtime; `events` collects what it reports."""
    clock = Clock()
    fake = FakeProviders(clock)
    runtime = AuthRuntime(
        settings or auth_settings(),
        "sign_in",
        transport=fake,
        clock=clock,
        on_event=events.append if events is not None else None,
        **kwargs,
    )
    return runtime, fake, clock
