# Automation and connectors

The weekly loop can run without manual exports and without anyone at the keyboard, up to the point where a person has
to decide. Nothing unattended approves, rejects or publishes anything: it pulls, imports, refreshes rule findings and
drafts AI results that wait in the review queue.

## Connectors (`sed pull`)

`sed pull servicenow|jira|sharepoint|confluence|sap` reads an API with a read-only account and writes the same files a
manual export would produce into the profile's inbox. `sed import --inbox` then runs unchanged: same mappings, PII
scrubbing, aliases, rule findings and reports.

| Connector | API | File written | Mapping it feeds |
|---|---|---|---|
| servicenow | Table API (`/api/now/table/<table>`), display values for choices and references, UTC dates converted to the mapping's time zone | `<prefix>_pull_<stamp>.csv` with field-name headers | `servicenow_incident` and the other ServiceNow mappings |
| jira | REST search with the source JQL | `jira_pull_<stamp>.csv` with "all fields" headers (repeated Labels, Sprint, Component/s, Fix Version/s) | `jira_issues` |
| sharepoint | Microsoft Graph list items (whole list, following `@odata.nextLink`) | `<prefix>_pull_<stamp>.csv` with the headers named in `columns` | registers such as `contracts_xlsx` |
| confluence | content search (CQL on the space) with storage-format bodies | `confluence_<space>_pull_<stamp>/` in the space HTML export layout | `confluence_pages` |
| sap | SAP Gateway OData v2 or v4 entity sets (`$select`, `$orderby`, `$top`/`$skip`, `$filter` on the change property) | `sap_charm_changes_pull_<source>_<stamp>.csv`, `sap_transport_imports_pull_...`, `sap_idocs_pull_...` | `sap_charm_changes`, `sap_charm_transports`, `sap_idocs` |

### Configure a connector
1. Copy `config/connectors.yaml` to `DATA_DIR\config\connectors.yaml` and fill in the real instance URL, the source
   filters and the name of the secret. Set `enabled: true`. Real values stay in the data folder, never in git.
2. Store the secret value in one of these places (never in a config file or a chat):
   - the Windows Credential Manager, service `sed`, user = the secret name (needs the `keyring` package);
   - the environment variable `SED_CREDENTIAL_<NAME>` (for example `SED_CREDENTIAL_SERVICENOW`);
   - the file `DATA_DIR\secret\connectors\<name>` (first line only; the folder is readable by your account only).
3. Check without calling the API: `uv run sed pull status --profile real --json` shows where each secret was found
   (never its value) and each source's watermark; `uv run sed doctor --profile real` checks the same.
4. Try a read: `uv run sed pull servicenow --dry-run --profile real --json` counts rows and writes nothing.

### How pulls behave
- **Read-only:** GET requests only, `https://` URLs only, a pause between calls, retries with backoff on 429 and 5xx
  (honouring `Retry-After`), `max_rows` per source and `max_pages` per pull.
- **Delta:** each delta source keeps a watermark (the newest update time pulled) in the database. The next pull starts
  at the watermark minus `overlap_minutes`; duplicates are harmless because the import upserts. `--since` overrides
  the watermark and `--full` ignores it. When `max_rows` stops a pull, the watermark only moves to the newest row
  written, so the next pull continues from there.
- **Refusals:** the synthetic profile refuses pulls (it must never mix with real data); disabled connectors, missing
  secrets and rejected credentials stop with exit code 4 and a message without URLs' query strings or secrets.
- **Record:** every pull is appended to `DATA_DIR\logs\pulls.jsonl` (connector, source, rows, file, watermark).
- **Fallback:** manual exports keep working; pulled and exported files can be imported side by side.

### SAP (`sed pull sap`)
SAP data comes through SAP Gateway OData services with a read-only technical user; Claude never connects to SAP and
there is no MCP server in the reporting path. Each source in the `sap` section names an entity set (`service_path`),
the mapping file it produces (`file_prefix`), which OData property feeds each CSV column (`columns`, keyed by the field
names the sap mappings read), the dates to convert (`datetime_columns`) and the change property for delta pulls
(`updated_property`). IDocs are read per production system: an IDoc source can name its own `base_url`, `user` and
`credential` and adds `constants: {system_id: <SID>}`. OData v2 `/Date(...)/` and v4 ISO dates are both accepted
(`odata_version`).

The service and property names in `config/connectors.yaml` are placeholders: which services exist (Solution Manager
ChaRM, Cloud ALM, or custom Gateway services for transports and IDoc status) must be confirmed with SAP Basis, together
with the technical user's authorisations, network access from this laptop and approval to run pulls on a schedule.
Until then the manual exports stay the source; after it, reconcile one week pulled against the same week exported by
hand (`sed metrics reconcile` and the SAP pages) before relying on the pulls.

## Weekly schedule (`sed schedule write`)

```bash
uv run sed schedule write --day MON --time 07:00 --profile real --json
```

writes two files into `DATA_DIR\automation\` and prints the command to register them. SED never registers the task
itself: read the script, then run the printed `schtasks /Create ...` command in your own terminal.

- `weekly.ps1`: `sed pull` for every enabled connector, `sed import --inbox`, `sed analytics refresh`, and with
  `--analyze` (the default) `claude -p "Run the sed-analyze workflow ..."` for triage, recurring issues and risks.
  Output goes to `DATA_DIR\logs\automation-<date>.log`.
- `weekly-task.xml`: a weekly trigger that runs only while you are logged on (Claude Code's sign-in and the salt file
  are available then) with least privilege and at most six hours.
- Remove it with the printed `schtasks /Delete ...` command.

After the run: review the drafts in the dashboard (`#/review`), draft the report sections with the `sed-report`
workflow, and build with `--ai approved --require-complete`.

## Review-rate trends

`uv run sed ai review-rates --profile real --json` (and the table on `#/runs`) shows, per skill version (skill hash)
and month, how reviewers decided: findings approved as written, edited, rejected or still open, and the mean sample
accuracy of label runs. A skill change that makes reviewers edit or reject more shows up as a new row next to the
previous version.
