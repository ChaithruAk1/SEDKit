"""Sign-in flows against a fake provider: PKCE and nonce binding, the allowlist, single session, expiry and events."""

from __future__ import annotations

import base64
import hashlib

import pytest

from sed.auth.oidc import SignInFailedError
from sed.auth.runtime import FLOW_SECONDS, AuthRuntime
from tests.fixtures.auth import (
    GOOGLE_CLIENT,
    OTHER_TENANT,
    OWNER,
    TENANT,
    auth_settings,
    query,
    sign_in_runtime,
)

ORIGIN = "http://127.0.0.1:8123"
BINDINGS: dict[str, str] = {}  # state -> the binding cookie value the starting "browser" holds


def start(runtime, provider, origin=ORIGIN):
    """Start a redirect sign-in as the browser would: keep the binding value, return the authorize URL's query."""
    url, binding = runtime.start_redirect(provider, origin)
    params = query(url)
    BINDINGS[params["state"]] = binding
    return params


def finish(runtime, state, code, error=None, *, here=ORIGIN, binding="same browser"):
    """The provider's answer arriving at `here`, from the browser that started (unless `binding` says otherwise)."""
    held = BINDINGS.get(state or "") if binding == "same browser" else binding
    return runtime.finish_redirect(state=state, code=code, error=error, binding=held, here=here)


def _start_google(runtime):
    params = start(runtime, "google")
    return params["state"], params["nonce"], params


def test_google_sign_in_binds_code_to_pkce_and_nonce():
    events: list = []
    runtime, fake, _ = sign_in_runtime(events=events)
    url, binding = runtime.start_redirect("google", ORIGIN)
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?") and binding not in url
    params = query(url)
    BINDINGS[params["state"]] = binding
    assert params["client_id"] == GOOGLE_CLIENT and params["response_type"] == "code"
    assert params["redirect_uri"] == "http://127.0.0.1:8123/auth/callback"
    assert params["code_challenge_method"] == "S256" and params["scope"] == "openid email profile"
    assert "client_secret" not in url

    fake.google(params["nonce"])
    outcome = finish(runtime, params["state"], "code-1", None)
    assert outcome.status == "signed_in" and outcome.token and outcome.origin == ORIGIN

    (form,) = fake.forms("oauth2.googleapis.com/token")
    assert form["code"] == "code-1" and form["redirect_uri"] == params["redirect_uri"]
    assert "client_secret" not in form
    digest = hashlib.sha256(form["code_verifier"].encode("ascii")).digest()
    assert base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii") == params["code_challenge"]

    session = runtime.session(outcome.token)
    assert session is not None and session.identity.email == OWNER
    actor = runtime.actor_for(outcome.token)
    assert actor is not None and (actor.id, actor.method, actor.verified) == (OWNER, "google", True)
    assert [e.kind for e in events] == ["sign_in"]


def test_a_state_is_used_once_and_unknown_states_record_nothing():
    events: list = []
    runtime, fake, _ = sign_in_runtime(events=events)
    state, nonce, _ = _start_google(runtime)
    fake.google(nonce)
    assert finish(runtime, state, "code-1", None).status == "signed_in"
    again = finish(runtime, state, "code-1", None)
    assert again.status == "failed" and "already used" in again.message
    assert finish(runtime, "made-up", "x", None).status == "failed"
    assert finish(runtime, None, "x", None).status == "failed"
    assert [e.kind for e in events] == ["sign_in"]  # nothing recorded for callbacks SED did not start


def test_a_started_sign_in_expires():
    runtime, fake, clock = sign_in_runtime()
    state, nonce, _ = _start_google(runtime)
    fake.google(nonce)
    clock.advance(FLOW_SECONDS + 1)
    assert finish(runtime, state, "code-1", None).status == "failed"
    assert fake.forms("token") == []


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"nonce": "another-nonce"}, "does not belong"),
        ({"aud": "someone-else"}, "different application"),
        ({"iss": "https://accounts.example"}, "unexpected issuer"),
        ({"exp": 1}, "expired"),
    ],
)
def test_id_token_checks_refuse_a_mismatch(overrides, fragment):
    events: list = []
    runtime, fake, _ = sign_in_runtime(events=events)
    state, nonce, _ = _start_google(runtime)
    fake.google(nonce, **overrides)
    outcome = finish(runtime, state, "code-1", None)
    assert outcome.status == "failed" and fragment in outcome.message and outcome.token is None
    assert [e.kind for e in events] == ["sign_in_failed"]


