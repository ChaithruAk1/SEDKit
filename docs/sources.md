# Sources: every export by API or by file

Every source SED reads works two ways, and both end in the same import (mappings, PII scrubbing, hooks, data-quality
checks, rule findings):

- **By API**, when access is given: `uv run sed pull <connector> [--import] --profile real --json`, or **Pull now** on
  `#/data`. A connector writes files shaped like the manual export into the inbox; `--import` (and Pull now) import
  exactly those files. Connectors are read-only, run on the real profile only, and take their settings from
  `DATA_DIR\config\connectors.yaml` (template: `config/connectors.yaml`) and their secrets from the environment, the
  Windows Credential Manager or `DATA_DIR\secret\connectors\` (never from config or git).
- **By file**: export from the tool and either upload it on `#/data` ("Upload an export") or drop it into
  `DATA_DIR\inbox` and run `uv run sed import --inbox --profile real --json`. A Confluence space export can be uploaded
  as a `.zip`.

`uv run sed sources --profile <p> --json` (and the Sources card on `#/data`) lists every export below with the
connector sources configured for it, whether each connector can pull now, and the last import. A pulled file is named
like the export (`<file_prefix>_pull_<stamp>.csv`), so the mapping globs below decide which export it feeds.

## Choosing a connector source

| Connector | Source kind | Pulls | Suits |
|---|---|---|---|
| `servicenow` | table (`sources`) | rows changed since the watermark; with `snapshot: true` the whole table each time | delta exports; snapshot exports need `snapshot: true` |
| `jira` | JQL query (`sources`) | issues updated since the watermark | `jira_issues` |
| `confluence` | space (`sources`) | pages modified since the watermark, as an HTML export folder | `confluence_pages` |
| `sharepoint` | list (`sources`) | the whole list, with a column map | registers kept as SharePoint lists (full snapshots) |
| `sharepoint` | document library (`libraries`) | files matching `patterns` changed since the watermark, under their own names | workbooks and plan exports stored in SharePoint or OneDrive |
| `sap` | OData entity set (`sources`) | rows changed since `updated_property`, or the whole set without it | ChaRM changes, transport imports, IDocs |

An incremental source (a watermark) must not feed a snapshot export: the missing rows would be marked deleted. The
Sources overview warns about it; for ServiceNow set `snapshot: true`, otherwise use a list, a library or a file.

## Source matrix

### Module `ops`

| Export (mapping) | Load mode | File names | By API | By file |
|---|---|---|---|---|
| `servicenow_incident` | delta | `incident_*.csv`, `incident_*.xlsx` | `servicenow` table `incident`, `file_prefix: incident` | ServiceNow list export of incidents updated since the last export |
| `servicenow_incident_active` | active snapshot | `incident_active_*.csv/.xlsx` | `servicenow` table `incident`, `file_prefix: incident_active`, `filter: active=true`, `snapshot: true` | list export of all open incidents |
| `servicenow_sc_req_item` | delta | `sc_req_item_*.csv/.xlsx` | `servicenow` table `sc_req_item`, `file_prefix: sc_req_item` | list export of requested items |
| `servicenow_change_request` | delta | `change_request_*.csv/.xlsx` | `servicenow` table `change_request`, `file_prefix: change_request` | list export of changes |
| `servicenow_problem` | delta | `problem_*.csv/.xlsx`, `problem.csv` | `servicenow` table `problem`, `file_prefix: problem` | list export of problems |
| `servicenow_task_sla` | delta | `task_sla_*.csv/.xlsx` | `servicenow` table `task_sla`, `file_prefix: task_sla` | list export of task SLAs |
| `servicenow_user` | delta | `sys_user_*.csv/.xlsx`, `sys_user.csv` | `servicenow` table `sys_user`, `file_prefix: sys_user` | list export of users |
| `servicenow_group` | full snapshot | `sys_user_group*` | `servicenow` table `sys_user_group`, `file_prefix: sys_user_group`, `snapshot: true` | list export of assignment groups |
| `cmdb_ci_business_app` | full snapshot | `cmdb_ci_business_app*` | `servicenow` table `cmdb_ci_business_app`, `file_prefix: cmdb_ci_business_app`, `snapshot: true` | list export of business applications |
| `cmdb_rel_ci` | full snapshot | `cmdb_rel_ci*` | `servicenow` table `cmdb_rel_ci`, `file_prefix: cmdb_rel_ci`, `snapshot: true` | list export of CI relations |
| `jira_issues` | delta | `jira_*.csv` | `jira` query, `file_prefix: jira_pull` | Jira issue search, "Export CSV (all fields)" |
| `confluence_pages` | delta | `confluence_*` (folder) | `confluence` space | Confluence space export (HTML), unzipped or uploaded as `.zip` |
| `vendors_xlsx` | full snapshot | `vendor_master*.xlsx/.csv`, `vendors*.xlsx` | `sharepoint` library (`patterns: [vendor_master*.xlsx]`) or list (`file_prefix: vendor_master`) | the vendor master workbook |
| `contracts_xlsx` | full snapshot | `contracts_register*.xlsx`, `contracts*.xlsx/.csv` | `sharepoint` library or list (`file_prefix: contracts`) | the contracts register workbook |
| `licenses_xlsx` | full snapshot | `license_inventory*.xlsx/.csv` | `sharepoint` library or list (`file_prefix: license_inventory`) | the license inventory workbook |
| `license_usage_xlsx` | append snapshot | `license_usage_*.xlsx/.csv` | `sharepoint` library | the monthly license usage workbook |
| `costs_wide_xlsx` | delta | `it_cost_actuals*.xlsx` | `sharepoint` library | the cost actuals workbook (month columns) |
| `budget_csv` | append snapshot | `budget_*.csv/.xlsx` | `sharepoint` library | the budget file |

