# SED modules

SED is a platform: the core (import engine, PII, database, AI run lifecycle, report engines, API host, dashboard shell)
knows nothing about any business domain. Each domain is a **module**. Ops (tickets, SLA, costs, licenses, vendors) is
module #1. Later modules (for example delivery management of new business apps) plug in the same way.

## What a module declares

A module is a package `sed.modules.<key>` exposing `MODULE: Module` (see `src/sed/modules/contract.py`). Everything
is a lazy import reference (`"package.module:attr"`), so the core imports module code only when a surface is used.

| Surface | Declaration | Where it appears |
|---|---|---|
| CLI | `CliMount(name, app)` | `sed <name> ...` (keep names short; core names are reserved) |
| API | `ApiMount(router)` | `/api/<key>/...` with the core's host check, token and error envelope |
| Dashboard | `NavItem(id="<key>.<page>", path="/<key>/...")` + `web/src/modules/<key>/index.ts` | `#/<key>/...`, nav from `GET /api/nav` |
| Reports | `ReportDef(key, title, spec, builder, period_kinds, ...)` | `sed report build <key>`; spec in `config/<key>/reports/` |
| AI skills | `SkillDef(name="sed-<...>", handler)` + `.claude/skills/<name>/` | `sed ai start-run <name>` |
| Import | `mappings_dir`, `ingest_targets`, `ingest_hooks`, `entities`, `alias_kinds` | `sed import`; mappings in `config/<key>/mappings/` |
| Synthetic data | `SynthDef(generate)` | `sed synth [--module <key>]`; ground truth under `ground_truth/<key>/` |
| Definitions | `metric_definitions` | Definitions sheet, deck notes, `/api/meta` |
| Health | `doctor_checks` | `sed doctor` as `<key>.<check>` |
| Config | `config_files`, `data_subdirs` | `config/<key>/...`, overridable in `DATA_DIR\config\<key>\` |
| Tables | `tables` | documentation and collision checks |

Enable or disable modules in the layered `config/modules.yaml` (`enabled: [ops]`). A disabled module's commands exit 4,
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
- Keys, report keys, skill names, nav ids and CLI names are unique; skills start with `sed-`; nav paths live under
  `/<key>`.
- Module tables are disjoint from core tables.

## Shared ("portfolio") tables

`vendor`, `application`, `work_item` and `doc_page` are core-owned schema shared by all modules. Any module may write
them through its own ingest targets (with distinct mapping names). Module-specific tables belong to one module.

## Known schema debt (relax before adding module #2)

Migration 001 has CHECK constraints listing ops values: `alias.kind`, `finding.kind`, `report_snapshot.report_key`,
`review_decision.target_type/decision`, and `ai_claim` references `ticket`. The registry validates declarations against
`SCHEMA_CHECKS` so a module cannot silently violate them; a migration relaxing these constraints (and module-owned
migration streams) is planned before the delivery-management module.