def test_not_on_the_list_is_refused_and_recorded():
    events: list = []
    runtime, fake, _ = sign_in_runtime(events=events)
    state, nonce, _ = _start_google(runtime)
    fake.google(nonce, email="stranger@example.org")
    outcome = finish(runtime, state, "code-1", None)
    assert outcome.status == "refused" and outcome.token is None
    assert "stranger@example.org is not on the list" in outcome.message
    assert [(e.kind, e.identity.shown()) for e in events] == [("sign_in_refused", "stranger@example.org")]


def test_an_unverified_google_address_does_not_count():
    runtime, fake, _ = sign_in_runtime()
    state, nonce, _ = _start_google(runtime)
    fake.google(nonce, email_verified=False)
    outcome = finish(runtime, state, "code-1", None)
    assert outcome.status == "refused" and "did not confirm an e-mail address" in outcome.message


def test_cancelled_at_the_provider():
    events: list = []
    runtime, _, _ = sign_in_runtime(events=events)
    state, _, _ = _start_google(runtime)
    outcome = finish(runtime, state, None, "access_denied")
    assert (outcome.status, outcome.message) == ("cancelled", "Sign-in was cancelled.")
    assert events[0].kind == "sign_in_failed" and events[0].detail == {"error": "access_denied"}


def test_a_refused_code_exchange_repeats_only_the_error_code():
    runtime, fake, _ = sign_in_runtime()
    state, _, _ = _start_google(runtime)
    fake.token_status, fake.token_error = 400, {"error": "invalid_grant", "error_description": "<b>ticket text</b>"}
    outcome = finish(runtime, state, "code-1", None)
    assert outcome.status == "failed" and "ticket text" not in outcome.message and "Google" in outcome.message


def test_microsoft_uses_the_tenant_and_the_localhost_redirect():
    runtime, fake, _ = sign_in_runtime()
    params = start(runtime, "microsoft")
    assert params["redirect_uri"] == "http://localhost:8123/auth/callback"
    assert params["response_mode"] == "query"
    fake.microsoft(params["nonce"])
    outcome = finish(runtime, params["state"], "code-1", None)
    assert outcome.status == "signed_in"
    (form,) = fake.forms(f"login.microsoftonline.com/{TENANT}/oauth2/v2.0/token")
    assert form["scope"] == "openid email profile" and "client_secret" not in form


def test_a_token_issued_for_another_organisation_is_not_accepted():
    runtime, fake, _ = sign_in_runtime()
    params = start(runtime, "microsoft")
    fake.microsoft(params["nonce"], tid=OTHER_TENANT)
    outcome = finish(runtime, params["state"], "code-1", None)
    assert outcome.status == "failed" and "unexpected issuer" in outcome.message and outcome.token is None


def test_a_listed_organisation_lets_everyone_in_it_sign_in():
    runtime, fake, _ = sign_in_runtime(auth_settings(emails=[], microsoft_tenants=[TENANT]))
    params = start(runtime, "microsoft")
    fake.microsoft(params["nonce"], preferred_username="colleague@example.com")
    outcome = finish(runtime, params["state"], "code-1", None)
    assert outcome.status == "signed_in" and outcome.identity.email == "colleague@example.com"


