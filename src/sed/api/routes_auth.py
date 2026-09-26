"""Sign-in routes (docs/sign-in.md). The logic lives in `sed.auth`; these routes only carry it over HTTP.

Mounted at /api (public: the sign-in screen calls them before anyone is signed in):
- `GET /auth/session`: the mode, who is signed in, and which providers the sign-in screen offers.
- `POST /auth/start {provider}`: Microsoft or Google; answers the provider's sign-in address for the browser to open.
- `POST /auth/device/start {provider}`: GitHub; answers the code to enter at github.com.
- `POST /auth/device/poll {flow_id}`: whether the code was entered; sets the session cookie once signed in.
- `POST /auth/sign-out`.

Pages (no /api prefix, not in the API contract): `GET /auth/callback`, where Microsoft and Google send the browser back,
and `GET /auth/finish`, where the browser that started collects an answer that arrived on the other loopback name
(localhost versus 127.0.0.1; cookies are per host name).

Cookies: the session cookie is HttpOnly and SameSite=Strict, and lasts until the browser closes or the session
expires. The binding cookie (`sed_signin_<port>`, HttpOnly, SameSite=Lax so it rides along when the provider sends
the browser back, path /auth, ten minutes) ties a redirect sign-in to the browser that started it. POSTs need the
per-launch X-SED-Token like every other write.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import HTMLResponse

from sed.api.models import (
    AuthProviderOut,
    AuthSessionOut,
    AuthStartIn,
    AuthStartOut,
    AuthUserOut,
    DevicePollIn,
    DevicePollOut,
    DeviceStartOut,
    SignOutIn,
    SignOutOut,
)
from sed.auth.middleware import binding_cookie, request_origin, session_cookie
from sed.auth.oidc import SignInFailedError
from sed.auth.pages import page
from sed.auth.providers import CALLBACK_PATH
from sed.auth.runtime import FLOW_SECONDS, AuthRuntime, Outcome, origin_port
from sed.errors import PreconditionFailed

router = APIRouter()
pages = APIRouter()

TITLES = {
    "refused": "Not allowed",
    "failed": "Sign-in did not complete",
    "cancelled": "Sign-in cancelled",
    "expired": "Sign-in expired",
}


def _runtime(request: Request) -> AuthRuntime:
    return request.app.state.auth


def _set_cookie(response: Response, runtime: AuthRuntime, origin: str, token: str) -> None:
    name = runtime.cookie_name(origin_port(origin))
    response.set_cookie(name, token, path="/", httponly=True, samesite="strict")


def _clear_binding(response: Response, runtime: AuthRuntime, origin: str) -> None:
    response.delete_cookie(
        runtime.binding_cookie_name(origin_port(origin)), path="/auth", httponly=True, samesite="lax"
    )


def _outcome_page(runtime: AuthRuntime, outcome: Outcome, here: str) -> HTMLResponse:
    """The page for a finished redirect sign-in: signed in (session cookie, back to the dashboard) or why not."""
    if outcome.status == "signed_in" and outcome.token:
        response = page("Signed in", "Returning to SED.", go_to="/")
        _set_cookie(response, runtime, here, outcome.token)
        _clear_binding(response, runtime, here)
        return response
    status = 403 if outcome.status == "refused" else 400
    back = f"{outcome.origin}/" if outcome.origin else "/"
    return page(TITLES.get(outcome.status, "Sign-in"), outcome.message, link=back, status=status)


def _iso(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@router.get("/auth/session", response_model=AuthSessionOut)
def auth_session(request: Request) -> AuthSessionOut:
    # Who is signed in (or, in developer mode, whose Windows account actions go under) and what the sign-in screen
    # offers. Public, and never reveals who is on the allowlist.
    runtime = _runtime(request)
    if runtime.mode == "developer":
        actor = runtime.actor_for(None)
        name = actor.name if actor else "unknown"
        user = AuthUserOut(name=name, email=None, method="developer_mode", verified=False)
        return AuthSessionOut(
            mode="developer", signed_in=False, user=user, expires_at=None, providers=[], setup_needed=[]
        )
    providers = [AuthProviderOut.model_validate({"key": k, "label": lb, "flow": f}) for k, lb, f in runtime.providers()]
    session = runtime.session(session_cookie(request.scope, runtime))
    user = None
    if session is not None:
        who = session.identity
        user = AuthUserOut.model_validate(
            {"name": who.name or who.shown(), "email": who.email, "method": who.provider, "verified": True}
        )
    return AuthSessionOut(
        mode="sign_in",
        signed_in=session is not None,
        user=user,
        expires_at=_iso(session.expires_at) if session else None,
        providers=providers,
        setup_needed=runtime.settings.problems(),
    )


@router.post("/auth/start", response_model=AuthStartOut)
def auth_start(body: AuthStartIn, request: Request, response: Response) -> AuthStartOut:
    # Start a Microsoft or Google sign-in: the browser opens the answer, the provider sends it back to /auth/callback.
    # The binding cookie set here ties the answer to this browser.
    runtime = _runtime(request)
    origin = request_origin(request.scope)
    try:
        url, binding = runtime.start_redirect(body.provider, origin)
    except SignInFailedError as exc:
        raise PreconditionFailed(str(exc)) from exc
    name = runtime.binding_cookie_name(origin_port(origin))
    response.set_cookie(name, binding, max_age=FLOW_SECONDS, path="/auth", httponly=True, samesite="lax")
    return AuthStartOut(authorize_url=url)


@router.post("/auth/device/start", response_model=DeviceStartOut)
def device_start(body: AuthStartIn, request: Request) -> DeviceStartOut:
    # Start a GitHub sign-in: the person enters the code at the verification page while the screen polls.
    try:
        flow_id, user_code, uri, expires_in, interval = _runtime(request).start_device(body.provider)
    except SignInFailedError as exc:
        raise PreconditionFailed(str(exc)) from exc
    return DeviceStartOut(
        flow_id=flow_id, user_code=user_code, verification_uri=uri, expires_in=expires_in, interval=interval
    )


@router.post("/auth/device/poll", response_model=DevicePollOut)
def device_poll(body: DevicePollIn, request: Request, response: Response) -> DevicePollOut:
    # Has the code been entered? Once it has and the person is allowed in, the session cookie comes with the answer.
    runtime = _runtime(request)
    outcome = runtime.poll_device(body.flow_id)
    if outcome.status == "signed_in" and outcome.token:
        _set_cookie(response, runtime, request_origin(request.scope), outcome.token)
    return DevicePollOut(status=outcome.status, message=outcome.message)


@router.post("/auth/sign-out", response_model=SignOutOut)
def sign_out(body: SignOutIn, request: Request, response: Response) -> SignOutOut:
    # End the session and clear the cookie.
    runtime = _runtime(request)
    ended = runtime.sign_out(session_cookie(request.scope, runtime))
    name = runtime.cookie_name(origin_port(request_origin(request.scope)))
    response.delete_cookie(name, path="/", httponly=True, samesite="strict")
    return SignOutOut(signed_out=ended is not None)


@pages.get(CALLBACK_PATH, include_in_schema=False)
def auth_callback(
    request: Request,
    state: str | None = Query(None, max_length=512),
    code: str | None = Query(None, max_length=4096),
    error: str | None = Query(None, max_length=200),
) -> HTMLResponse:
    runtime = _runtime(request)
    here = request_origin(request.scope)
    outcome = runtime.finish_redirect(
        state=state, code=code, error=error, binding=binding_cookie(request.scope, runtime), here=here
    )
    if outcome.status == "handoff" and outcome.handoff and outcome.origin:
        # The answer arrived on the other loopback name: the browser that started collects it where it started.
        return page("Signing in", "Returning to SED.", go_to=f"{outcome.origin}/auth/finish?handoff={outcome.handoff}")
    return _outcome_page(runtime, outcome, here)


@pages.get("/auth/finish", include_in_schema=False)
def auth_finish(request: Request, handoff: str | None = Query(None, max_length=200)) -> HTMLResponse:
    runtime = _runtime(request)
    outcome = runtime.finish_handoff(handoff, binding_cookie(request.scope, runtime))
    return _outcome_page(runtime, outcome, request_origin(request.scope))
