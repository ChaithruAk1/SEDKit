# SED modules

SED is a platform: the core (import engine, PII, database, AI run lifecycle, report engines, API host, dashboard shell)
knows nothing about any business domain. Each domain is a **module**:

| Module | Key | Scope |
|---|---|---|
| #1 | `ops` | tickets, SLA, backlog, costs, licenses, vendors; weekly, monthly, quarterly and vendor reports |
| #2 | `sap` | SAP L3 support by area and landscape on top of the ops tickets, ChaRM changes and transports, IDoc health, SAP risks, SAP subcategories in AI triage, `sap-weekly` (`src/sed/modules/sap/CLAUDE.md`) |
| #3 | `delivery` | delivery of new business applications: project register, plan milestones and slips, RAID log, Jira progress and forecast, requirements and ADR pages, delivery risks, `delivery-status` (`src/sed/modules/delivery/CLAUDE.md`) |

Later modules plug in the same way.

## What a module declares

A module is a package `sed.modules.<key>` exposing `MODULE: Module` (see `src/sed/modules/contract.py`). Everything
is a lazy import reference (`"package.module:attr"`), so the core imports module code only when a surface is used.

| Surface | Declaration | Where it appears |
|---|---|---|
| CLI | `CliMount(name, app)` | `sed <name> ...` (keep names short; core names are reserved) |
| API | `ApiMount(router)` | `/api/<key>/...` with the core's host check, token and error envelope |
| Dashboard | `NavItem(id="<key>.<page>", path="/<key>/...")` + `web/src/modules/<key>/index.ts` | `#/<key>/...`, nav from `GET /api/nav` |
| Reports | `ReportDef(key, title, spec, builder, period_kinds, ...)` | `sed report build <key>`; spec in `config/<key>/reports/`; AI sections under `sections:` (drafted by `sed-draft-report`, shown on `narrative` slides) |
| AI skills | `SkillDef(name="sed-<...>", handler)` + `.claude/skills/<name>/` | `sed ai start-run <name>` |
| Import | `mappings_dir`, `ingest_targets`, `ingest_hooks`, `entities`, `alias_kinds` | `sed import`; mappings in `config/<key>/mappings/` |
| Synthetic data | `SynthDef(generate)` | `sed synth [--module <key>]`; ground truth under `ground_truth/<key>/` |
| Definitions | `metric_definitions` | Definitions sheet, deck notes, `/api/meta` |
| Rule findings | `finding_kinds` + `rule_findings` | "System-detected" risks in `finding`, refreshed per module (`sed.rule_findings`) |
| Health | `doctor_checks` | `sed doctor` as `<key>.<check>` |
| Extension points | `extension_points` (owner) and `Extension(point="<owner>.<name>", ref)` (contributor) | whatever the owner does with the contributions (see below) |
| Config | `config_files`, `data_subdirs` | `config/<key>/...`, overridable in `DATA_DIR\config\<key>\` |
| Tables | `tables` | documentation and collision checks |

Enable or disable modules in the layered `config/modules.yaml` (`enabled: [ops, sap, delivery]`). A disabled module's commands exit 4,
its API routes are not mounted and its pages are hidden.

## Adding a module

1. Create `src/sed/modules/<key>/__init__.py` with `MODULE = Module(key="<key>", ...)` and add it to `BUILTIN` in
   `src/sed/modules/__init__.py`.
2. Put config under `config/<key>/`, tests under `tests/modules/<key>/`, pages under `web/src/modules/<key>/`, skills
   under `.claude/skills/sed-<key>-<verb>/`.
3. Run `uv run sed modules check --json` and `uv run python scripts/codegen.py`, then CI.
4. `tests/platform/modules/sample_module` (module `hello`) is the minimal working example: CLI, API and nav with no core
   edits.

Rules enforced by tests (`tests/platform/test_registry.py`, `test_core_boundaries.py`):
- Core code never imports `sed.modules.<key>` (only through the registry). Modules may import the core.
- Keys, report keys, skill names, nav ids, CLI names, alias kinds and finding kinds are unique; skills start with `sed-`;
  nav paths live under `/<key>`.
- Module tables are disjoint from core tables.

## Extension points

A module can let other modules add to one of its features without importing them. The owner declares a name in
`extension_points` and defines what a contribution's import reference resolves to; a contributor declares
`Extension(point="<owner key>.<name>", ref)` and must depend on the owner. The owner reads the contributions of the
enabled modules with `sed.modules.extensions("<owner key>.<name>", paths)`.

| Point | Owner | A contribution is | Used by |
|---|---|---|---|
| `ops.triage` | ops | `(Paths) -> TriageExtension` (`src/sed/modules/ops/ai/extensions.py`): a packet field for the module's tickets, subcategories under the portfolio categories, field descriptions and a guide | `sed-triage-batch`: the field goes on the module's packet lines, the subcategories and guide into `in/context.md`, the configuration into the skill hash; ingest accepts the subcategories only on the module's tickets; `start-run --only <key>` keeps only those tickets |

The SAP module contributes to `ops.triage` (`src/sed/modules/sap/triage.py`, `config/sap/taxonomy.yaml`).

## Rule findings

A module that computes deterministic risks declares the `finding_kinds` it owns and `rule_findings`, a function
`(conn, paths, as_of) -> list[dict]`. Each finding has `stable_key` (starting with `<kind>:`), `kind`, `subject_type`,
`subject_id`, `severity`, `title` and `evidence` (`[{fact_key, value}]`). The core engine (`src/sed/rule_findings.py`)
upserts them, keeps human acknowledgements and suppressions, and supersedes only rows of that module's kinds, so two
modules never undo each other's findings. Each module keeps its own refresh state in `meta`
(`<key>.rule_findings_as_of`).

## Import targets and hooks

- `ingest_targets` points at a dict `name -> Target` (`src/sed/ingest/target.py`). A target describes where mapped rows
  go and how its load mode is applied:
  - `table`, `key`, `columns`, `build(record, ctx)`;
  - `updated_field` (delta freshness guard), `soft_delete`, `snapshot_field` (append-snapshot scope),
    `active_scope` (active-snapshot scope);
  - `batch_column`, `after_load`, and ordering: `order`, then `sort_key(spec)`.
  The loader itself knows no domain tables.
- `ingest_hooks` points at an object implementing `IngestHooks` (`src/sed/ingest/hooks.py`):
  - `session_start` (seed aliases, load directories);
  - `before_target`;
  - `relink` (the tables `sed import reresolve` re-links after an alias changes).
- Mapping YAMLs live in `config/<key>/mappings/`, and names must be unique across modules. `entities` and `alias_kinds`
  tell the resolver which canonical tables alias targets point at; `validate_ids=False` keeps free-text ids unvalidated.

## Shared ("portfolio") tables

`vendor`, `application`, `work_item` and `doc_page` are core-owned schema shared by all modules. Any module may write
them through its own mappings, and a module that depends on another may also load that module's targets (the `sap`
mappings load `ticket` and `application` rows through the ops targets). Snapshots stay within their module:

- a `full_snapshot` file soft-deletes only rows that a mapping of its own module last wrote;
- an `active_snapshot` file ("all open" export) flags as stale only open rows that a mapping of its own module last
  wrote.

A row belongs to the module whose mapping wrote it last (`last_batch_id`), including mappings renamed since; a row no
installed module's mapping wrote stays in every module's scope (`sed.ingest.loader._module_rows`). One module's
snapshot therefore never retires or stales rows another module's files loaded. Module-specific tables belong to one
module.

A module can also select rows of shared tables on read instead of copying them: `metrics.Filters.scope_sql` takes an
extra ticket predicate (written with `{t}` for the alias), which is how the SAP scope reuses every ops metric.

## Synthetic inbox manifest

`sed synth` generators list the files they write in `inbox\_manifest.json`, one section per module
(`src/sed/ingest/manifest.py`: `clean(inbox, key)` before generating, `save_section(inbox, key, ...)` after). The
loader imports on the synthetic profile only files in the union of all sections, so regenerating one module never
unlists or deletes another module's files.

## Remaining schema debt

Migration 005 removed the ops-only CHECK constraints on `alias.kind`, `finding.kind` and `report_snapshot.report_key`;
the registry now owns those values. Still ops-shaped: `review_decision.target_type/decision`, and the AI claim and label
tables (`ai_claim`, `ai_ticket_label`) reference `ticket`. Migrations remain one global numbered stream; a module's
schema files start with `-- owner: <key>`.