def test_github_device_sign_in_polls_no_faster_than_allowed():
    events: list = []
    runtime, fake, clock = sign_in_runtime(events=events)
    flow_id, user_code, uri, _, interval = runtime.start_device("github")
    assert (user_code, uri, interval) == ("WDJB-MJHT", "https://github.com/login/device", 5)
    (form,) = fake.forms("login/device/code")
    assert form == {"client_id": "test-github-client", "scope": "user:email"}

    assert runtime.poll_device(flow_id).status == "pending"
    assert fake.forms("oauth/access_token") == []  # too early: GitHub is not asked
    clock.advance(5)
    fake.github_polls = [{"error": "slow_down", "interval": 10}, {"access_token": "gh-token"}]
    assert runtime.poll_device(flow_id).status == "pending"
    clock.advance(5)
    assert runtime.poll_device(flow_id).status == "pending"  # slowed down to 10 seconds
    assert len(fake.forms("oauth/access_token")) == 1
    clock.advance(5)
    outcome = runtime.poll_device(flow_id)
    assert outcome.status == "signed_in" and outcome.identity.email == OWNER
    assert "client_secret" not in fake.forms("oauth/access_token")[-1]
    assert [e.kind for e in events] == ["sign_in"]
    assert runtime.poll_device(flow_id).status == "expired"  # the flow is gone once used


def test_github_only_verified_addresses_count():
    runtime, fake, clock = sign_in_runtime()
    fake.github_emails = [
        {"email": OWNER, "verified": False, "primary": True},
        {"email": "other@example.org", "verified": True, "primary": False},
    ]
    fake.github_polls = [{"access_token": "gh-token"}]
    flow_id, *_ = runtime.start_device("github")
    clock.advance(5)
    outcome = runtime.poll_device(flow_id)
    assert outcome.status == "refused" and "other@example.org" in outcome.message


def test_github_denied_and_expired():
    runtime, fake, clock = sign_in_runtime()
    fake.github_polls = [{"error": "access_denied"}]
    flow_id, *_ = runtime.start_device("github")
    clock.advance(5)
    assert runtime.poll_device(flow_id).status == "cancelled"
    flow_id, *_ = runtime.start_device("github")
    fake.github_polls = [{"error": "expired_token"}]
    clock.advance(5)
    assert runtime.poll_device(flow_id).status == "expired"


def test_one_person_at_a_time():
    events: list = []
    runtime, fake, _ = sign_in_runtime(events=events)
    state, nonce, _ = _start_google(runtime)
    fake.google(nonce)
    first = finish(runtime, state, "code-1", None)
    state, nonce, _ = _start_google(runtime)
    fake.google(nonce)
    second = finish(runtime, state, "code-2", None)
    assert runtime.session(first.token) is None and runtime.session(second.token) is not None
    assert [e.kind for e in events] == ["sign_in", "sign_in", "session_replaced"]


def test_a_session_expires_and_sign_out_ends_it():
    events: list = []
    runtime, fake, clock = sign_in_runtime(events=events)
    state, nonce, _ = _start_google(runtime)
    fake.google(nonce)
    token = finish(runtime, state, "code-1", None).token
    clock.advance(12 * 3600 - 1)
    assert runtime.session(token) is not None
    clock.advance(2)
    assert runtime.session(token) is None and runtime.actor_for(token) is None

    state, nonce, _ = _start_google(runtime)
    fake.google(nonce)
    token = finish(runtime, state, "code-2", None).token
    assert runtime.sign_out(token) is not None and runtime.session(token) is None
    assert runtime.sign_out(token) is None
    assert [e.kind for e in events] == ["sign_in", "sign_in", "sign_out"]


def test_a_sign_in_the_audit_trail_cannot_record_does_not_happen():
    def refuse(event):
        if event.kind == "sign_in":
            raise OSError("disk full")

    runtime, fake, _ = sign_in_runtime()
    runtime.on_event = refuse
    state, nonce, _ = _start_google(runtime)
    fake.google(nonce)
    outcome = finish(runtime, state, "code-1", None)
    assert outcome.status == "failed" and outcome.token is None and "audit trail" in outcome.message


def test_other_events_never_block():
    def broken(event):
        raise OSError("disk full")

    runtime, fake, _ = sign_in_runtime()
    state, nonce, _ = _start_google(runtime)
    fake.google(nonce, email="stranger@example.org")
    runtime.on_event = broken
    assert finish(runtime, state, "code-1", None).status == "refused"


