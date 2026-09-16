# SAP module (`sed.modules.sap`)

Module #2 of SED: SAP application support on top of the ops module (`depends_on=("ops",)`). Phase S1 covers L3
support. ChaRM changes and transports (S2), IDoc health (S3) and SAP subcategories in AI triage (S4) follow.

- **Manifest:** `__init__.py` (`MODULE`). Everything else is reached through the lazy import references declared there.
- **SAP tickets stay ops tickets.** `scope.py` turns `config/sap/scope.yaml` into a ticket predicate
  (`Scope.ticket_sql`, passed as `metrics.Filters.scope_sql`). A ticket is in scope by SAP assignment group (which gives
  its area), by ServiceNow category, or by a kept custom field (`raw_keep`); the landscape comes from its application.
  Nothing is stored, so a scope change applies to all history at once.
- **Metrics:** `queries/l3.py` wraps the ops functions in `sed.metrics`, so SAP numbers follow the portfolio
  definitions (SLA source order, stale tickets excluded from backlog). Only group-level (area) breakdowns, never people.
- **Config:** `config/sap/` holds `scope.yaml`, `risk_rules.yaml`, `mappings/` and `reports/sap-weekly.yaml`, each
  overridable in `DATA_DIR\config\sap\`. Real SAP group names, categories and application ids live only there.
- **Rule findings:** kind `sap_backlog_risk` (`rules.py`), with stable keys `sap_backlog_risk:growth:<area>` and
  `sap_backlog_risk:aged:<area>`. They are refreshed per module by `sed.rule_findings`.
- **API:** `/api/sap/overview` and `/api/sap/l3` (`api.py`, `api_models.py`, `queries/api_views.py`), read-only.
- **Dashboard pages:** `web/src/modules/sap/` (`#/sap`, `#/sap/tickets`), with fixtures in `web/src/api/fixtures/sap.ts`.
- **Report:** `sap-weekly` (`reports/weekly.py`, `reports/markdown.py`) in xlsx, md and pptx.
- **Synthetic data:** `synth.py` writes SAP apps and incidents through the SAP mappings, in number blocks 6000–7999
  (ops uses 0000–3999 and 9000+). Planted patterns: SP1 EWM backlog growth and SP2 FI/CO month-end failures. Negative
  control: SN1, an SD surge with matching closures. SC1/SC2 are tickets marked only by category or custom field. Ground
  truth goes to `ground_truth/sap/`.
- **Tests:** `tests/modules/sap/`, with the shared fixtures `sap_profile` / `sap_profile_rw`
  (`tests/fixtures/sap_profile.py`: the ops profile plus the SAP data).
- **Boundary:** core code never imports this package directly (`tests/platform/test_core_boundaries.py`), and neither
  does ops.
