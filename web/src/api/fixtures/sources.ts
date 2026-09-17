/**
 * Sources fixtures (W8): connectors with their readiness and sources, every export with the connector sources that can
 * pull it and its last import, plus upload and pull jobs. Fictional data only.
 */
import { ApiError } from '../client';
import type { PostBody, Schema } from '../types';
import { startJob } from './jobs';
import { AS_OF, isoAt } from './random';

type SourcesOut = Schema<'SourcesOut'>;
type Job = Schema<'JobOut'>;
type SourceRow = Schema<'ConnectorSourceRow'>;

const SUFFIXES = ['.csv', '.tsv', '.txt', '.xlsx', '.xlsm', '.xls', '.xlsb', '.ods', '.zip'];

function source(key: string, kind: SourceRow['kind'], mappings: string[], extra: Partial<SourceRow> = {}): SourceRow {
  return {
    source: key,
    kind,
    mappings,
    watermark: null,
    last_pull_at: null,
    last_rows: null,
    last_file: null,
    credential: null,
    credential_found_in: null,
    warning: null,
    ...extra,
  };
}

function file(
  module: string,
  mapping: string,
  target: string,
  loadMode: string,
  patterns: string[],
  connectorSources: string[],
  lastFile: string | null,
  hour = 7,
): Schema<'FileSourceRow'> {
  return {
    mapping,
    module,
    target,
    load_mode: loadMode,
    format: mapping === 'confluence_pages' ? 'confluence_html' : 'table',
    patterns,
    connector_sources: connectorSources,
    last_file: lastFile,
    last_status: lastFile ? 'completed' : null,
    last_imported_at: lastFile ? isoAt(AS_OF, hour, 12) : null,
  };
}

export function sources(): SourcesOut {
  return {
    profile: 'synthetic',
    data_class: 'synthetic',
    upload: { suffixes: SUFFIXES, max_bytes: 200 * 1024 * 1024, needs_confirmation: true },
    connectors: [
      {
        connector: 'servicenow',
        enabled: false,
        credential: 'servicenow',
        credential_found_in: null,
        can_pull: false,
        reason: 'not enabled in connectors.yaml',
        sources: [
          source('incident', 'table', ['servicenow_incident']),
          source('business_apps', 'table', ['cmdb_ci_business_app'], {
            warning: 'incremental pulls feed snapshot exports (cmdb_ci_business_app): set `snapshot: true` on the source',
          }),
        ],
      },
      {
        connector: 'jira',
        enabled: false,
        credential: 'jira',
        credential_found_in: null,
        can_pull: false,
        reason: 'not enabled in connectors.yaml',
        sources: [source('issues', 'query', ['jira_issues'])],
      },
      {
        connector: 'sharepoint',
        enabled: true,
        credential: 'graph',
        credential_found_in: 'credential manager',
        can_pull: true,
        reason: null,
        sources: [
          source('plans', 'library', ['delivery_plan'], {
            watermark: '2026-08-28T08:00:00+00:00',
            last_pull_at: isoAt(AS_OF, 6, 5),
            last_rows: 3,
            last_file: 'delivery_plan_PRJ-101_2026-08-28.xlsx',
          }),
          source('raid_log', 'list', ['delivery_raid']),
        ],
      },
      {
        connector: 'sap',
        enabled: false,
        credential: 'sap-solman',
        credential_found_in: null,
        can_pull: false,
        reason: 'not enabled in connectors.yaml',
        sources: [
          source('charm_changes', 'odata', ['sap_charm_changes']),
          source('idocs_prod', 'odata', ['sap_idocs'], { credential: 'sap-prod' }),
        ],
      },
    ],
    files: [
      file('ops', 'servicenow_incident', 'ticket', 'delta', ['incident_*.csv', 'incident_*.xlsx'], ['servicenow/incident'], 'incident_2026-08.csv', 6),
      file('ops', 'jira_issues', 'work_item', 'delta', ['jira_*.csv'], ['jira/issues'], 'jira_export_delivery.csv'),
      file('ops', 'contracts_xlsx', 'contract', 'full_snapshot', ['Contracts_Register*.xlsx'], [], 'Contracts_Register.xlsx'),
      file('ops', 'confluence_pages', 'doc_page', 'delta', ['confluence_*'], [], null),
      file('sap', 'sap_charm_changes', 'sap_change', 'delta', ['sap_charm_changes*.csv'], ['sap/charm_changes'], 'sap_charm_changes_2026-08.csv', 8),
      file('sap', 'sap_idocs', 'sap_idoc', 'delta', ['sap_idocs*.csv'], ['sap/idocs_prod'], 'sap_idocs_2026-08-31.csv', 8),
      file('delivery', 'delivery_plan', 'delivery_milestone', 'delta', ['delivery_plan_*.csv', 'delivery_plan_*.xlsx'], ['sharepoint/plans'], 'delivery_plan_PRJ-101_2026-08-28.csv', 9),
      file('delivery', 'delivery_raid', 'delivery_raid', 'full_snapshot', ['delivery_raid*.csv', 'raid_log*.xlsx'], ['sharepoint/raid_log'], 'delivery_raid.csv', 9),
    ],
  };
}

