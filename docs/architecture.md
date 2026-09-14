# SED architecture

One local Python core over one SQLite database, a module registry, and three kinds of consumers: the CLI (humans and
Claude Code agents), the report builders and the local API/dashboard. Nothing leaves the machine except what an agent
reads from scrubbed AI packets.

```
exports (ServiceNow, Jira, Confluence, Excel) -> DATA_DIR/inbox -> sed import
    readers -> mapping YAML -> PII pseudonymise/scrub -> alias resolve -> module ingest targets -> upsert (DQ, provenance)
                                              |
                                  SQLite sed.db (WAL, outside the repo)
        writes: SED Python only (CLI, API POSTs)  |  reads: per-request / per-command connections
                                              |
   metrics + rule findings (exact, compute on read)     AI runs (sed ai start-run -> packets -> agents -> ingest)
                                              |
   report snapshots (frozen facts + tables + provenance) -> XLSX / Markdown / PPTX (template maps) -> DATA_DIR/out
                                              |
   FastAPI on 127.0.0.1 (host allowlist, per-launch token) -> React dashboard (web/, served from web/dist)
```

## Core and modules

- **Core** (`src/sed/`): paths, layered settings, database and migrations, PII, CLI plumbing, ingest engine
  (`ingest/`), report engines (`reports/`), AI run lifecycle (`ai/`), API host (`api/`) and the module registry
  (`modules/`). The core never imports module code directly (`tests/platform/test_core_boundaries.py`).
- **Modules** (`src/sed/modules/<key>/`): a manifest (`MODULE`) declares CLI mounts, API routers, dashboard pages,
  reports, AI skills, ingest targets and hooks, synthetic data, metric definitions, doctor checks, config and tables
  as lazy import references. Ops is module #1. How to add one: `docs/modules.md`.
- **Contracts** are generated from code into `contracts/` (`cli.json`, `openapi.json`) and checked in CI; skill output
  schemas are generated from Pydantic models.

## Key invariants

1. Only SED Python writes the database; agents read packets, write JSON and call `sed ai ingest`.
2. Metrics are exact and never read AI tables. AI-derived facts and tables are marked in the snapshot and can be
   excluded at render time (`--ai none`).
3. Every report renders from one frozen snapshot (sha256), so tables, charts and text agree.
4. Rule findings are deterministic and always published ("system-detected"); AI findings need human approval.
5. Real data, salts, ground truth, corporate templates and real mappings live only in `DATA_DIR`
   (`%LOCALAPPDATA%\sed\<profile>`); the repo is synthetic-only and guarded at commit time.

## Where to look

| Topic | Files |
|---|---|
| Import and mappings | `src/sed/ingest/`, `config/ops/mappings/`, `src/sed/ingest/targets.py` (ops targets) |
| Metrics and rule findings | `src/sed/metrics.py`, `src/sed/analytics.py`, `config/ops/risk_rules.yaml` |
| Reports and decks | `src/sed/reports/`, `src/sed/modules/ops/reports/`, `config/ops/reports/`, `templates/pptx/` |
| AI runs and review | `src/sed/ai/`, `src/sed/modules/ops/ai/`, `.claude/skills/sed-triage-batch/`, `.claude/workflows/sed-analyze.js` |
| API and dashboard | `src/sed/api/`, `src/sed/modules/ops/api.py`, `web/` |
| Tests | `tests/platform/` (core contracts), `tests/modules/<key>/`, `tests/fixtures/ops_profile.py` |
