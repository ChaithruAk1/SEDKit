# AI walking skeleton runbook (integration step I1)

I1 is the first real Claude Code run of the AI layer, right after `m2/ws1-ai` merges and before any other merge.
It proves, on the synthetic profile, that 100 tickets go through the `sed-analyze` workflow (start-run, one agent per
packet, ingest, finish-run) with **zero permission prompts**, that a human can review and approve the run, and that the
weekly workbook then carries AI categories with a sample-accuracy provenance line. It also measures the real Read-tool
limits so the packet settings can be tuned.

Everything below runs in the **main checkout** (the repository root) through the Bash tool or a terminal. Quote
paths, in case the checkout path contains spaces. Replace `<RUN>` with the run id and `<you>` with the Windows user
name. The offline equivalent of this flow is
`tests/modules/ops/ai/test_walking_skeleton.py` (fake agent, no Claude calls).

## 0. Preconditions
- `m2/ws1-ai` is merged, `uv run python scripts/ci.py` is green on main, and no other workstream merges until I1 is
  done (step 1 of the merge order is blocking).
- `git status --porcelain` in the main checkout prints nothing.

## 1. Restart the Claude Code session
Skills and workflows are discovered when a session starts. Exit the current session and start a new one in the main
checkout (run `claude` from the repository root). Check that the new session lists the `sed-triage-batch` skill
and the `sed-analyze` workflow.

## 2. Migrate and check the profile
```bash
uv run sed init --profile synthetic --json
uv run sed doctor --profile synthetic --json
```
- `init` migrates the database to the latest schema (003 adds `ai_batch_item`, `ai_sample`, `ai_run.sample_n`) and
  takes a backup first (`migration.backup` in the JSON).
- `doctor` must report `ok` for `schema_current`, `skills_claimed`, `skill_names_valid`, `claude_settings_local` and
  `prefix_allow_rule`.

## 3. Permissions
Run `/permissions` in the session. It must list:
- the additional directory `C:\Users\<you>\AppData\Local\sed\synthetic\runs`;
- allow rules `Read(//c/Users/<you>/AppData/Local/sed/synthetic/runs/**)` and
  `Edit(//c/Users/<you>/AppData/Local/sed/synthetic/runs/**)`, plus `Bash(uv run sed:*)`;
- deny rules for `ground_truth`, `secret`, `inbox` and `config` under the profile folder.

Record the result in the measurement table.

## 4. Data
Check that August 2026 has eligible tickets (a dry run writes nothing):
```bash
uv run sed ai start-run sed-triage-batch --scope period:2026-08 --limit 100 --batch-size 50 --dry-run --profile synthetic --json
```
Expect `plan.items` 100 and `plan.batches` 2. If the command exits 4 (no database) or `plan.items` is below 100,
generate and import synthetic data first, then repeat the dry run:
```bash
uv run sed synth --profile synthetic --json
uv run sed import --inbox --profile synthetic --json
uv run sed analytics refresh --profile synthetic --json
```

## 5. Record the Claude Code version
```bash
claude --version
```
Copy the output (for example `2.1.0 (Claude Code)`) into `claudeVersion` below and into the measurement table.

## 6. Run the workflow
In the session, ask Claude to run the saved workflow `sed-analyze` with these args (JSON values, not a string):
```json
{"profile": "synthetic", "steps": ["triage"], "scope": "period:2026-08", "limit": 100, "batchSize": 50,
 "claudeVersion": "<output of claude --version>"}
```
While it runs, count every permission prompt (the target is 0) and note which tool asked. The workflow logs the plan
(items, batches, largest packet, longest line), failed batches by name, and the final counts with the review command.

If the workflow stops midway, resume the same run instead of starting a new one:
```json
{"profile": "synthetic", "steps": ["triage"], "resumeRunIds": {"triage": "<RUN>"}, "claudeVersion": "<version>"}
```

## 7. Claims count check
Exactly the 100 trimmed tickets must have been claimed, and finish-run must release all of them:
```bash
uv run sed ai runs --skill sed-triage-batch --limit 1 --profile synthetic --json
```
- `runs[0].counts.claimed` is 100 and `runs[0].counts.claims_released` is 100 (the workflow's final log shows the
  same numbers).
- `runs[0].status` is `completed`, `counts.ingested_batches` is 2 and `counts.failed_batches` is 0.
- `runs[0].model_reported` and `runs[0].claude_version` are filled in.