function summary(imported: number, errors: number, rows: number) {
  return { imported, errors, skipped: 0, rows_read: rows, rows_rejected: 0 };
}

/** `POST /api/imports/upload`, answered through `apiUpload` (a raw file body, so not one of the typed POST handlers). */
export function startUpload(name: string, size: number, syntheticOk: boolean): Job {
  if (!syntheticOk) {
    throw new ApiError(
      412,
      'precondition',
      'The synthetic profile imports generator files only. Upload real exports to the real profile, or confirm that this is a hand-made fictional fixture (synthetic_ok).',
    );
  }
  const clean = (name.split(/[\\/]/).pop() ?? name).replace(/[^A-Za-z0-9._-]+/g, '_');
  const suffix = clean.includes('.') ? `.${(clean.split('.').pop() ?? '').toLowerCase()}` : '';
  if (!SUFFIXES.includes(suffix)) {
    throw new ApiError(422, 'validation', `Unsupported file type '${suffix}' (allowed: ${SUFFIXES.join(', ')})`);
  }
  const info = { file: clean, bytes: size, kind: suffix === '.zip' ? 'folder' : 'file', members: 1 };
  const known = /^(incident|jira|delivery_|sap_|contracts|raid_log)/i.test(clean);
  return startJob('import_upload', { name, ...info }, (job) => {
    const files = known
      ? [{ file: clean, mapping: 'delivery_plan', status: 'completed', rows_read: 42, rows_rejected: 0 }]
      : [{ file: clean, status: 'error', error: { kind: 'validation', message: `No mapping matches file name '${clean}'` } }];
    Object.assign(job, {
      status: 'done',
      finished_at: isoAt(AS_OF, 12, 1),
      result: { upload: info, import: { files, skipped: [], summary: known ? summary(1, 0, 42) : summary(0, 1, 0) } },
    });
  });
}

export function startPull(connector: string, body: PostBody<'/api/sources/{connector}/pull'>): Job {
  const row = sources().connectors.find((c) => c.connector === connector);
  if (!row) throw new ApiError(422, 'validation', `Unknown connector '${connector}'`);
  if (!row.can_pull) throw new ApiError(412, 'precondition', `Connector '${connector}' cannot pull: ${row.reason ?? 'not ready'}`);
  return startJob('source_pull', { connector, ...body }, (job) => {
    Object.assign(job, {
      status: 'done',
      finished_at: isoAt(AS_OF, 12, 2),
      result: {
        pull: { connector, sources: [{ source: 'plans', rows: 1, files: ['delivery_plan_PRJ-101_2026-09-04.xlsx'] }, { source: 'raid_log', rows: 18, files: ['raid_log_pull_20260901T120200.csv'] }] },
        import: { files: [], skipped: [], summary: summary(2, 0, 21) },
      },
    });
  });
}
