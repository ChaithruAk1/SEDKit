---
description: The weekly loop - import this week's data, run the analysis workflow, then hand over for review
argument-hint: "<profile> [ISO week, e.g. 2026-W35]"
allowed-tools: Bash(uv run sed:*), Read, Grep, Glob
---

Run the weekly loop for profile `$1` and period `$2` up to the point where a human takes over. Never approve
anything yourself: approval is the user's.

1. **Bring in the data.** Check what is waiting: `uv run sed sources --profile $1 --json`. Import everything in the
   inbox with `uv run sed import --inbox --profile $1 --json`, then refresh the derived numbers with
   `uv run sed analytics refresh --profile $1 --json`. Report rows imported, DQ warnings and unmapped values; stop and
   ask if a DQ error appears.
2. **Analyse.** Run the saved workflow `.claude/workflows/sed-analyze.js` with
   `{profile: '$1', steps: ['triage', 'recurring', 'risks']}` (the import step is already done). Report the run ids
   and what each step produced.
3. **Hand over.** Print the review queue (`uv run sed review list --profile $1 --json`) and tell the user to review it
   at `#/review` in the dashboard, or with `/sed-review`.
4. **Stop there.** Once the user has approved, the next step is the `sed-report.js` workflow for period `$2` and then
   `uv run sed report build weekly --period $2 --ai approved --profile $1 --json`. Say this; do not run it before the
   review is done.
