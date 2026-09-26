"""PKCE, random values and ID-token checks.

The ID token reaches SED straight from the provider's token endpoint over TLS, never through the browser. For that case
OpenID Connect Core (3.1.3.7, step 6) lets TLS vouch for the issuer instead of the token's signature, so the signature
is not checked; the issuer, audience, expiry and nonce always are. The authorization code itself is bound to this
sign-in by PKCE, so a code copied from elsewhere cannot be redeemed.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from collections.abc import Collection
from typing import Any

CLOCK_LEEWAY_SECONDS = 300


class SignInFailedError(Exception):
    """A sign-in that did not complete. The message is plain English and safe to show and to record."""


def random_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def pkce_pair() -> tuple[str, str]:
    """(code_verifier, S256 code_challenge). The verifier is 86 characters of the unreserved set (RFC 7636: 43-128)."""
    verifier = secrets.token_urlsafe(64)
    return verifier, b64url(hashlib.sha256(verifier.encode("ascii")).digest())


def id_token_claims(token: Any) -> dict[str, Any]:
    parts = token.split(".") if isinstance(token, str) else []
    if len(parts) != 3 or not parts[1]:
        raise SignInFailedError("The provider's answer did not contain an ID token.")
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
    except (ValueError, UnicodeError) as exc:
        raise SignInFailedError("The provider's ID token could not be read.") from exc
    if not isinstance(claims, dict):
        raise SignInFailedError("The provider's ID token could not be read.")
    return claims


def check_claims(
    claims: dict[str, Any],
    *,
    issuers: Collection[str],
    audience: str,
    nonce: str,
    now: float | None = None,
    leeway: int = CLOCK_LEEWAY_SECONDS,
) -> None:
    now = time.time() if now is None else now
    if claims.get("iss") not in issuers:
        raise SignInFailedError("The sign-in answer came from an unexpected issuer.")
    aud = claims.get("aud")
    audiences = aud if isinstance(aud, list) else [aud]
    if audience not in audiences or (len(audiences) > 1 and claims.get("azp") != audience):
        raise SignInFailedError("The sign-in answer was meant for a different application.")
    exp = claims.get("exp")
    if not isinstance(exp, int | float) or isinstance(exp, bool) or exp + leeway < now:
        raise SignInFailedError("The sign-in answer has expired. Please try again.")
    supplied = str(claims.get("nonce") or "").encode("utf-8")
    if not supplied or not hmac.compare_digest(supplied, nonce.encode("utf-8")):
        raise SignInFailedError("The sign-in answer does not belong to this sign-in. Please try again.")
