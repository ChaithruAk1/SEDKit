# SED

SED is a personal toolkit for an application owner, built as a platform of modules (ops is module #1, SAP application
support is module #2, delivery management is module #3; see `docs/modules.md`). It:

1. **Imports** ITSM (incidents, requests, changes, problems, SLAs, CMDB), Jira, Confluence and Excel/SharePoint
   exports into one local SQLite database.
2. **Computes exact metrics:** SLA, MTTR, backlog, costs, renewals and license use.
3. **Runs AI analysis in Claude Code:** ticket triage, recurring issues, risks and report drafts. You approve the
   results before they reach a report.
4. **Builds reports:** weekly, monthly, quarterly and vendor reports as PowerPoint and Excel, plus a local dashboard.

> All data in this repository is synthetic. Real exports and configuration live in `DATA_DIR`
> (`%LOCALAPPDATA%\sed\<profile>`, or under `SED_DATA_ROOT` when set), outside git. If you run SED from the Claude
> desktop app, read [docs/data-location.md](docs/data-location.md) first.

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
`.github/workflows/ci.yml` runs the same command on every push and pull request.

## Repository layout

```
src/sed/          the package: CLI, ingest, metrics, AI, reports, API, module registry (src/sed/modules/<key>)
web/              the dashboard (Vite + React + Mantine); web/src/design holds the design book
config/           synthetic defaults, overridable file by file from the data folder
contracts/        generated API contract and schemas (scripts/codegen.py; never hand-edited)
docs/             architecture, data model, export specs, playbooks, runbooks
evals/  templates/  tests/  scripts/
.claude/          Claude Code configuration: settings, skills, commands, agents, workflows
.github/workflows/  CI
CLAUDE.md         the agent contract for this repository (web/ and each module carry their own)
```

Everything real — exports, mappings with real values, the corporate template, branding, salts, ground truth — lives in
the data folder outside the repository (`docs/data-location.md`).

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
| sap-weekly | ISO week | `uv run sed report build sap-weekly --period 2026-W35 --profile synthetic` |
| delivery-status | month | `uv run sed report build delivery-status --period 2026-08 --profile synthetic` |

- `--ai approved|none|draft` controls AI content; `none` builds deterministic, shareable files, `draft` stamps DRAFT on
  every page. Every report has Markdown, and AI-drafted sections (headline, executive summary, asks...) declared under
  `sections:` in its spec.
- AI sections: after reviewing findings, run the `sed-report` workflow with `{profile, report, period}` (or the
  `sed-draft-report` skill): it freezes the snapshot, drafts one section per agent with every number as a fact token,
  and builds a DRAFT report. Approve the sections in `#/review`, check `uv run sed report readiness monthly --period
  2026-08`, then build with `--ai approved --require-complete`. A section is left out while a number it cites has
  changed or a finding it cites is not approved.
- `#/reports` in the dashboard shows section readiness, builds in the background and lists artifacts to download.
- `uv run sed ai packet <run_id> --text` shows exactly what a run sends to the agents; `uv run sed report eval <run_id>`
  scores a drafting run.
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

The front screen greets you with one search across tickets, applications, delivery projects and vendors, a
suggestion for what to do next, the three steps (bring in data, review findings, build reports) and a tile for every
area. A sidebar lists the pages and the items waiting for your review; pages open in tabs. The look is light by
default, with a dark look one click away, and follows the design book in `web/src/design/DESIGN.md` (every number in
`tokens.css`, every colour in `faces.css`, one stylesheet per shared object).

Branding stays on your machine and never enters the repository: a logo, a watermark for each look and a title for the
sidebar, stored in `<data root>\branding\` and served only by your local dashboard.

```bash
uv run sed branding logo "C:\path\to\logo.png" --json
uv run sed branding watermark "C:\path\to\mark-light.png" --json
uv run sed branding watermark "C:\path\to\mark-dark.png" --dark --json
uv run sed branding title "Your team name" --json
uv run sed branding show --json
```

## SAP application support (module `sap`)

SAP tickets stay ordinary ops tickets. `config/sap/scope.yaml` decides on read which of them are SAP L3 tickets (an
SAP assignment group, one per SAP area, or an SAP category or custom field) and gives each its area and landscape (ECC
or S/4HANA, from the application). Real group names, categories and application ids go in
`DATA_DIR\config\sap\scope.yaml`; `uv run sed doctor --profile real` warns about groups never seen on a ticket.

```bash
uv run sed synth --module sap --profile synthetic     # SAP apps and incidents with planted patterns
uv run sed import --inbox --profile synthetic
uv run sed analytics refresh --profile synthetic      # SAP backlog risks per area (config/sap/risk_rules.yaml)
uv run sed report build sap-weekly --period 2026-W35 --ai none --profile synthetic
```

Changes come from the Solution Manager ChaRM exports: change documents (weekly deltas; each new user status is kept
as history) and transport imports per system. `config/sap/charm.yaml` maps transaction types, statuses, components and
change cycles, and names the Jira projects whose stories reference ChaRM changes (either way round);
`config/sap/scope.yaml` lists the SAP systems with their landscape and role.

IDoc health comes from the IDoc monitor export: one row per IDoc with its current status (daily files); each new status
is kept as history. `config/sap/idoc.yaml` groups the status codes (ok, in process, error, closed), maps message types to
SAP areas and sets the reprocessing grace time: errors fixed within it are normal operation, the rest are persistent
errors.

Dashboard pages:
- `#/sap`: KPIs, areas, landscapes and SAP risks.
- `#/sap/tickets`: area and landscape filters, trend, aging, SLA, arrivals vs closures, AI-assisted SAP
  subcategories, attention list.
- `#/sap/changes`: open changes by stage, urgent share by area, production imports, failed imports, SAP incidents
  after production imports, stuck changes, transports waiting for production, changes without a Jira story.
- `#/sap/idocs`: IDoc errors by system, message type and partner, aging, new and persistent errors per week,
  reprocessing times, frequent error texts, error spikes after production imports.

AI triage gives SAP tickets SAP subcategories under the portfolio categories (`config/sap/taxonomy.yaml`: IDoc error,
interface, job failure, month-end close, authorisation, role request, master data, custom code dump, transport issue,
Basis, performance, how-to). The packet line of a SAP ticket carries its SAP area and landscape. Run SAP tickets only
with `--only sap`, then score a synthetic run against the ground truth (scores only):

```bash
uv run sed ai start-run sed-triage-batch --scope period:2026-08 --only sap --dry-run --profile synthetic --json
uv run sed sap eval-triage <run_id> --profile synthetic --json     # thresholds in evals/thresholds.yaml
```

`#/sap/tickets` and `sap-weekly` show the breakdown of approved labels, marked AI-assisted with the run's sample
accuracy; `--ai none` leaves it out.

Metrics stay at group (area), system and partner level, never per person.

## Delivery management (module `delivery`)

The delivery module follows the projects that build new business applications. It imports three exports into
`DATA_DIR\inbox`:
- the project register (workbook or CSV: project, application, phase, reported RAG, sponsor, manager, Jira project
  keys, Confluence space, start, target go-live, budget);
- MS Project or Excel plan exports with a status date (task, milestone flag, start, finish, baseline finish, actual
  finish, % complete); keep every weekly version, because slips and replans come from comparing them;
- the RAID log (type, title, owner, severity, status, raised, due and closed dates).

Jira stories and Confluence pages come in through the ops mappings and are linked by the register's Jira keys and
Confluence space.

```bash
uv run sed synth --module delivery --profile synthetic   # four fictional projects with planted slips and risks
uv run sed import --inbox --profile synthetic
uv run sed analytics refresh --profile synthetic         # delivery risks (config/delivery/risk_rules.yaml)
uv run sed delivery portfolio --profile synthetic --json
uv run sed report build delivery-status --period 2026-08 --ai none --profile synthetic
```

Health is computed, not copied: red for a milestone 30+ days past baseline or overdue, an overdue high RAID item or a
forecast finish after the target go-live; amber for a 14-day slip, open high RAID items or scope growth above the
threshold. The reported RAG is shown next to it. Dashboard pages: `#/delivery` (portfolio) and
`#/delivery/projects/<id>` (plan, burn-up, RAID, requirement and ADR pages, risks). SED never writes to Jira,
Confluence or the plan tools.

### AI drafts for delivery (stories, ADRs, test plans, release notes)

Four skills draft delivery documents for one project. Each draft waits in `#/review`, and only approved drafts are
exported as files. SED never writes to Jira, Confluence or a test tool.

| Skill | From | Draft | Export (`sed delivery export ... --project <id>`) |
|---|---|---|---|
| `sed-draft-stories` | requirements pages | user stories with acceptance criteria, linked to epics | `stories`: Jira CSV import file |
| `sed-draft-adr` | requirements, recorded ADRs, approved stories | ADRs (context, options, decision, consequences) | `adr`: one Markdown file per ADR |
| `sed-draft-test-plan` | approved stories | test cases per story | `test-plan`: Markdown plan and a CSV of cases |
| `sed-draft-release-notes` | Jira issues resolved in a period | release notes with fact tokens | `release-notes`: Markdown with the numbers filled |

```bash
uv run sed ai start-run sed-draft-stories --subject PRJ-101 --dry-run --profile synthetic --json
uv run sed delivery export stories --project PRJ-101 --profile synthetic --json
```

Exports read the approved text, so reviewer edits reach the file. A test plan can be approved only while its stories
are approved. Files land in `DATA_DIR\out\delivery\<project>\`. For code review of a module change, the
`sed-review-module` skill runs the module checks and reports verified findings in chat.

## Building a new module (app factory)

New capabilities are built as modules inside SED with the `sed-build-module` skill: approved stories and ADRs, then a
scaffold, vertical slices with tests, the quality gate, an adversarial review (`sed-review-module`) and a checkpoint
with you before any commit.

```bash
uv run sed modules new crm --title "Customer relations" --depends-on ops --dry-run --json
uv run sed modules new crm --title "Customer relations" --depends-on ops --json
uv run python scripts/codegen.py
uv run sed modules gate crm --run-tests --json
```

The scaffold is a working module (manifest, API route, CLI command, page with a fixture, test, CLAUDE.md) registered
in `BUILTIN`, `config/modules.yaml`, the web fixtures and `docs/modules.md`. The gate checks the manifest, import
boundaries, owned paths, config files, skill folders, web routes and fixtures, tests and docs (`--run-tests` also runs
the module's tests). Details: `docs/modules.md`.

## AI analysis (Claude Code)

- Triage runs through the `sed-triage-batch` skill (small runs in-session) or the `sed-analyze` workflow (larger runs):
  start-run writes scrubbed packets, one agent per packet labels them, `sed ai ingest` validates, `sed ai finish-run`
  draws a review sample.
- The weekly analysis is one workflow run, `sed-analyze` with `{profile: "real"}`: import the inbox (stops on import
  errors), refresh rule findings, triage, recurring issues, then risks when contract, license, cost or vendor files
  changed or on the first run of a month (`riskMode: "always"|"never"` overrides). `steps` picks a subset, and
  `resumeRunIds: {triage, recurring, risks}` resumes an interrupted run.
- Recurring issues (`sed-find-recurring`), risks (`sed-assess-risks`) and open P1–P3 tickets (`sed-triage-open`) produce
  draft findings; `sed-eval` scores runs against the synthetic ground truth.
- Review happens in the dashboard or in the terminal. `#/review` is a keyboard queue: approve, reject, edit,
  approve wording updates, acknowledge or suppress system-detected findings. `#/runs/<run_id>` shows a label run's
  sample, where you record verdicts and approve or reject the run. In the terminal: `uv run sed review sample RUN
  --template verdicts.json`, fill in verdicts, `uv run sed review verdicts RUN --file verdicts.json`, then
  `uv run sed review approve-run RUN` (the `sed-review` skill walks through it).
- Approved labels appear in reports with their sample accuracy and confidence interval, and approved findings
  (recurring issues, risks) appear in the weekly report (AI findings sheet and slide); `--ai none` leaves both out.
  See `docs/ai/walking-skeleton-runbook.md`.

## Every source by API or by file

Each export SED reads (ServiceNow, Jira, Confluence, Excel and SharePoint workbooks, MS Project plans, SAP ChaRM,
transports and IDocs) can come either way, and both end in the same import:
- **By API**, when access is given: `uv run sed pull <connector> --import --profile real --json`, or **Pull now** on
  `#/data`. SharePoint document libraries (plans, RAID logs, cost and contract workbooks) are pulled as files.
- **By file**: upload the export on `#/data` (a Confluence space export as a `.zip`), or drop it into the inbox and run
  `sed import --inbox`.

`uv run sed sources --profile real --json` and the Sources card on `#/data` show, for every export, the connector
sources configured for it, whether each connector can pull now, and the last import. The full source matrix with the
connector settings and export recipes per export: `docs/sources.md`.

## Automation and connectors

- `uv run sed pull servicenow|jira|sharepoint|confluence|sap --profile real` reads the APIs with a read-only account and
  writes export-shaped files into the inbox; `sed import --inbox` (or `--import` on the pull) runs unchanged. Secrets live in the Windows Credential
  Manager, an environment variable or the profile's `secret` folder, never in config. `sed pull status` and
  `sed doctor` check the setup without calling the API.
- `uv run sed schedule write --profile real` writes a weekly script and a Task Scheduler definition into the data
  folder (pull, import, rule findings, headless `sed-analyze`); you register it yourself. Nothing unattended approves
  anything.
- `uv run sed ai review-rates` (and `#/runs`) shows how reviewers decided per skill version.
- Details: `docs/automation.md`.

## Real data

Onboarding real exports on the `real` profile (profile creation, export list, mapping overrides with the
`sed-map-export` skill, config overrides, reconciliation with `sed metrics reconcile`, reports without AI and the
headless spike): `docs/real-data-onboarding.md`.

## Status

| Milestone | Scope | Status |
|---|---|---|
| M0 | Skeleton & guardrails | done |
| M1 | Synthetic data, import, metrics, weekly Excel | done |
| M2 | Modular platform, AI walking skeleton, all decks, API and dashboard core | done |
| SAP S0–S4 | SAP module: L3 view, ChaRM changes, IDoc health, SAP subcategories in AI triage | done |
| M3 | Reality check with real exports | tooling done; real run pending |
| M4 | Full AI analysis & review | built; AI-run gates (90-day triage, eval slice, real 50-ticket sample) pending |
| M5 | AI-drafted reports | built; a real monthly draft (needs Claude usage) pending |
| M6 | Automation & connectors | built; real connector setup and the scheduled run pending |
| M7 | AI-native SDLC: delivery module (D1), AI drafts for stories, ADRs, test plans and release notes (D2), app factory (D3) | built |
| SAP S5 | SAP connectivity (`sed pull sap`), last wave | built; SAP services, technical user and reconciliation pending |
| W8 | Every source by API or by file: dashboard upload, Pull now, SharePoint document libraries, source matrix | built; real connector access pending |
