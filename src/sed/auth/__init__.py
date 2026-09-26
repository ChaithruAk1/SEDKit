"""Sign-in for the dashboard (docs/sign-in.md).

Sign-in exists so the audit trail can say who did something. It protects the dashboard, not the data file: anyone with
the Windows login can still open the data folder directly.

* `settings`: config/auth.yaml (every provider off) overlaid by DATA_DIR\\config\\auth.yaml, and the mode.
* `providers`: Microsoft and Google (authorization code with PKCE, loopback redirect) and GitHub (device code). No
  client secret is stored anywhere.
* `identity`: who the provider says signed in, and whether the allowlist lets them in.
* `runtime`: pending sign-ins and the one live session, in memory (a restart of `sed serve` signs everyone out).
* `middleware`: every /api request carries the signed-in person, or is refused with 401.
* `actor`: who is asking, as the audit trail records it.
"""
