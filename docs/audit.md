# The audit trail

SED keeps a record of who did what: every sign-in, every download, every pull from ServiceNow and the other tools,
every import, every time data is cleared or a backup restored, every AI run, and every change to who may sign in.

## What is recorded

| Action | When |
|---|---|
| Sign-in, sign-out | Every sign-in, refused sign-in and failed sign-in, and every sign-out (including a session ended because someone else signed in). |
| SED started, stopped | Each launch of the dashboard, and whether it asks for sign-in or runs in developer mode. |
| Download | A ticket workbook or a report file handed to the browser: how many tickets, which filters and columns, and a fingerprint of the file. |
| Report built | A report written to the data folder (dashboard or command line). |
| Export | Approved delivery drafts written as files (`sed delivery export`). |
| Pull | Data pulled from ServiceNow, Jira, Confluence, SharePoint or SAP: rows per source. |
| Import | An export file uploaded or imported: files and rows. |
| Data cleared | The data one source imported deleted, or old export files deleted from the inbox. |
| Backup restored | The database replaced with an older copy. |
| Data folder moved | The whole data folder copied to a new place (`sed data move`). |
| AI run | Ticket text prepared for Claude to analyse (how many items), or an export's column profile prepared for Claude to map (`sed mappings draft`). |
| Sign-in settings changed | A provider switched on or off, or a person or organisation added to or removed from the list, with the value before and after. |

Not recorded, by decision: reading (searches, opening a ticket, looking at a page), and review decisions, which
already record who decided inside SED. Commands that only print a few sample rows in the terminal
(`sed mappings try`, `sed import --dry-run`) are reading too, even when Claude runs them.

Entries hold names, counts, filters and fingerprints, never ticket text, and never the text of an error (which can
quote a file's rows or a folder path): a failure is recorded in fixed words, such as "the input was refused".

## Reading it

Click **Audit** in the sidebar. The page lists every entry, newest first, with a note at the top saying whether the
trail is intact. Filter by person, action, outcome, days or words; open an entry for its details and, for a change,
each value before and after, or to see how an action started and ended. **Download as a workbook** saves what the
filters show, with each entry's fingerprints (a copy kept elsewhere can later show whether the trail was rewritten).
The download is itself recorded; reading the page is not.

## What each entry says

- **When** (UTC) and **who**: the signed-in address, or `windows:<name>` when nobody proved who it was (developer
  mode and the command line, including the AI helpers and scheduled runs).
- **How they were identified** (Microsoft, Google, GitHub, developer mode or the Windows account) and **where**
  (dashboard or command line). A command-line entry also notes who was signed in to the dashboard at that moment.
- **What**, in one plain sentence, with the details behind it, and the **outcome**: started, done, failed or refused.
  Actions that take time (pulls, imports, clears, restores, report builds, AI runs) have two entries, one when they
  start and one when they end, tied together. A start without an end means SED stopped half-way through.

## How it is protected, and how not

- **Its own file** in the data folder (`audit\audit.db`), apart from SED's data: restoring a backup or clearing data
  never touches it, and moving the data folder carries it along.
- **Kept forever.** SED has no way to change or delete an entry, and the database itself refuses to.
- **Edits by hand show.** Every entry carries a fingerprint of the one before it, so an entry changed or removed by
  hand breaks the chain. `sed audit verify` and `sed doctor` check it.
- **No record, no action.** When SED cannot write an entry, it does not act: the download is refused, the pull or
  import does not start, the sign-in does not complete.
- **The limit.** The chain is not a seal: it has no key, so someone who knows how SED builds it can rewrite the whole
  file, and anyone with your Windows login can delete or replace it, as with the data file itself. It catches casual
  and accidental edits; it cannot stop a determined one. Disk encryption and backups of the data folder protect the
  files themselves, and a backup keeps a copy of the trail that a later rewrite would not match.

## For developers

- **Page.** `GET /api/audit` (filters `person`, `action`, `outcome`, `since`, `until`, `q`, `correlation`; paged, newest
  first, with the people, the actions and the chain check) and `GET /api/audit-export.xlsx` (the same filters, recorded
  as a download) in `sed/api/routes_audit.py`, reading through `sed.audit.query`; the page is
  `web/src/core/pages/AuditPage.tsx`.
- **Code.** `sed.audit.store` (the append-only SQLite store, the hash chain, `verify`), `sed.audit.record` (`record`,
  `start` and `Attempt`, `AuditUnavailable`, the sign-in recorder and the dashboard-session note) and
  `sed.audit.actions` (each action's wording, shared by the dashboard and the command line). `sed audit verify`.
- **Pattern.** Record before acting, so an action the trail cannot record never happens:

  ```python
  with actions.start_pull(paths, who, connector, source=None, full=False, then_import=True) as attempt:
      result = pull_and_import(paths, connector)
      actions.pull_done(attempt, result["pull"], result["import"])
  ```

  An exception inside the block records the failure (SED's own message, or the error type, never the error text).
  Something that happens at once uses `record(...)` before anything is handed out.
- **Who.** API routes use `deps.actor(request)` (set by the sign-in middleware); the command line uses
  `sed.auth.actor.command_line_actor()`.
- **Why a separate file.** `sed db restore` replaces `sed.db` wholesale and backups are pruned, and GET routes (the
  downloads) must never write `sed.db`. The store has its own connections: `BEGIN IMMEDIATE` for the chain, and
  read-only connections that never create the file.
- **Write-back** (changing ServiceNow or Jira) will record the attempt with `changes` (each field before and after)
  before anything is sent, then the outcome.
- **Agents never open `audit\`** (CLAUDE.md hard rule; the guard hook and `sed init`'s deny rules block it).
