# Delivery module (`sed.modules.delivery`)

Module #3 of SED (M7 D1): delivery management of new business applications, on top of the ops module
(`depends_on=("ops",)`). Projects, plans and RAID items are this module's tables; Jira issues and Confluence pages stay
the ops `work_item` and `doc_page` rows, linked through the project register.

- **Manifest:** `__init__.py` (`MODULE`). Everything else is reached through the lazy import references declared there.
- **Tables** (migration `010_delivery.sql`, raw export values only):
  - `delivery_project`: the project register. Full snapshot; a project missing from the newest register is soft-deleted.
    `jira_keys_json` and `confluence_space` link a project to its Jira projects and Confluence space.
  - `delivery_milestone`: MS Project or Excel plan exports, keyed by project, task and plan status date. Every plan
    version is kept, so slips and replans stay measurable.
  - `delivery_raid`: the RAID log (risks, assumptions, issues, dependencies). Full snapshot.
- **Read models** (`queries/portfolio.py`, computed on read for an as-of date):
  - `milestones`: the latest plan version on or before the as-of date, with `slip_days` (forecast finish minus
    baseline) and `replans` (plan versions in which the forecast finish moved later).
  - `raid_items`: open or closed and days overdue at the as-of date.
  - `progress`: story points of the project's Jira stories: scope, done, growth over the window, velocity, forecast
    finish and a 12-week burn-up.
  - `documents`: requirement pages and ADRs in the Confluence space (by label or title prefix).
  - `health`: a computed RAG with its reasons, independent of the RAG in the register.
- **Config:** `config/delivery/` holds `risk_rules.yaml`, `mappings/` (`delivery_projects`, `delivery_plan`,
  `delivery_raid`) and `reports/delivery-status.yaml`, each overridable in `DATA_DIR\config\delivery\`. Real project
  names, sponsors and Jira or Confluence keys live only there and in the exports.
- **Rule findings** (`rules.py`, kind `delivery_risk`): `slip:<project>:<task>`, `raid_overdue:<raid id>`,
  `scope_growth:<project>` and `forecast_late:<project>`.
- **API:** `/api/delivery/portfolio` and `/api/delivery/projects/{project_id}` (`api.py`, `api_models.py`), read-only.
- **Dashboard pages:** `web/src/modules/delivery/` (`#/delivery`, `#/delivery/projects/:projectId`), with fixtures in
  `web/src/api/fixtures/delivery.ts`.
- **Report:** `delivery-status` (month; `reports/status.py`, `reports/markdown.py`) in xlsx, md and pptx. AI sections
  `headline`, `progress`, `risks_and_issues`, `decisions_needed` and `next_steps` are drafted by the generic
  `sed-draft-report` skill; per-project facts `delivery.project.<id>.*` give them exact tokens.
- **CLI:** `sed delivery portfolio [--as-of]` prints the portfolio with health and reasons.
- **Synthetic data:** `synth.py` writes the register, three plan versions (as of 15, 8 and 1 days before the data date),
  the RAID log, Jira stories and epics (`jira_export_delivery.csv`) and Confluence space exports. Planted patterns: DP1
  PRJ-101 double milestone slip, DP2 PRJ-103 overdue high risk, DP3 PRJ-102 scope growth, DP4 PRJ-102 late forecast;
  control PRJ-104. Ground truth goes to `ground_truth/delivery/`.
- **Tests:** `tests/modules/delivery/`, with the shared fixtures `delivery_profile` / `delivery_profile_rw`
  (`tests/fixtures/delivery_profile.py`: the ops profile plus the delivery data).
- **Boundary:** core code never imports this package directly (`tests/platform/test_core_boundaries.py`), and neither
  do ops or sap. SED never writes to Jira, Confluence or the plan tools.