def test_an_answer_without_the_starting_browsers_binding_is_refused_before_redeeming():
    events: list = []
    runtime, fake, _ = sign_in_runtime(events=events)
    state, nonce, _ = _start_google(runtime)
    fake.google(nonce)
    for other in (None, "someone-elses-cookie"):
        outcome = finish(runtime, state, "code-1", None, binding=other)
        assert outcome.status == "failed" and "another browser" in outcome.message and outcome.token is None
        state, nonce, _ = _start_google(runtime)
        fake.google(nonce)
    assert fake.forms("token") == []  # the code was never redeemed
    assert [e.detail.get("error") for e in events] == ["other_browser", "other_browser"]


def test_an_answer_on_the_other_loopback_name_waits_for_the_starting_browser():
    runtime, fake, _clock = sign_in_runtime()
    params = start(runtime, "microsoft")  # redirect registered on localhost; the dashboard runs on 127.0.0.1
    fake.microsoft(params["nonce"])
    parked = finish(runtime, params["state"], "code-1", None, here="http://localhost:8123", binding=None)
    assert (parked.status, parked.origin) == ("handoff", ORIGIN) and parked.handoff
    assert fake.forms("token") == []  # parked, not redeemed
    assert runtime.finish_handoff(parked.handoff, None).status == "failed"  # a browser without the binding
    params = start(runtime, "microsoft")
    fake.microsoft(params["nonce"])
    parked = finish(runtime, params["state"], "code-2", None, here="http://localhost:8123", binding=None)
    outcome = runtime.finish_handoff(parked.handoff, BINDINGS[params["state"]])
    assert outcome.status == "signed_in" and outcome.token
    assert runtime.finish_handoff(parked.handoff, BINDINGS[params["state"]]).status == "expired"  # single use


def test_a_parked_answer_expires():
    runtime, _fake, clock = sign_in_runtime()
    params = start(runtime, "microsoft")
    parked = finish(runtime, params["state"], "code-1", None, here="http://localhost:8123", binding=None)
    clock.advance(61)
    assert runtime.finish_handoff(parked.handoff, BINDINGS[params["state"]]).status == "expired"
    assert runtime.finish_handoff(None, None).status == "expired"


def test_guests_of_a_listed_organisation_need_their_own_address():
    runtime, fake, _ = sign_in_runtime(auth_settings(emails=[], microsoft_tenants=[TENANT]))
    params = start(runtime, "microsoft")
    guest_idp = "https://sts.windows.net/00000000-0000-4000-8000-0000000000cc/"
    fake.microsoft(params["nonce"], preferred_username="visitor@example.org", idp=guest_idp)
    outcome = finish(runtime, params["state"], "code-1", None)
    assert outcome.status == "refused" and outcome.identity.guest

    runtime, fake, _ = sign_in_runtime(auth_settings(emails=["visitor@example.org"], microsoft_tenants=[TENANT]))
    params = start(runtime, "microsoft")
    fake.microsoft(params["nonce"], preferred_username="visitor@example.org", idp=guest_idp)
    assert finish(runtime, params["state"], "code-1", None).status == "signed_in"


def test_switched_off_providers_and_developer_mode_refuse_to_start():
    runtime, _, _ = sign_in_runtime(auth_settings())
    with pytest.raises(SignInFailedError, match="signs in another way"):
        runtime.start_redirect("github", ORIGIN)
    with pytest.raises(SignInFailedError, match="signs in another way"):
        runtime.start_device("google")
    developer = AuthRuntime(auth_settings(), "developer")
    with pytest.raises(SignInFailedError, match="developer mode"):
        developer.start_redirect("google", ORIGIN)
    assert developer.providers() == []
    actor = developer.actor_for(None)
    assert actor is not None and (actor.method, actor.verified) == ("developer_mode", False)
    assert actor.id.startswith("windows:")


def test_an_unknown_client_id_is_explained():
    runtime, fake, _ = sign_in_runtime()

    def not_found(url, form, headers=None):
        return 404, {"error": "Not Found"}

    fake.post_form = not_found
    with pytest.raises(SignInFailedError, match="does not recognise SED's client ID"):
        runtime.start_device("github")
