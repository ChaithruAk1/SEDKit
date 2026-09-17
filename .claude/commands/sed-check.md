---
description: Run every check CI runs and fix what fails
argument-hint: "[what you changed]"
allowed-tools: Bash(uv run python scripts/ci.py), Bash(uv run ruff:*), Bash(uv run pytest:*), Bash(npm --prefix web run:*), Read, Grep, Glob, Edit
---

Run the full check suite and bring it back to green.

1. Run `uv run python scripts/ci.py`. It runs ruff (check and format), pytest without the slow marker, the
   confidentiality guard, detect-secrets, the workflow harness, generated-contract drift, and the web typecheck,
   build and checks.
2. If a step fails, read its output, find the cause and fix it. Regenerate rather than hand-edit anything generated:
   `uv run python scripts/codegen.py` for `contracts/` and the workflow schema blocks, and
   `npm --prefix web run gen:api` for `web/src/api/schema.d.ts`.
3. Re-run `scripts/ci.py` until every step is ok or skipped, then report the summary line per step.
4. Never weaken a test, a guard rule or a lint rule to make a step pass. If a check is wrong, say so and stop.

Context for this run: $ARGUMENTS
