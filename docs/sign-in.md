# Signing in to SED

SED asks who you are before it shows the dashboard, so that its audit trail can say who did what: who signed in, who
downloaded data, who pulled it from ServiceNow, and later who changed it. (The audit trail itself is the next step of
this work; until it lands, sign-in controls who gets in but nothing is recorded yet.)

**What it protects, and what it does not.** Signing in protects the dashboard. It does not protect the data file:
anyone with your Windows login can still open the data folder directly. If the worry is the data at rest, the answer
is disk encryption (BitLocker), not a sign-in screen.

## How it works

- **The real profile asks for a sign-in; synthetic profiles do not.** A synthetic profile runs in *developer mode*:
  nobody signs in, and every screen says so.
- **Microsoft, Google or GitHub.** You choose on the sign-in screen. Your password goes only to that provider's own
  page; SED never sees it and has no password field anywhere.
- **Only listed people get in.** Signing in with *a* Google or GitHub account proves who you are; the list in SED's
  settings decides whether you may use it. The list holds e-mail addresses, or a whole Microsoft organisation.
- **One person at a time.** A new sign-in ends any other. A sign-in lasts 12 hours, or until SED is stopped.
- **Nothing else leaves the laptop.** Signing in is the one exception, and it talks only to the provider you chose.
  No client secret is stored anywhere, so there is none to leak.

## Setting up sign-in

Each provider needs a one-off registration that gives SED a *client ID* (not a secret). Google and GitHub you can
register yourself; Microsoft needs your IT department. Set up at least one provider and add yourself to the list,
then restart `sed serve`.

### Google (you can do this yourself)

1. Open the Google Cloud console and create a project, for example "SED sign-in".
2. Under *Google Auth Platform*, set up the consent screen: user type **External**, publishing status **Testing**, and
   add your own Google address as a test user.
3. Under *Clients*, create a client of type **Desktop app**. Copy its **client ID**. Ignore the client secret: SED does
   not use it.
4. Tell SED:

   ```bash
   uv run sed auth provider google --client-id <the client ID> --profile real
   ```

### GitHub (you can do this yourself)

1. On GitHub: *Settings → Developer settings → OAuth Apps → New OAuth App*.
2. Application name "SED", homepage `http://127.0.0.1`, callback URL `http://127.0.0.1/auth/callback` (the form needs
   one; SED does not use it).
3. After registering, tick **Enable Device Flow** and save. Copy the **Client ID**. Do not create a client secret.
4. Tell SED:

   ```bash
   uv run sed auth provider github --client-id <the Client ID> --profile real
   ```

GitHub signs in with a short code: SED shows it, you enter it on GitHub's page, and SED continues on its own.

### Microsoft (ask your IT department)

Send them this:

> Please create an app registration in our Entra ID for a desktop tool called SED:
> - supported account types: accounts in this organisational directory only (single tenant);
> - platform **Mobile and desktop applications**, redirect URI `http://localhost/auth/callback`;
> - no client secret or certificate, and no API permissions beyond the defaults (sign-in and read the user's basic
>   profile: `openid`, `profile`, `email`).
>
> Please send me the **Application (client) ID** and the **Directory (tenant) ID**.

Then tell SED:

```bash
uv run sed auth provider microsoft --client-id <application ID> --tenant-id <directory ID> --profile real
```

