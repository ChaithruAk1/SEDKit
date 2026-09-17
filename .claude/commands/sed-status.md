---
description: Where the project stands - data, review queue, AI runs, reports and repo state
argument-hint: "[profile, default synthetic]"
allowed-tools: Bash(uv run sed:*), Bash(git status:*), Bash(git log:*), Read, Grep, Glob
---

Report the current state of SED for profile `$1` (default `synthetic`). Read only; change nothing.

Run these and summarise them in plain words:

```bash
uv run sed doctor --profile $1 --json
uv run sed db info --profile $1 --json
uv run sed sources --profile $1 --json
uv run sed review list --profile $1 --json
uv run sed ai runs --profile $1 --json
uv run sed report list --profile $1 --json
```

Then `git status --short` and `git log --oneline -5`.

Say in a short list: which profile and data class, how fresh the data is per source, what is waiting for review,
the last AI runs and their status, the reports that exist, and anything uncommitted or unpushed. Finish with the one
thing that most deserves attention next. If `doctor` reports a problem, lead with that.
