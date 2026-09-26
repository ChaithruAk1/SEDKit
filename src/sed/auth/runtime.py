"""Sign-ins in progress and the live session, held in memory by `sed serve`.

One signed-in person at a time is the whole model: a new sign-in ends any other session. Nothing here is written to
disk, so stopping `sed serve` signs everyone out. Session tokens are held only as their SHA-256.

A redirect sign-in is tied to the browser that started it: the start sets a short-lived cookie (the binding) and the
code is redeemed only for a request that carries it. A sign-in answer copied from elsewhere, or caught by another
program listening on a loopback port, therefore cannot become a session. When the provider answers on the other
loopback name (the binding cookie lives on the host that started), the code is parked unredeemed and collected by that
browser at /auth/finish.

Every outcome worth recording (sign-in, refusal, failure, sign-out, a session ended by another sign-in) is reported to
`on_event`. A sign-in is only completed once its event was accepted, so the audit trail can never miss one; the other
events never block anything.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import threading
import time
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from sed.auth.actor import Actor, developer_actor
from sed.auth.http import AuthTransport, HttpsTransport
from sed.auth.identity import Identity, decide
from sed.auth.oidc import SignInFailedError, pkce_pair, random_token
from sed.auth.providers import (
    CALLBACK_PATH,
    FLOWS,
    authorize_url,
    github_device_poll,
    github_device_start,
    redeem_code,
    redirect_host,
)
from sed.auth.settings import LABELS, AuthSettings, Mode

log = logging.getLogger("sed.auth")

FLOW_SECONDS = 600  # a started sign-in must finish within ten minutes
HANDOFF_SECONDS = 60  # a parked answer waits this long for the browser that started the sign-in
MAX_PENDING = 16  # sign-ins in progress; the oldest is dropped beyond this

EventKind = Literal["sign_in", "sign_in_refused", "sign_in_failed", "sign_out", "session_replaced"]
Status = Literal["signed_in", "refused", "failed", "cancelled", "pending", "expired", "handoff"]


@dataclass(frozen=True)
class AuthEvent:
    kind: EventKind
    provider: str | None
    identity: Identity | None
    message: str
    detail: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class Session:
    identity: Identity
    started_at: float
    expires_at: float

    @property
    def actor(self) -> Actor:
        return self.identity.actor()


@dataclass(frozen=True)
class Outcome:
    status: Status
    message: str
    token: str | None = None  # the new session token, when signed in
    origin: str | None = None  # where a redirect sign-in was started (scheme://host:port)
    identity: Identity | None = None
    handoff: str | None = None  # status "handoff": the key the starting browser collects at /auth/finish


@dataclass
class _Redirect:
    provider: str
    verifier: str
    nonce: str
    redirect_uri: str
    origin: str
    binding: str  # SHA-256 of the binding cookie set on the browser that started
    expires: float


@dataclass
class _Parked:
    pending: _Redirect
    code: str
    expires: float


@dataclass
class _Device:
    provider: str
    device_code: str
    interval: int
    next_poll: float
    expires: float


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def origin_port(origin: str) -> int:
    parts = urllib.parse.urlsplit(origin)
    try:
        return parts.port or 80
    except ValueError:
        return 80


class AuthRuntime:
    def __init__(
        self,
        settings: AuthSettings,
        mode: Mode,
        *,
        transport: AuthTransport | None = None,
        clock: Callable[[], float] = time.time,
        on_event: Callable[[AuthEvent], None] | None = None,
    ) -> None:
        self.settings = settings
        self.mode: Mode = mode
        self.transport = transport or HttpsTransport()
        self.clock = clock
        self.on_event = on_event
        self._lock = threading.Lock()
        self._redirects: dict[str, _Redirect] = {}
        self._parked: dict[str, _Parked] = {}
        self._devices: dict[str, _Device] = {}
        self._sessions: dict[str, Session] = {}
        self._developer = developer_actor()

    # -- what the sign-in screen offers --------------------------------------------------------------------------

    def providers(self) -> list[tuple[str, str, str]]:
        """(key, label, flow) for every switched-on provider; none in developer mode."""
        if self.mode != "sign_in":
            return []
        return [(key, LABELS[key], FLOWS[key]) for key in self.settings.enabled()]

    def cookie_name(self, port: int) -> str:
        # Cookies are shared by every port of a host, so two `sed serve` launches (say synthetic and real) must not
        # overwrite each other's session.
        return f"sed_session_{port}"

    def binding_cookie_name(self, port: int) -> str:
        return f"sed_signin_{port}"

    # -- redirect sign-in (Microsoft, Google) --------------------------------------------------------------------

    def start_redirect(self, provider: str, origin: str) -> tuple[str, str]:
        """(the provider's sign-in address, the value for the starting browser's binding cookie)."""
        self._require(provider, "redirect")
        state, nonce, binding = random_token(), random_token(), random_token()
        verifier, challenge = pkce_pair()
        redirect_uri = f"http://{redirect_host(provider, self.settings)}:{origin_port(origin)}{CALLBACK_PATH}"
        url = authorize_url(
            provider, self.settings, redirect_uri=redirect_uri, state=state, nonce=nonce, challenge=challenge
        )
        now = self.clock()
        pending = _Redirect(provider, verifier, nonce, redirect_uri, origin, _digest(binding), now + FLOW_SECONDS)
        with self._lock:
            self._purge(now)
            self._redirects[state] = pending
            while len(self._redirects) > MAX_PENDING:
                self._redirects.pop(next(iter(self._redirects)))
        return url, binding

    def finish_redirect(
        self, *, state: str | None, code: str | None, error: str | None, binding: str | None, here: str
    ) -> Outcome:
        """The provider's answer at /auth/callback, which arrived at `here` (scheme://host:port)."""
        now = self.clock()
        with self._lock:
            self._purge(now)
            pending = self._redirects.pop(state, None) if state else None
        if pending is None:
            # Not a sign-in this SED started, or one already finished: nothing to record.
            return Outcome("failed", "This sign-in has expired or was already used. Please start again from SED.")
        label = LABELS[pending.provider]
        if error or not code:
            cancelled = error == "access_denied"
            message = "Sign-in was cancelled." if cancelled else f"{label} did not complete the sign-in."
            self._emit(AuthEvent("sign_in_failed", pending.provider, None, message, {"error": _short(error)}))
            return Outcome("cancelled" if cancelled else "failed", message, origin=pending.origin)
        if here != pending.origin:
            key = random_token()
            with self._lock:
                self._parked[key] = _Parked(pending, code, now + HANDOFF_SECONDS)
            return Outcome("handoff", "Returning to SED.", origin=pending.origin, handoff=key)
        return self._redeem(pending, code, binding)

    def finish_handoff(self, handoff: str | None, binding: str | None) -> Outcome:
        """Collect a parked answer on the host where the sign-in started (/auth/finish)."""
        with self._lock:
            self._purge(self.clock())
            parked = self._parked.pop(handoff, None) if handoff else None
        if parked is None:
            return Outcome("expired", "This sign-in link has expired or was already used. Please sign in again.")
        return self._redeem(parked.pending, parked.code, binding)

    def _redeem(self, pending: _Redirect, code: str, binding: str | None) -> Outcome:
        supplied = _digest(binding) if binding else ""
        if not hmac.compare_digest(supplied, pending.binding):
            message = "This sign-in was finished in another browser than the one that started it. Please start again."
            self._emit(AuthEvent("sign_in_failed", pending.provider, None, message, {"error": "other_browser"}))
            return Outcome("failed", message, origin=pending.origin)
        try:
            identity = redeem_code(
                pending.provider,
                self.settings,
                code=code,
                verifier=pending.verifier,
                redirect_uri=pending.redirect_uri,
                nonce=pending.nonce,
                transport=self.transport,
                now=self.clock(),
            )
        except SignInFailedError as exc:
            self._emit(AuthEvent("sign_in_failed", pending.provider, None, str(exc)))
            return Outcome("failed", str(exc), origin=pending.origin)
        return self._admit(identity, origin=pending.origin)

    # -- device code sign-in (GitHub) ----------------------------------------------------------------------------

    def start_device(self, provider: str) -> tuple[str, str, str, int, int]:
        """(flow_id, user_code, verification_uri, expires_in, interval). Raises SignInFailedError."""
        self._require(provider, "device")
        code = github_device_start(self.settings, self.transport)
        flow_id = random_token()
        now = self.clock()
        expires_in = min(code.expires_in, FLOW_SECONDS * 2)
        with self._lock:
            self._purge(now)
            self._devices[flow_id] = _Device(
                provider, code.device_code, code.interval, now + code.interval, now + expires_in
            )
            while len(self._devices) > MAX_PENDING:
                self._devices.pop(next(iter(self._devices)))
        return flow_id, code.user_code, code.verification_uri, expires_in, code.interval

    def poll_device(self, flow_id: str) -> Outcome:
        now = self.clock()
        with self._lock:
            self._purge(now)
            device = self._devices.get(flow_id)
            if device is None:
                return Outcome("expired", "This sign-in has expired. Please start again.")
            if now < device.next_poll:
                return Outcome("pending", "Waiting for the code to be entered at GitHub.")
            device.next_poll = now + device.interval  # never ask GitHub faster than it allows
        try:
            status, identity, interval = github_device_poll(self.settings, device.device_code, self.transport)
        except SignInFailedError as exc:
            self._drop_device(flow_id)
            self._emit(AuthEvent("sign_in_failed", device.provider, None, str(exc)))
            return Outcome("failed", str(exc))
        if status in ("pending", "slow_down"):
            if status == "slow_down":
                with self._lock:
                    device.interval = interval or device.interval + 5
                    device.next_poll = self.clock() + device.interval
            return Outcome("pending", "Waiting for the code to be entered at GitHub.")
        self._drop_device(flow_id)
        if status == "expired":
            return Outcome("expired", "The code expired before it was entered. Please start again.")
        if status == "denied" or identity is None:
            message = "Sign-in was cancelled at GitHub."
            self._emit(AuthEvent("sign_in_failed", device.provider, None, message, {"error": "access_denied"}))
            return Outcome("cancelled", message)
        return self._admit(identity, origin=None)

    # -- sessions ------------------------------------------------------------------------------------------------

    def session(self, token: str | None) -> Session | None:
        if not token or self.mode != "sign_in":
            return None
        key = _digest(token)
        with self._lock:
            found = self._sessions.get(key)
            if found is not None and found.expires_at <= self.clock():
                del self._sessions[key]
                return None
            return found

    def actor_for(self, token: str | None) -> Actor | None:
        """Who is asking: the developer-mode actor, the signed-in person, or None (not signed in)."""
        if self.mode == "developer":
            return self._developer
        found = self.session(token)
        return found.actor if found else None

    def sign_out(self, token: str | None) -> Session | None:
        if not token:
            return None
        with self._lock:
            ended = self._sessions.pop(_digest(token), None)
        if ended is not None:
            self._emit(AuthEvent("sign_out", ended.identity.provider, ended.identity, "Signed out."))
        return ended

    # -- internals -----------------------------------------------------------------------------------------------

    def _require(self, provider: str, flow: str) -> None:
        if self.mode != "sign_in":
            raise SignInFailedError("Nobody signs in while SED runs in developer mode.")
        if provider not in self.settings.enabled():
            raise SignInFailedError(f"Sign-in with {LABELS.get(provider, provider)} is not switched on.")
        if FLOWS[provider] != flow:
            raise SignInFailedError(f"{LABELS[provider]} signs in another way.")

    def _admit(self, identity: Identity, *, origin: str | None) -> Outcome:
        decision = decide(identity, self.settings)
        if not decision.allowed:
            self._emit(AuthEvent("sign_in_refused", identity.provider, decision.identity, decision.reason))
            return Outcome("refused", decision.reason, origin=origin, identity=decision.identity)
        now = self.clock()
        session = Session(decision.identity, now, now + self.settings.session_hours * 3600)
        event = AuthEvent(
            "sign_in", identity.provider, decision.identity, decision.reason, {"expires_at": session.expires_at}
        )
        if self.on_event is not None:
            try:
                self.on_event(event)  # must succeed: a sign-in the audit trail cannot record does not happen
            except Exception as exc:
                log.error("sign-in not recorded: %s", type(exc).__name__)
                message = "SED could not record this sign-in in its audit trail, so it was stopped. Please try again."
                return Outcome("failed", message, origin=origin, identity=decision.identity)
        token = random_token()
        with self._lock:
            replaced = list(self._sessions.values())
            self._sessions = {_digest(token): session}
        for old in replaced:
            message = f"Session ended: {decision.identity.shown()} signed in."
            self._emit(AuthEvent("session_replaced", old.identity.provider, old.identity, message))
        return Outcome("signed_in", decision.reason, token=token, origin=origin, identity=decision.identity)

    def _emit(self, event: AuthEvent) -> None:
        if self.on_event is None:
            return
        try:
            self.on_event(event)
        except Exception as exc:  # never let recording block a refusal, failure or sign-out
            log.error("auth event %s not recorded: %s", event.kind, type(exc).__name__)

    def _drop_device(self, flow_id: str) -> None:
        with self._lock:
            self._devices.pop(flow_id, None)

    def _purge(self, now: float) -> None:
        """Forget expired sign-ins and parked answers. Call with the lock held."""
        for store in (self._redirects, self._parked, self._devices):
            for key in [k for k, v in store.items() if v.expires <= now]:
                del store[key]


def _short(value: str | None) -> str:
    return "".join(ch for ch in (value or "").lower() if ch.isalnum() or ch == "_")[:40]