Microsoft ignores the port of a `localhost` redirect, so SED can run on any port. If IT can register
`http://127.0.0.1/auth/callback` instead (in the app's manifest), that is slightly more robust, because some browsers
try the IPv6 address first for `localhost`; then add `--redirect-host 127.0.0.1` to the command above.

### Who may sign in

```bash
uv run sed auth allow you@example.com --profile real        # one person, by the address the provider confirms
uv run sed auth allow <directory ID> --profile real         # everyone in your Microsoft organisation
uv run sed auth disallow you@example.com --profile real
uv run sed auth show --profile real                         # how sign-in is set up (add --list for the names)
```

Allowing a whole Microsoft organisation lets in its own people; guests it has invited from elsewhere get in only when
their own address is on the list. Changes take effect the next time `sed serve` starts. In Claude Code these commands
always ask for your permission.

### Trying it on synthetic data first

`uv run sed auth mode sign_in --profile synthetic` makes the synthetic dashboard ask for a sign-in too, with the same
providers and list you set for that profile. `uv run sed auth mode auto --profile synthetic` goes back.

## Developer mode

`uv run sed serve --profile real --developer-mode` skips sign-in for that one launch, for working offline or testing.
Every screen shows a *Developer mode* label, and decisions are recorded under your Windows account name as
`windows:<name>`, which marks it as not proven. It can never be switched on in the settings for real data, so it
cannot become the default; a synthetic profile name pointed at a real data folder still asks for a sign-in.

## When something goes wrong

| The screen says | What to do |
|---|---|
| Sign-in is not set up yet | Set up a provider and add yourself to the list (above), then restart `sed serve`. |
| … does not recognise SED's client ID | Check the client ID (`sed auth show`); for GitHub, check the app still exists. |
| … asked for a client secret | The Google client must be of type *Desktop app*. |
| Device sign-in is off for SED's GitHub app | Tick *Enable Device Flow* in the GitHub app's settings. |
| This Microsoft account belongs to a different organisation | Sign in with your work account, or check the tenant ID. |
| … is not on the list of people allowed to use SED | Add the address with `sed auth allow` if that person should get in. |
| … finished in another browser than the one that started it | Start again, and finish in the same browser window. |
| Could not reach … | Check the network. SED uses a proxy set in Windows' proxy settings (or `HTTPS_PROXY`), but not an automatic proxy script; ask IT for the proxy address if your company uses one. |

A Microsoft error page saying the reply URL does not match means the app registration lacks the redirect
`http://localhost/auth/callback` under *Mobile and desktop applications*.

## For developers

- **Flows.** Microsoft and Google: authorization code with PKCE (S256) and a loopback redirect (RFC 8252), as a public
  client, `state` and `nonce` single-use, ten minutes to finish. The redirect port is the port SED listens on, never
  the Host header. The start sets a binding cookie (`sed_signin_<port>`, HttpOnly, SameSite=Lax, path `/auth`) and the
  code is redeemed only for the browser that holds it, so an answer copied or caught elsewhere never becomes a session.
  GitHub: device authorization grant (RFC 8628), because its browser flow requires the client secret on every token
  exchange; SED polls no faster than GitHub allows.
- **ID tokens** come straight from the token endpoint over TLS, so their issuer is vouched for by TLS (OpenID Connect
  Core 3.1.3.7); SED checks issuer, audience, expiry and nonce, not the signature. GitHub identity comes from `/user`
  and `/user/emails` (verified addresses only); the GitHub token is discarded at once. SED never stores a provider token.
  Microsoft guests (an `idp` claim other than the issuer) are not covered by an organisation-wide allow.
- **Session.** In memory in `sed serve`, one at a time, `session_hours` long. The cookie `sed_session_<port>` is
  HttpOnly and SameSite=Strict; the token is kept only as its SHA-256. When the provider's registered redirect uses the
  other loopback name (localhost versus 127.0.0.1), the answer is parked unredeemed for one minute and collected at
  `/auth/finish` by the browser that started, on its own host.
- **Recorded names.** Decisions record the signed-in address, or `windows:<account>` when nobody proved who it was
  (developer mode, the command line), so an unproven name never reads like a signed-in one. The data class that decides
  whether sign-in is required comes from the database when it says `real`, not only from the profile's name.
- **Every request** carries the actor on `request.state.actor`, or gets 401 `unauthenticated`. Deny by default: open
  are only the dashboard shell (`/`, `/assets/*`, top-level files), `/auth/callback`, `/auth/finish`, `/api/health`,
  `/api/branding*` and `/api/auth/*`. Writes still need the per-launch `X-SED-Token`.
- **Code.** `src/sed/auth/` (settings, providers, identity, runtime, middleware, actor, cli), `src/sed/api/routes_auth.py`,
  `web/src/core/auth/`. Settings: `config/auth.yaml` overlaid by `DATA_DIR\config\auth.yaml`. Tests use a fake provider
  (`tests/fixtures/auth.py`); CI makes no network calls.
- **Audit.** The runtime reports every sign-in, refusal, failure, sign-out and replaced session as an event to
  `on_event`, and does not complete a sign-in whose event was refused. Nothing receives these events yet: the audit
  trail that records them is the next phase (`docs/playbooks/sign-in-and-audit.md`).
- **Module keys** `auth`, `health` and `branding` are reserved (`sed.modules.CORE_API_KEYS`), so no module router can
  land under a public prefix.
