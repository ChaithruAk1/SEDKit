# SED

SED is a personal toolkit for an application owner, built as a platform of modules (ops is module #1; see
`docs/modules.md`). It:

1. **Imports** ITSM (incidents, requests, changes, problems, SLAs, CMDB), Jira, Confluence and Excel/SharePoint
   exports into one local SQLite database.
2. **Computes exact metrics:** SLA, MTTR, backlog, costs, renewals and license use.
3. **Runs AI analysis in Claude Code:** ticket triage, recurring issues, risks and report drafts. You approve the
   results before they reach a report.
4. **Builds reports:** weekly, monthly, quarterly and vendor reports as PowerPoint and Excel, plus a local dashboard.

> All data in this repository is synthetic. Real exports and configuration live in `DATA_DIR`
> (`%LOCALAPPDATA%\sed\<profile>`), outside git.

## Setup

**Windows (PowerShell):**

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

**Manual setup:**

```bash
uv sync
uv run sed init --profile synthetic --new-salt   # back up the salt file it creates
uv run pre-commit install
uv run sed doctor
```

**Behind a corporate proxy:**
- Set `UV_DEFAULT_INDEX` (your package mirror) and `UV_SYSTEM_CERTS=1` for uv.
- Set `NODE_EXTRA_CA_CERTS` for npm.

> **Name clash:** the CLI is called `sed`, like the Unix stream editor. Run it as `uv run sed ...` and don't activate
> the virtualenv in Git Bash or WSL shells, where `.venv\Scripts\sed.exe` would shadow the system `sed`.

## Profiles

| Profile | Purpose |
|---|---|
| `synthetic` | Development default; generated data with planted patterns |
| `real` | Real exports on an approved machine; record the AI approval with `--ai-approval-note` |
| `eval-<seed>` | Fresh-seed AI evaluations |

## Checks

```bash
uv run python scripts/ci.py
```

This runs ruff, pytest, the confidentiality guard, detect-secrets, and (when present) the workflow scripts and web build.

## First run (synthetic data)

```bash
uv run sed synth --profile synthetic                  # export files + ground truth into DATA_DIR
uv run sed import --inbox --profile synthetic         # ~270k rows in about a minute
uv run sed analytics refresh --profile synthetic      # system-detected risks
uv run sed report build weekly --period 2026-W35 --ai none --profile synthetic   # xlsx, md and pptx
```

Outputs land in `DATA_DIR\out\<period>\`. Useful follow-ups: `sed alias list --unmapped`, `sed attention`,
`sed metrics show <name>`, `sed mappings check FILE`, `sed import FILE --dry-run`.

## Reports and decks

| Report | Period | Example |
|---|---|---|
| weekly | ISO week | `uv run sed report build weekly --period 2026-W35 --profile synthetic` |
| monthly | month | `uv run sed report build monthly --period 2026-08 --profile synthetic` |
| quarterly | quarter | `uv run sed report build quarterly --period 2026-Q3 --profile synthetic` |
| vendor | quarter or month | `uv run sed report build vendor --period 2026-Q3 --vendor V001 --profile synthetic` |

- `--ai approved|none|draft` controls AI content; `none` builds deterministic, shareable files.
- What each report shows (KPIs, sheets, colour rules, slides) is YAML in `config/ops/reports/`; override it locally in
  `DATA_DIR\config\ops\reports\`.
- Decks use a template map: `templates/pptx/neutral.map.yaml` by default. A corporate template and map go in
  `DATA_DIR\config\templates\` (never in git); `uv run sed report template-inspect FILE.pptx` lists its layouts and
  `uv run sed report template-proof --map corporate` renders every slide kind for sign-off.

## Dashboard

```bash
npm --prefix web ci && npm --prefix web run build     # once, or after web changes
uv run sed serve --profile synthetic                  # http://127.0.0.1:8000, opens the browser
```

The server binds 127.0.0.1 only; write requests need the per-launch token injected into the page. Development with
hot reload: `powershell -ExecutionPolicy Bypass -File scripts\dev.ps1`.

## AI analysis (Claude Code)

- Triage runs through the `sed-triage-batch` skill (small runs in-session) or the `sed-analyze` workflow (larger runs):
  start-run writes scrubbed packets, one agent per packet labels them, `sed ai ingest` validates, `sed ai finish-run`
  draws a review sample.
- A human approves each run from its random sample: `uv run sed review sample RUN --template verdicts.json`, fill in
  verdicts, `uv run sed review verdicts RUN --file verdicts.json`, then `uv run sed review approve-run RUN`.
- Approved labels appear in reports with their sample accuracy and confidence interval. See
  `docs/ai/walking-skeleton-runbook.md`.

## Status

| Milestone | Scope | Status |
|---|---|---|
| M0 | Skeleton & guardrails | done |
| M1 | Synthetic data, import, metrics, weekly Excel | done |
| M2 | Modular platform, AI walking skeleton, all decks, API and dashboard core | done (final review in progress) |
| M3 | Reality check with real exports | planned |
| M4 | Full AI analysis & review | planned |
| M5 | AI-drafted reports | planned |
| M7 | AI-native SDLC: delivery-management module and app factory | to be planned |
