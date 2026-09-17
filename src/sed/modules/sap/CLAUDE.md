# SAP module (`sed.modules.sap`)

Module #2 of SED: SAP application support on top of the ops module (`depends_on=("ops",)`): L3 support (S1), ChaRM
changes with transports (S2), IDoc health (S3) and SAP subcategories in AI triage (S4). S5 connectivity is the core
connector `sed pull sap` (`sed.connectors`, SAP Gateway OData): it writes files named for this module's mappings, so
nothing here changes; see docs/automation.md.

- **Manifest:** `__init__.py` (`MODULE`). Everything else is reached through the lazy import references declared there.
- **SAP tickets stay ops tickets.** `scope.py` turns `config/sap/scope.yaml` into a ticket predicate
  (`Scope.ticket_sql`, passed as `metrics.Filters.scope_sql`). A ticket is in scope by SAP assignment group (which gives
  its area), by ServiceNow category, or by a kept custom field (`raw_keep`); the landscape comes from its application.
  Nothing is stored, so a scope change applies to all history at once.
- **Metrics:** `queries/l3.py` wraps the ops functions in `sed.metrics`, so SAP numbers follow the portfolio
  definitions (SLA source order, stale tickets excluded from backlog). Only group-level (area) breakdowns, never people.
- **Config:** `config/sap/` holds `scope.yaml` (areas, groups, landscapes, systems), `charm.yaml`, `idoc.yaml`,
  `taxonomy.yaml`, `risk_rules.yaml`,
  `mappings/` and `reports/sap-weekly.yaml`, each overridable in `DATA_DIR\config\sap\`. Real SAP group names,
  categories, application ids, system ids and ChaRM values live only there. Config lists replace the defaults; `key+`
  appends to them.
- **Changes (S2):** tables `sap_change`, `sap_change_status` and `sap_transport_import` (migration 007) hold raw export
  values; `charm.py` derives change type, stage, area and landscape on read, and `queries/changes.py` builds the change
  read models (status at a past moment from the history, latest import per transport and system, Jira links parsed
  from both sides). Ingest targets and the status-history hook are in `ingest.py`.
- **IDocs (S3):** tables `sap_idoc` and `sap_idoc_status` (migration 008) hold exported status codes; `idoc.py` groups
  them and maps message types to areas, and `queries/idocs.py` works on error episodes (first error to next processed
  status). Errors not processed within `thresholds.reprocess_grace_hours` are persistent; only those count towards
  growth and spikes after production imports.
- **AI triage (S4):** `triage.py` contributes to the ops extension point `ops.triage`
  (`sed.modules.ops.ai.extensions`): SAP tickets get a `sap` object (area and landscape labels) in their packet line and
  may take the SAP subcategories of `config/sap/taxonomy.yaml` (`taxonomy.py`, codes `sap_...`) under the portfolio
  categories. `sed ai start-run sed-triage-batch --only sap` triages SAP tickets only. `queries/ai_labels.py` builds the
  AI-assisted breakdown (approved labels, or drafts on request) for `/api/sap/l3` and `sap-weekly`. `evals.py` scores a
  run against the synthetic truth (`sed sap eval-triage <run>`, thresholds in `evals/thresholds.yaml`), scores only.
- **Rule findings** (`rules.py`, refreshed per module by `sed.rule_findings`):
  - `sap_backlog_risk:growth:<area>` and `sap_backlog_risk:aged:<area>`;
  - `sap_change_risk:stuck:<area>`, `sap_change_risk:urgent_ratio:<area>`,
    `sap_change_risk:failed_import:<transport>:<system>` and `sap_change_risk:waiting:<landscape>`;
  - `sap_idoc_risk:backlog:<system>:<type>`, `sap_idoc_risk:growth:<system>:<type>:<partner>`,
    `sap_idoc_risk:aged:<system>` and `sap_idoc_risk:spike:<system>:<change>`.
- **API:** `/api/sap/overview`, `/api/sap/l3`, `/api/sap/changes` and `/api/sap/idocs` (`api.py`, `api_models.py`,
  `queries/api_views.py`), read-only.
- **Dashboard pages:** `web/src/modules/sap/` (`#/sap`, `#/sap/tickets`, `#/sap/changes`, `#/sap/idocs`), with fixtures
  in `web/src/api/fixtures/sap.ts`.
- **Report:** `sap-weekly` (`reports/weekly.py`, `reports/markdown.py`) in xlsx, md and pptx.
- **Synthetic data:** `synth.py` writes SAP apps and incidents through the SAP mappings, in number blocks 6000–7999
  (ops uses 0000–3999 and 9000+). Planted patterns: SP1 EWM backlog growth and SP2 FI/CO month-end failures. Negative
  control: SN1, an SD surge with matching closures. SC1/SC2 are tickets marked only by category or custom field.
  `synth_changes.py` writes the ChaRM changes, transport imports and SAP Jira stories: CP1 failed urgent import with
  incidents, CP2 MM urgent creep, CP3 stuck in test, CP4 waiting for production, CP5 without Jira, and the CN1
  release-weekend control. `synth_idocs.py` writes daily IDoc exports for EP1 and HP1: IP1 INVOIC errors after the
  CP1 import, IP2 growing ORDERS errors for one partner, and the IN1 quick-reprocessing and IN2 cutover controls.
  Every SAP incident comes from a `Template` whose triage truth (category, SAP subcategory, misfiled_as) goes to
  `ticket_truth.csv`; keep the number of templates per area, because the RNG draws depend on it.
  Ground truth goes to `ground_truth/sap/`.
- **Tests:** `tests/modules/sap/`, with the shared fixtures `sap_profile` / `sap_profile_rw`
  (`tests/fixtures/sap_profile.py`: the ops profile plus the SAP data).
- **Boundary:** core code never imports this package directly (`tests/platform/test_core_boundaries.py`), and neither
  does ops.
