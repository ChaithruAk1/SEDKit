"""Who a provider says signed in, and whether the allowlist lets them in.

Signing in with *a* Google or GitHub account proves an identity, not an authorisation: only the allowlist in
auth.yaml decides. Addresses are compared only when the provider vouches for them (Google `email_verified`, GitHub
verified addresses, Microsoft accounts of the configured organisation).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import cast

from sed.auth.actor import Actor, Method
from sed.auth.settings import LABELS, AuthSettings


@dataclass(frozen=True)
class Identity:
    provider: str  # microsoft | google | github
    subject: str  # the provider's stable id for the person
    emails: tuple[str, ...] = ()  # addresses the provider vouches for, lower case, best first
    name: str | None = None
    login: str | None = None  # GitHub login, or the Microsoft sign-in name
    tenant: str | None = None  # Microsoft directory (tenant) ID
    guest: bool = False  # Microsoft: an account from elsewhere that the organisation invited
    email: str | None = None  # the address that matched the allowlist (set by `decide`)

    def shown(self) -> str:
        """How the person is named in messages: their address, else their login, else the provider id."""
        return self.email or (self.emails[0] if self.emails else None) or self.login or f"{self.provider} account"

    def actor(self) -> Actor:
        who = self.shown()
        method = cast(Method, self.provider)
        return Actor(id=who, name=self.name or who, method=method, verified=True, channel="dashboard")


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str  # plain English, shown on the refusal page and recorded in the audit trail
    identity: Identity  # with `email` set to the address that matched, when one did


def decide(identity: Identity, settings: AuthSettings) -> Decision:
    allow = settings.allow
    label = LABELS.get(identity.provider, identity.provider)
    if identity.provider == "microsoft":
        configured = settings.providers.microsoft.tenant_id
        if not identity.tenant or identity.tenant != configured:
            return Decision(False, "This Microsoft account belongs to a different organisation.", identity)
        # A whole organisation means its own people; a guest it invited gets in only by their own listed address.
        if identity.tenant in allow.microsoft_tenants and not identity.guest:
            email = identity.emails[0] if identity.emails else None
            return Decision(True, "Everyone in this organisation may use SED.", _with_email(identity, email))
    matched = next((e for e in identity.emails if e in allow.emails), None)
    if matched:
        return Decision(True, f"{matched} is on the list of people allowed in.", _with_email(identity, matched))
    if not identity.emails:
        return Decision(False, f"{label} did not confirm an e-mail address for this account.", identity)
    return Decision(False, f"{identity.shown()} is not on the list of people allowed to use SED.", identity)


def _with_email(identity: Identity, email: str | None) -> Identity:
    return replace(identity, email=email)
