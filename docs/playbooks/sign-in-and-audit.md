# Sign-in and the audit trail

A brief for a session building this on its own. Read it before writing anything, and ask the owner the open
questions in §6 first: two of them change the design.

## 1. What is actually being asked for

The request was for sign-in with Google, GitHub or company single sign-on, and its purpose is to know who is doing
what: who accesses the information, who downloads it or pulls it from ServiceNow, and, once SED can change
information rather than only read it, who changes what.

So the requirement is an **audit trail**. Sign-in is a prerequisite, not the goal: an audit entry whose actor is a
Windows username nobody had to prove is worth very little. Build identity because the log needs it, and judge every
choice by whether the log can answer "who did this".

Offer Microsoft (single sign-on), Google and GitHub, and let config switch any of them off.

## 2. What exists today (checked 2026-09-25, do not assume it still holds)

| | |
|---|---|
| Review decisions | `review_decision.reviewer`, `finding.reviewer` — a name is recorded |
| Pulls from a connector | appended to `DATA_DIR\logs\pulls.jsonl` — **no actor** |
| Imports | `import_batch` records file, mapping, rows, time — **no actor** |
| Exports and downloads | **nothing is recorded** |
| Reads (a search, opening a ticket) | nothing |
| The actor's name | `sed.bootstrap.reviewer_name()` — the `USERNAME` environment variable, unproven |

There is no audit table and no audit page. Exports are the gap that matters most: `GET /api/ops/tickets-export.xlsx`
writes a workbook of real tickets and leaves no trace.

## 3. Constraints that are already settled

- **Desktop, single user.** SED ships as a Windows installer for one person; a hosted multi-user version is out of
  scope. Do not build accounts, roles or tenancy. One signed-in person at a time is the whole model.
- **Nothing leaves the laptop.** Signing in is the one deliberate exception and it reaches only the identity
  provider. No telemetry, no log shipping, no other outbound call.
- **The data-folder boundary.** Identity config, the allowlist and the audit log all live under `DATA_DIR`, never in
  the repo. Client IDs are not secrets in this flow, but the allowlist names real people, so it is DATA_DIR-only.
- **Read the repo's hard rules first** (`CLAUDE.md`). They are not negotiable, and the guard hook enforces some of
  them.

## 4. Design already agreed with the owner

- **Authorization code flow with PKCE and a loopback redirect** — the standard for a desktop application. No client
  secret is stored on disk, so there is none to leak. Redirect `http://localhost:<api port>/auth/callback`.
- **SED never sees a password.** There is no password field anywhere in it; the provider's own page collects it.
- **A developer mode that skips sign-in**, clearly marked on screen. Without it SED cannot be used offline and cannot
  be tested. It must be obvious, never the default on a real profile.
- **An allowlist.** Signing in with *a* Google account proves an identity, not an authorisation. Config names the
  addresses, or the Microsoft tenant, that may in. Without it the buttons are decoration.
- **Each provider switchable** in config, so an organisation can keep Microsoft and drop the rest.

## 5. What the owner was told, and must stay true

> This protects the dashboard. It does not protect the data file — anyone with the Windows login can still open it
> directly. If the real concern is data at rest, the answer is disk encryption, not a sign-in screen.

Do not let the work drift into implying more protection than it gives.

## 6. Ask the owner these before designing the log

1. **How much detail?** Actions only (sign-in, export, pull, import, clear, and later every change — tens of entries
   a week, answers "who took data out"), or actions plus reads (every search and every ticket opened — thousands a
   week, answers "who looked at this ticket"). This changes the volume by two orders of magnitude and changes
   whether the log needs its own retention.
2. **Is write-back real or hypothetical?** The owner said SED will eventually modify information rather than only
   read it. If that is real, design the log to carry before-and-after from the start: retrofitting that onto a
   system already writing leaves gaps that can never be filled.
3. **Retention.** How long must entries be kept, and may they ever be deleted? An audit log that can be pruned by
   the person it audits is weak; one that grows forever needs a plan.

## 7. Suggested shape

Three phases, each committable on its own.

**Phase 1 — identity.** Provider config in `DATA_DIR`, the PKCE flow, the callback, a local session, the allowlist,
developer mode, and the sign-in screen. Every API request then knows who is asking.

**Phase 2 — the log.** One append-only table, one helper every action calls, and the actions wired to it: sign-in,
sign-out, refusal, export, pull, import, clear. Never updated, never deleted by the application.

**Phase 3 — reading it.** An Audit page: filter by person, action and date, and export it. A log nobody can read is
not a control.

Write-back, if it is real, is a fourth phase and should be designed against the log rather than beside it.

## 8. Practical notes from the session that wrote this brief

- `tests/platform/api/test_core_routes.py` pins the exact set of core GET routes, and
  `tests/platform/api/test_readonly.py` calls each one. A new endpoint fails both until it is declared there, and a
  new endpoint with a required query parameter also needs an entry in `REQUIRED_QUERY` in that folder's `conftest.py`.
  This is a deliberate tripwire; do not work around it.
- Every new API model needs a fixture in `web/src/api/fixtures/` or the web typecheck fails. That is also deliberate.
- Run the checks as `uv run python scripts/ci.py` and read its exit code. **Do not pipe it through `tail`** — the
  pipe returns the exit code of `tail`, and a failing run then looks green. That mistake was made twice here.
- `sed serve` caches `index.html` at launch: restart it after any web rebuild, or the browser gets the old bundle.
- A browser holds the old page hard. Hard-refresh (Ctrl+Shift+R) before concluding a change did not work.
- Declare React hooks before any early return. A hook after one crashed the whole dashboard with React error #310.
- A new route under an existing one with a path parameter (`/tickets/{id}`) is shadowed by it. Use a sibling path.
- The Microsoft provider needs an Entra ID app registration (application ID, directory ID, the loopback redirect);
  Google and GitHub can be registered without it, so build and test those first. Steps: `docs/sign-in.md`.

## 9. Decisions and status (2026-09-25)

The owner's answers to §6, plus one question found in the code:

1. **Detail: actions only.** Sign-in, sign-out, refusal, export and download, pull, import, clear, and later every
   change. No reads. Added at the Phase 1 checkpoint: AI runs (ticket text prepared for Claude), restoring a backup,
   and changes to who may sign in (with before and after). Review decisions are not logged; they already record who
   decided.
2. **Write-back is real, including to ServiceNow and Jira.** Entries carry before-and-after from day one, and a change
   sent out is recorded as an attempt before it is sent, then as its outcome.
3. **Retention: forever, never deleted.** SED has no way to delete or prune an entry.
4. **Command-line actions** (the CLI, AI helpers, scheduled runs) are recorded under the Windows account name, marked
   as not proven, noting whether someone was signed in to the dashboard at the time. The command line does not sign in.

One deviation from §4: **GitHub uses its device code sign-in**, not the browser redirect, because GitHub's redirect
flow needs the client secret on every token exchange and §4 promises no stored secret. Microsoft and Google use the
redirect with PKCE as planned.

Phase 1 (identity) is built: `docs/sign-in.md`. Phase 2 (the log) and Phase 3 (the Audit page) are built:
`docs/audit.md`. The log is its own file
under `DATA_DIR` (`audit\audit.db`), not in `sed.db`, because `sed db restore` replaces `sed.db` wholesale and GET
routes must never write it; `sed data move` copies it with SQLite's backup so its newest entries travel too.
