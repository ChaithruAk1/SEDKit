"""The three identity providers.

* Microsoft and Google: authorization code with PKCE and a loopback redirect (RFC 8252), as a public client. There is
  no client secret, so none can leak. Who signed in comes from the ID token (scopes `openid email profile`).
* GitHub: its browser sign-in needs the app's client secret for every token exchange, so SED uses GitHub's device code
  sign-in instead (RFC 8628), which needs none. SED shows a short code and the person enters it at github.com. Who
  signed in comes from the GitHub API (/user and /user/emails), and the GitHub token is dropped once it has answered.

Nothing here keeps a provider token: SED only needs to know who signed in.
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass
from typing import Any

from sed.auth.http import AuthTransport
from sed.auth.identity import Identity
from sed.auth.oidc import SignInFailedError, check_claims, id_token_claims
from sed.auth.settings import LABELS, AuthSettings

FLOWS = {"microsoft": "redirect", "google": "redirect", "github": "device"}
CALLBACK_PATH = "/auth/callback"

GOOGLE_AUTHORIZE = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")
MICROSOFT_BASE = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0"
MICROSOFT_ISSUER = "https://login.microsoftonline.com/{tenant}/v2.0"
OIDC_SCOPE = "openid email profile"

GITHUB_DEVICE_CODE = "https://github.com/login/device/code"
GITHUB_TOKEN = "https://github.com/login/oauth/access_token"
GITHUB_API = "https://api.github.com"
GITHUB_SCOPE = "user:email"  # enough to read the account's verified addresses; nothing else
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"


def redirect_host(provider: str, settings: AuthSettings) -> str:
    """The loopback host registered with the provider. Google takes 127.0.0.1 on any port (a "Desktop app" client);
    Microsoft ignores the port of a localhost redirect, which is what an app registration usually carries."""
    if provider == "microsoft":
        return settings.providers.microsoft.redirect_host
    return "127.0.0.1"


def authorize_url(
    provider: str, settings: AuthSettings, *, redirect_uri: str, state: str, nonce: str, challenge: str
) -> str:
    params = {
        "client_id": _client_id(provider, settings),
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": OIDC_SCOPE,
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "prompt": "select_account",
    }
    if provider == "google":
        return f"{GOOGLE_AUTHORIZE}?{urllib.parse.urlencode(params)}"
    if provider == "microsoft":
        base = MICROSOFT_BASE.format(tenant=settings.providers.microsoft.tenant_id)
        return f"{base}/authorize?{urllib.parse.urlencode({**params, 'response_mode': 'query'})}"
    raise SignInFailedError(f"{LABELS.get(provider, provider)} does not sign in through a redirect.")


def redeem_code(
    provider: str,
    settings: AuthSettings,
    *,
    code: str,
    verifier: str,
    redirect_uri: str,
    nonce: str,
    transport: AuthTransport,
    now: float | None = None,
) -> Identity:
    """Exchange the authorization code (with the PKCE verifier) for an ID token and read who signed in."""
    client_id = _client_id(provider, settings)
    form = {
        "client_id": client_id,
        "code": code,
        "code_verifier": verifier,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }
    if provider == "google":
        url, issuers = GOOGLE_TOKEN, GOOGLE_ISSUERS
    elif provider == "microsoft":
        tenant = settings.providers.microsoft.tenant_id
        url, issuers = f"{MICROSOFT_BASE.format(tenant=tenant)}/token", (MICROSOFT_ISSUER.format(tenant=tenant),)
        form["scope"] = OIDC_SCOPE
    else:
        raise SignInFailedError(f"{LABELS.get(provider, provider)} does not sign in through a redirect.")
    status, body = transport.post_form(url, form)
    if status != 200 or not isinstance(body, dict) or "id_token" not in body:
        raise SignInFailedError(_token_error(provider, body, status))
    claims = id_token_claims(body["id_token"])
    check_claims(claims, issuers=issuers, audience=client_id, nonce=nonce, now=now)
    return _google_identity(claims) if provider == "google" else _microsoft_identity(claims)


def _google_identity(claims: dict[str, Any]) -> Identity:
    email = _text(claims.get("email")).lower()
    verified = claims.get("email_verified") in (True, "true")
    return Identity(
        provider="google",
        subject=_text(claims.get("sub")),
        emails=(email,) if email and verified else (),
        name=_text(claims.get("name")) or None,
    )


def _microsoft_identity(claims: dict[str, Any]) -> Identity:
    # The sign-in name (usually the address) first, then the mail claim. Both are set by the organisation, and
    # `decide` only compares them for accounts of the configured organisation. A guest (an account from elsewhere
    # that the organisation invited) signs in through an identity provider other than the organisation itself: the
    # `idp` claim then differs from the issuer (it is absent for the organisation's own accounts).
    login = _text(claims.get("preferred_username"))
    emails = tuple(dict.fromkeys(v.lower() for v in (login, _text(claims.get("email"))) if "@" in v))
    idp = _text(claims.get("idp"))
    return Identity(
        provider="microsoft",
        subject=_text(claims.get("oid") or claims.get("sub")),
        emails=emails,
        name=_text(claims.get("name")) or None,
        login=login or None,
        tenant=_text(claims.get("tid")).lower() or None,
        guest=bool(idp) and idp != _text(claims.get("iss")),
    )


# -- GitHub device code ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DeviceCode:
    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int


def github_device_start(settings: AuthSettings, transport: AuthTransport) -> DeviceCode:
    status, body = transport.post_form(
        GITHUB_DEVICE_CODE, {"client_id": _client_id("github", settings), "scope": GITHUB_SCOPE}
    )
    if status != 200 or not isinstance(body, dict) or "device_code" not in body:
        raise SignInFailedError(_token_error("github", body, status))
    uri = _text(body.get("verification_uri"))
    if not uri.startswith("https://github.com/"):
        raise SignInFailedError("GitHub answered with an unexpected sign-in page.")
    return DeviceCode(
        device_code=_text(body["device_code"]),
        user_code=_text(body.get("user_code")),
        verification_uri=uri,
        expires_in=_int(body.get("expires_in"), 900),
        interval=max(_int(body.get("interval"), 5), 1),
    )


def github_device_poll(
    settings: AuthSettings, device_code: str, transport: AuthTransport
) -> tuple[str, Identity | None, int | None]:
    """One poll: ("pending"|"slow_down"|"expired"|"denied", None, new interval) or ("signed_in", identity, None)."""
    form = {"client_id": _client_id("github", settings), "device_code": device_code, "grant_type": DEVICE_GRANT}
    status, body = transport.post_form(GITHUB_TOKEN, form)
    body = body if isinstance(body, dict) else {}
    token = body.get("access_token")
    if status == 200 and isinstance(token, str) and token:
        return "signed_in", _github_identity(token, transport), None
    error = _code(body.get("error"))
    if error == "authorization_pending":
        return "pending", None, None
    if error == "slow_down":
        return "slow_down", None, _int(body.get("interval"), 0) or None
    if error in ("expired_token", "token_expired"):
        return "expired", None, None
    if error == "access_denied":
        return "denied", None, None
    raise SignInFailedError(_token_error("github", body, status))


def _github_identity(token: str, transport: AuthTransport) -> Identity:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    status, user = transport.get_json(f"{GITHUB_API}/user", headers)
    if status != 200 or not isinstance(user, dict) or user.get("id") is None:
        raise SignInFailedError("GitHub did not say who signed in.")
    status, listed = transport.get_json(f"{GITHUB_API}/user/emails", headers)
    rows = listed if status == 200 and isinstance(listed, list) else []
    verified = [r for r in rows if isinstance(r, dict) and r.get("verified") is True and "@" in _text(r.get("email"))]
    verified.sort(key=lambda r: r.get("primary") is not True)  # the primary address first
    login = _text(user.get("login"))
    return Identity(
        provider="github",
        subject=_text(user.get("id")),
        emails=tuple(dict.fromkeys(_text(r["email"]).lower() for r in verified)),
        name=_text(user.get("name")) or login or None,
        login=login or None,
    )


# -- helpers ---------------------------------------------------------------------------------------------------------


def _client_id(provider: str, settings: AuthSettings) -> str:
    config = getattr(settings.providers, provider, None)
    if config is None or not config.enabled:
        raise SignInFailedError(f"Sign-in with {LABELS.get(provider, provider)} is not switched on.")
    return config.client_id


def _token_error(provider: str, body: Any, status: int) -> str:
    """A plain message for a refused exchange. Only the provider's short error code is repeated, never its text."""
    label = LABELS.get(provider, provider)
    body = body if isinstance(body, dict) else {}
    error = _code(body.get("error"))
    if status == 404 or error in ("not_found", "notfound"):  # GitHub answers 404 for a client ID it does not know
        error = "invalid_client"
    described = _text(body.get("error_description")).lower()
    if "client_secret" in described or "client secret" in described:
        return f"{label} asked for a client secret, which SED never stores. Check the client type (docs/sign-in.md)."
    if error == "device_flow_disabled":
        return "Device sign-in is off for SED's GitHub app: tick 'Enable Device Flow' in its settings."
    if error in ("invalid_client", "unauthorized_client", "incorrect_client_credentials"):
        return f"{label} does not recognise SED's client ID. Check it in the sign-in settings (docs/sign-in.md)."
    if error == "invalid_grant":
        return f"{label} refused the sign-in code (it may have expired or been used already). Please try again."
    return f"{label} refused the sign-in" + (f" ({error})." if error else ".")


def _code(value: Any) -> str:
    return re.sub(r"[^a-z0-9_]", "", str(value or "").lower())[:40]


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
