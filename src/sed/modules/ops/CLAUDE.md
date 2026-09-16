# Ops module (`sed.modules.ops`)

Module #1 of SED: ITSM tickets, SLA, backlog, costs, licenses, vendors and the recurring ops reports.

- **Manifest:** `__init__.py` (`MODULE`). Everything else is reached through the lazy import references declared there.
- **Config:** `config/ops/` (taxonomy, sla, risk_rules, vendor_groups, `mappings/`, `reports/`), overridable in
  `DATA_DIR\config\ops\`.
- **API:** routes at `/api/ops/...` (`api.py`), models in `api_models.py`, SQL in `queries/`.
- **Dashboard pages:** `web/src/modules/ops/`.
- **Reports:** `reports/<report>.py:build(req) -> SnapshotParts`, with specs in `config/ops/reports/<report>.yaml`.
- **AI:** skill `sed-triage-batch`, handler `ai/triage.py:TriageBatchHandler`, workflow `.claude/workflows/sed-analyze.js`.
  Other modules add packet fields and subcategories for their tickets through the extension point `ops.triage`
  (`ai/extensions.py`); ops never imports them.
- **Legacy code still owned here:** `sed/metrics.py`, `sed/analytics.py`, `sed/synth/`, `sed/ingest/targets.py`.
- **Tests:** `tests/modules/ops/...`, with shared fixtures `ops_profile` / `ops_profile_rw` (`tests/fixtures/ops_profile.py`).
- **Boundary:** core code never imports this package directly (`tests/platform/test_core_boundaries.py`).