If a batch failed, triage it in the session with the `sed-triage-batch` skill procedure for that batch only, or
resume the workflow (step 6) before finish-run; after finish-run a failed batch stays failed.

## 8. Human review
Write the verdicts template into the run folder (outside `out/`):
```bash
uv run sed review sample <RUN> --template "C:/Users/<you>/AppData/Local/sed/synthetic/runs/<RUN>/review/verdicts.json" --profile synthetic --json
```
The JSON shows the 30 random stratified cards, the 30 lowest-confidence cards, the ServiceNow-vs-AI category matrix
and misfiled counts. Open the template and replace each `null` with `"correct"` or
`{"verdict": "incorrect", "category": "<code>", "subcategory": "<code or null>"}`. A key left `null` is skipped.
Every key of the random sample needs a verdict before approval. Then:
```bash
uv run sed review verdicts <RUN> --file "C:/Users/<you>/AppData/Local/sed/synthetic/runs/<RUN>/review/verdicts.json" --profile synthetic --json
uv run sed review approve-run <RUN> --note "I1 walking skeleton" --profile synthetic --json
```
`verdicts` must report `random_missing` 0. `approve-run` prints `sample_accuracy`, `sample_ci_low`,
`sample_ci_high` and `sample_n` (30). If the labels are unusable, reject instead:
`uv run sed review reject-run <RUN> --note "<why>" --profile synthetic --json`.

## 9. Weekly workbook with AI provenance
```bash
uv run sed report build weekly --period 2026-W35 --format xlsx --ai approved --profile synthetic --json
```
- `ai_runs` in the JSON is `["<RUN>"]`.
- Open the workbook from `artifacts[0].path`. The **Categories** sheet has AI categories other than
  `(not labelled)`. The **Provenance** sheet has the line
  `sed-triage-batch <first 12 characters of the skill hash> run <RUN>, approved by <you> at <time>: sample accuracy ...`.
- The **Definitions** sheet lists the four `ai.category.*` facts.

## 10. Measurements
Fill in this table and keep it with the I1 notes.

| Item | Value | How to measure |
|---|---|---|
| `claude --version` | 2.1.267 (Claude Code) | Step 5 |
| Triage model (`triageModel` arg, or inherited) | inherited (`model_arg` empty) | Workflow args or session model |
| `model_reported` | claude-opus-5 | `sed ai runs` (step 7) |
| Permission prompts during the workflow (target 0) | 0 | Count while step 6 runs |
| `/permissions` lists the runs rules and deny rules | not opened; zero prompts with the generated rules | Step 3 |
| Read truncation: lines read before the first truncation of a packet | none: 50-line packets read whole | Batch agent transcript (Read result says it was truncated) |
| Read truncation: characters read before truncation | none up to 16,386 characters | Same transcript; compare with `plan.max_chars_per_batch` |
| Packet size: `max_chars_per_batch` / `longest_line_chars` | 16,386 / 421 (50 items per batch) | Workflow plan log |
| Did agents page with offset/limit when truncated (yes/no) | not needed (no truncation) | Batch agent transcripts |
| Batches ingested on the first attempt / after retries / failed | 2 / 0 / 0 | Workflow result `batches[]` (`errors_count`) |
| Claims claimed / released (target 100 / 100) | 100 / 100 | Step 7 |
| Wall-clock time per batch agent / whole workflow | 99 s and 105 s / 2 min 16 s | Workflow progress view |
| Tokens used by the workflow | about 21k output tokens (batch agents 9.4k and 9.7k) | Workflow summary |
| Sample accuracy (95% CI), `sample_n` | 100% (88.6-100%), 30 | `approve-run` output (step 8) |
| Lowest-confidence error rate | n/a (no labels below 0.5; lowest-confidence items not reviewed) | `sed ai runs` (`lowest_conf_error_rate`) |
| Tuned `ai.triage_batch_size` (before -> after) | 100 -> 100 (unchanged: no truncation measured) | Step 11 |
| Tuned `ai.triage_packet_max_chars` (before -> after) | 40000 -> 40000 (unchanged) | Step 11 |

## 11. Tune the packet settings
Set `ai.triage_batch_size` and `ai.triage_packet_max_chars` in `config/settings.yaml` so that one packet is read in a
single Read call with margin (for example 80% of the measured truncation limit), then land the change as a
`contract:` commit on main with the measurement table in the commit message. Re-run
`uv run python scripts/ci.py` before merging the next workstream.
