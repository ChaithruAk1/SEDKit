# Playbook: building a milestone with parallel Claude Code agents

This is how M2 was built: a serial foundation, then several agents implementing independent workstreams at the same
time, then gated merges and an adversarial review. Reuse it for large changes (a new module, the app factory).

## 1. Design and freeze contracts (one agent: the integrator)

- Agree the design first (for M2: three independent proposals, judges, a synthesis and a completeness critic).
- Implement the foundation serially on a branch, with CI green after every step:
  - equivalence baselines that prove existing numbers do not change;
  - frozen contracts: module manifest, Pydantic API models and route signatures, CLI signatures, report and AI
    protocols;
  - stubs that raise `NotImplementedByWorkstream("<ws>")` (CLI exit 4, HTTP 501) wherever a workstream will fill in
    behaviour;
  - generated contracts (`scripts/codegen.py`) so drift fails CI.
- Write the ownership map (`docs/m2/ownership.yaml`): which files each workstream owns, which are frozen, which are
  generated. Tag the foundation.

## 2. Worktrees and permissions

- One manual worktree per workstream: `git worktree add C:/Projects/sed-wt/<ws> -b m2/<ws> <tag>`. Do not use the
  workflow `isolation: "worktree"` option; manual worktrees keep paths predictable.
- Agents run every command through `bash C:/Projects/sed-wt/<ws>/scripts/wt.sh python|git|npm|node ...`. The wrapper
  pins the worktree's `src` on PYTHONPATH, a scratch data root, its own pytest temp folder, and allows only
  non-destructive git commands.
- With the user's approval, add temporary rules to the gitignored `.claude/settings.local.json`: the worktree root as
  an additional directory, Read/Edit allow rules, and one `Bash(bash C:/Projects/sed-wt/<ws>/scripts/wt.sh:*)` rule per
  workstream.
- Dry-run one background agent (read, edit, test, commit) and require zero permission prompts before fanning out.

## 3. Fan out

- One agent per workstream, all in parallel, each with: its worktree, its ownership entry, the spec sections it needs,
  the rules above, and a finish gate (`scripts/ci.py`, `scripts/check_ownership.py <ws>`, `scripts/codegen.py --check`).
- Agents never edit frozen files; they append contract requests to `docs/m2/contract-requests/<ws>.md` and report.
- Structured results: status, final commit, deliverables done/missing, gates, contract requests, notes.

## 4. Merge

- Before each merge: main is clean, `check_ownership.py <ws>` exits 0.
- `git merge --no-ff m2/<ws>`, regenerate contracts on conflicts (never hand-merge generated files), run CI.
- Apply contract requests as `contract:` commits (typical: frozen tests that asserted a stub status).
- Integration steps between merges: real AI run with human review, deck and workbook builds for every report,
  performance at full scale, a served-app smoke test, cross-cutting PII and concurrency tests, `--no-stubs`.

## 5. Close

- Remove worktrees, merged branches, scratch data and the temporary permission rules.
- Update CLAUDE.md, README and docs; run an adversarial multi-agent review over the milestone diff; fix; tag.

## Lessons from M2

- Frozen tests that assert a stub's status code will fail once the real code lands; plan those contract updates.
- Parallel pytest sessions must not share pytest's numbered temp folders: the wrapper gives each worktree its own
  temp root (`PYTEST_DEBUG_TEMPROOT`). A fixed `--basetemp` is not enough, because pytest deletes it at the start
  of every session.
- Windows shells: pass Windows paths with forward slashes and quote paths with spaces; avoid backslashes in inline
  scripts.
- Give agents a fixtures mode (for example the dashboard) so they can finish before the APIs they consume are merged.
