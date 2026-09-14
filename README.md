# amkit — App Owner Toolkit

A personal toolkit for an application owner. It:

1. **Imports** ITSM (incidents, requests, changes, problems, SLAs, CMDB), Jira, Confluence and Excel/SharePoint
   exports into one local SQLite database.
2. **Computes exact metrics:** SLA, MTTR, backlog, costs, renewals and license use.
3. **Runs AI analysis in Claude Code:** ticket triage, recurring issues, risks and report drafts. You approve the
   results before they reach a report.
4. **Builds reports:** weekly, monthly, quarterly and vendor reports as PowerPoint and Excel, plus a local dashboard.

> All data in this repository is synthetic. Real exports and configuration live in `DATA_DIR`
> (`%LOCALAPPDATA%\amkit\<profile>`), outside git.

## Setup

**Windows (PowerShell):**

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

**Manual setup:**

```bash
uv sync
uv run amkit init --profile synthetic --new-salt   # back up the salt file it creates
uv run pre-commit install
uv run amkit doctor
```

**Behind a corporate proxy:**
- Set `UV_DEFAULT_INDEX` (your package mirror) and `UV_SYSTEM_CERTS=1` for uv.
- Set `NODE_EXTRA_CA_CERTS` for npm.

## Profiles

| Profile | Purpose |
|---|---|
| `synthetic` | Development default; generated data with planted patterns |
| `real` | Work laptop only; real exports; record the AI approval with `--ai-approval-note` |
| `eval-<seed>` | Fresh-seed AI evaluations |

## Checks

```bash
uv run python scripts/ci.py
```

This runs ruff, pytest, the confidentiality guard, detect-secrets, and (when present) the workflow scripts and web build.

## Status

| Milestone | Scope | Status |
|---|---|---|
| M0 | Skeleton & guardrails | in progress |
| M1 | Synthetic data, import, metrics, weekly Excel | planned |
| M2 | AI walking skeleton, all decks, dashboard core | planned |
| M3 | Reality check on the work laptop | planned |
| M4 | Full AI analysis & review | planned |
| M5 | AI-drafted reports | planned |