### Module `sap`

| Export (mapping) | Load mode | File names | By API | By file |
|---|---|---|---|---|
| `sap_incidents` | delta | `sap_incident_*.csv/.xlsx` | `servicenow` table `incident`, `file_prefix: sap_incident`, `filter` on the SAP L3 groups | ServiceNow list export of SAP incidents |
| `sap_incidents_active` | active snapshot | `sap_incident_active_*.csv/.xlsx` | `servicenow` table `incident`, `file_prefix: sap_incident_active`, `filter: active=true^<SAP groups>`, `snapshot: true` | list export of open SAP incidents |
| `sap_business_apps` | full snapshot | `sap_business_apps*` | `servicenow` table `cmdb_ci_business_app`, `file_prefix: sap_business_apps`, SAP filter, `snapshot: true` | list export of the SAP business applications |
| `sap_charm_changes` | delta | `sap_charm_changes_*.csv/.xlsx` | `sap` OData `ChangeDocuments`, `file_prefix: sap_charm_changes` | Solution Manager ChaRM change document export |
| `sap_charm_transports` | delta | `sap_transport_imports_*.csv/.xlsx` | `sap` OData `TransportImports`, `file_prefix: sap_transport_imports` | ChaRM or STMS transport import export |
| `sap_idocs` | delta | `sap_idocs_*.csv/.xlsx` | `sap` OData IDoc status per production system, `file_prefix: sap_idocs` | IDoc status export (one row per IDoc) |

### Module `delivery`

| Export (mapping) | Load mode | File names | By API | By file |
|---|---|---|---|---|
| `delivery_projects` | full snapshot | `delivery_projects*.csv/.xlsx`, `project_register*.xlsx/.csv` | `sharepoint` list (`file_prefix: project_register`) or library | the project register workbook |
| `delivery_plan` | delta | `delivery_plan_*.csv/.xlsx`, `project_plan_*.xlsx/.csv` | `sharepoint` library holding the plan exports (`patterns: [project_plan_*.xlsx]`) | MS Project "Export to Excel" or an Excel plan, one file per plan version |
| `delivery_raid` | full snapshot | `delivery_raid*.csv/.xlsx`, `raid_log*.xlsx/.csv` | `sharepoint` list (`file_prefix: raid_log`) or library | the RAID log workbook |

Jira issues and Confluence pages of delivery projects come through the ops `jira_issues` and `confluence_pages` exports.

## Example: delivery sources from SharePoint

```yaml
sharepoint:
  enabled: true
  base_url: https://graph.microsoft.com/v1.0
  auth: bearer
  credential: graph
  sources:
    - key: raid_log                 # a SharePoint list: the whole list each pull (full snapshot)
      site_id: <site id>
      list_id: <list id>
      file_prefix: raid_log         # raid_log_pull_<stamp>.csv feeds delivery_raid
      columns: {ID: Title, Project ID: ProjectId, Type: RaidType, Title: Summary, Severity: Severity,
                Status: Status, Raised On: RaisedOn, Due Date: DueDate, Closed On: ClosedOn}
  libraries:
    - key: plans                    # a document library folder: new or changed plan exports
      drive_id: <drive id>
      folder: Delivery/Plans
      patterns: [project_plan_*.xlsx]
      download_hosts: [.sharepoint.com]
```

Library downloads use Graph's pre-authenticated download links, without the connector's token, and only to hosts
listed in `download_hosts`; each file is capped at 200 MB and never overwrites a file already in the inbox.
