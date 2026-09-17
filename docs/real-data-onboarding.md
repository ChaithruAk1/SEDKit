# Real-data onboarding (M3)

How to take SED from synthetic data to the real exports on this laptop (the `real` profile). Everything real stays in
`DATA_DIR` (`<data root>\real`, here `C:\Users\chait\SEDData\real`), never in the repo. Agents read real exports only
through `sed` commands, which apply the PII rules before showing values.

## 1. Create the real profile (you)
```bash
uv run sed init --profile real --new-salt --ai-approval-note "AI on real ticket data approved by <who>, <date>, <classification>" --json
uv run sed doctor --profile real --json
```
- Back up `DATA_DIR\secret\pii_salt.txt` outside the repo. Without it pseudonyms cannot be matched again, and import
  refuses a different salt.
- Add real application, vendor, SAP system and group names and the ServiceNow instance host to the guard denylist
  (`<data root>\guard\denylist.txt`, one per line) so they can never be committed.

## 2. Drop the exports
Put files into `DATA_DIR\inbox\` exactly as exported (never re-save them in Excel):

| Source | Files |
|---|---|
| ServiceNow | `incident` (18 months; one file per month when row caps apply), the "all open incidents" export, `sc_req_item`, `change_request`, `problem`, `task_sla` (if exportable), `cmdb_ci_business_app`, `cmdb_rel_ci`, `sys_user_group` |
| Jira | CSV with all fields |
| Confluence | space HTML export (optional) |
| Excel / SharePoint | vendor master, contracts register, license inventory, license usage, cost actuals, budget |
| SAP | ChaRM change documents, transport imports per system, IDoc monitor export |

## 3. Check every file (dry run)
```bash
uv run sed import --inbox --dry-run --keep-files --profile real --json
```
Files that match no mapping, miss required fields or show many transform errors need a mapping override. In Claude
Code ask: "map the export `<file>` with sed-map-export". The skill profiles the file (headers, value shapes, safe
categories only), drafts an override under `runs\map-*\out\`, proves it with `sed mappings try`, shows you the diff
and saves it with `sed mappings save-override` once you confirm (the save asks for permission and restores the
previous file when validation fails).

## 4. Real configuration
Write each override as a YAML draft (for example under `DATA_DIR\runs\drafts\`), then save it:
```bash
uv run sed config save-override settings.yaml --from "<draft>" --dry-run --profile real --json
uv run sed config save-override settings.yaml --from "<draft>" --profile real --json
```
Only the keys you change are needed; lists replace the defaults (`key+` appends, `~delete` removes a key).

| File | What to set |
|---|---|
| `settings.yaml` | `reporting_tz`, `base_currency`, `fiscal_year_start`, `display_names`, `reports.template_map` |
| `aliases.yaml` | seed aliases `{kind: {raw name: target id}}` for apps, vendors and groups that exports spell differently |
| `ops/ci_to_app.yaml` | CI names that `cmdb_rel_ci` cannot link to a business application |
| `ops/sla.yaml`, `ops/risk_rules.yaml`, `ops/vendor_groups.yaml` | SLA targets, thresholds, vendor-run groups |
| `sap/scope.yaml` | SAP areas, L3 groups, SAP categories or custom fields, landscapes with app ids, systems with roles |
| `sap/charm.yaml`, `sap/idoc.yaml`, `sap/taxonomy.yaml` | ChaRM types/statuses/components, IDoc codes and message types, SAP subcategories |

`sed doctor --profile real --json` then warns about SAP groups never seen, unmapped ChaRM values and unknown IDoc codes.

## 5. Import
```bash
uv run sed import --inbox --profile real --json
uv run sed alias list --unmapped --profile real --json
uv run sed alias assign <kind> "<raw value>" <target id> --profile real --json
uv run sed analytics refresh --profile real --json
```
Assigning an alias re-links the stored rows (`sed import reresolve` does it for all).

## 6. Reconcile (the M3 gate)
Take one recent full month from a trusted ServiceNow report:
```bash
uv run sed metrics reconcile --period 2026-08 --opened <n> --resolved <n> --p1 <n> --backlog <n> --sla-pct <x> --profile real --json
```
Passes when counts are within 2% (`--tolerance-pct`), SLA within 1 pp (`--sla-tolerance-pp`) and at least 98% of the
month's incidents link to an application (`--min-app-link-pct`). `sla_source` shows whether SLA came from `task_sla`,
`made_sla` or the configured targets; the official report usually uses `task_sla`. Scope with `--group` when the
trusted report is scoped to groups.

## 7. Reports without AI
```bash
uv run sed report build weekly --period 2026-W35 --format xlsx,md,pptx --ai none --profile real --json
uv run sed report build sap-weekly --period 2026-W35 --format xlsx,md,pptx --ai none --profile real --json
```
Corporate template: save it as `.pptx` in `DATA_DIR\config\templates\`, run `sed report template-inspect`, save the
suggested map with `sed config save-override templates/corporate.map.yaml`, check it with
`sed report template-proof --map corporate`, then set `reports.template_map: corporate` in `settings.yaml`.

## 8. Headless spike (Task Scheduler)
Goal: prove a scheduled, unattended run works on this laptop before automation depends on it (M6).
1. In Task Scheduler create a task "SED weekly import" that runs only when you are logged on, with action
   `powershell -NoProfile -Command "cd '<repo>'; uv run sed import --inbox --profile real --json | Out-File -Encoding utf8 '<data root>\real\logs\scheduled-import.json'"`.
2. Add a second action for the AI step: `claude -p "Run the sed-triage-batch skill for scope new on profile real" --output-format json`.
3. Run the task once by hand and record below: exit codes, whether Claude Code asked for permissions, run time, and
   anything the managed settings blocked.

Spike results: _not run yet_.

## Done when (M3)
- A real weekly deck and workbook build with `--ai none`.
- `sed metrics reconcile` passes for a recent month (volumes, P1, backlog within 2%, SLA within 1 pp, app link ≥ 98%).
- `uv run python scripts/guard_confidential.py --history` passes.
- The headless spike result is recorded above.
