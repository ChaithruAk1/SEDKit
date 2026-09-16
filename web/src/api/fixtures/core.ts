/** Core (platform) API fixtures: meta, nav, modules, findings, imports, unmapped values, aliases and AI runs. */
import { ApiError } from '../client';
import type { GetQuery, PostBody, PostResponse, Schema } from '../types';
import { APPS, GROUPS, RUNS, VENDORS } from './catalog';
import { AS_OF, addDays, isoAt, lastMonths, lastQuarters, lastWeeks } from './random';

type Definitions = Schema<'MetaOut'>['definitions'];

export const DEFINITIONS: Definitions = {
  'inc.backlog': {
    unit: 'count',
    text: 'Open incidents at period end; tickets flagged stale_open (missing from the latest active export) are excluded.',
  },
  'inc.sla.pct': { unit: 'pct', text: 'Share of incidents resolved in the period that met their resolution SLA.' },
  'inc.mttr.median_h': { unit: 'hours', text: 'Median calendar hours from opened to resolved, incidents resolved in the period.' },
  'inc.p1p2.opened': { unit: 'count', text: 'Priority 1 and 2 incidents opened in the period.' },
  'cost.actual.ytd': { unit: 'eur', text: 'Actual spend year to date (base currency).' },
  'cost.budget.ytd': { unit: 'eur', text: 'Budget year to date from the latest budget version (base currency).' },
  'cost.variance.ytd_pct': { unit: 'pct', text: '(actual - budget) / budget, year to date.' },
  'renewals.90d.count': { unit: 'count', text: 'Active contracts ending within 90 days of as_of.' },
  'notice.30d.count': { unit: 'count', text: 'Active contracts whose notice deadline falls within 30 days of as_of.' },
  'license.idle_cost': { unit: 'eur', text: 'Sum over licenses of max(entitled - active_90d, 0) x unit cost.' },
  'review.queue.count': { unit: 'count', text: 'AI drafts and runs waiting for human review.' },
  'attention.count': { unit: 'count', text: 'Open incidents needing attention (P1/P2, SLA, aged, reopened, ping-pong, unassigned).' },
  'vendor.sla.delta_pp': { unit: 'pp', text: 'Average SLA % of the last 3 months minus the 3 months before.' },
  'license.utilization': { unit: 'pct', text: 'active_90d / entitled from the latest usage snapshot.' },
};

export function meta(): Schema<'MetaOut'> {
  return {
    data_class: 'synthetic',
    profile: 'synthetic',
    pii_mode: 'pseudonymize',
    schema_version: 3,
    sed_version: '0.2.0',
    reporting_tz: 'Europe/Paris',
    base_currency: 'EUR',
    as_of_default: AS_OF,
    periods: {
      weeks: lastWeeks(AS_OF, 12).reverse(),
      months: lastMonths(AS_OF, 18).reverse(),
      quarters: lastQuarters(AS_OF, 6).reverse(),
    },
    entities: {
      app: APPS.map((app) => ({ value: app.app_id, label: app.name })),
      vendor: VENDORS.map((vendor) => ({ value: vendor.vendor_id, label: vendor.name })),
      group: GROUPS.map((group) => ({ value: group, label: group })),
    },
    freshness: freshness(),
    definitions: DEFINITIONS,
    modules: [{ key: 'ops', title: 'Application operations' }],
  };
}

export function freshness(): Schema<'FreshnessRow'>[] {
  return [
    { mapping_name: 'servicenow_incident', last_import: isoAt(AS_OF, 7, 12), files: 18, latest_as_of: AS_OF },
    { mapping_name: 'servicenow_incident_active', last_import: isoAt(AS_OF, 7, 13), files: 6, latest_as_of: AS_OF },
    { mapping_name: 'servicenow_change_request', last_import: isoAt(AS_OF, 7, 14), files: 18, latest_as_of: AS_OF },
    { mapping_name: 'contracts_xlsx', last_import: isoAt(addDays(AS_OF, -12), 9, 2), files: 2, latest_as_of: addDays(AS_OF, -12) },
    { mapping_name: 'license_usage_xlsx', last_import: isoAt(addDays(AS_OF, -40), 9, 5), files: 3, latest_as_of: addDays(AS_OF, -40) },
    { mapping_name: 'jira_issues', last_import: null, files: null, latest_as_of: null },
  ];
}

export function nav(): Schema<'NavOut'> {
  return {
    items: [
      { id: 'ops.overview', module: 'ops', label: 'Overview', path: '/ops', order: 10, icon: 'layout-dashboard' },
      { id: 'ops.attention', module: 'ops', label: 'Needs attention', path: '/ops/attention', order: 20, icon: 'alert-triangle' },
      { id: 'ops.tickets', module: 'ops', label: 'Tickets', path: '/ops/tickets', order: 30, icon: 'ticket' },
      { id: 'ops.apps', module: 'ops', label: 'App 360', path: '/ops/apps', order: 40, icon: 'apps' },
      { id: 'ops.costs', module: 'ops', label: 'Costs & Contracts', path: '/ops/costs', order: 50, icon: 'currency-euro' },
      { id: 'sap.overview', module: 'sap', label: 'SAP', path: '/sap', order: 60, icon: 'building-factory' },
      { id: 'sap.tickets', module: 'sap', label: 'SAP L3 tickets', path: '/sap/tickets', order: 61, icon: 'ticket' },
      { id: 'sap.changes', module: 'sap', label: 'SAP changes', path: '/sap/changes', order: 62, icon: 'git-pull-request' },
      { id: 'sap.idocs', module: 'sap', label: 'SAP IDocs', path: '/sap/idocs', order: 63, icon: 'arrows-exchange' },
      { id: 'core.data', module: 'core', label: 'Data', path: '/data', order: 900, icon: 'database' },
    ],
  };
}

export function modules(): Schema<'ModulesOut'> {
  return {
    modules: [
      {
        key: 'ops',
        title: 'Application operations',
        description: 'ITSM tickets, SLA, backlog, costs, licenses, vendors and recurring ops reports',
        version: '1',
        enabled: true,
        reports: ['weekly', 'monthly', 'quarterly', 'vendor'],
        skills: ['sed-triage-batch'],
      },
      {
        key: 'sap',
        title: 'SAP application support',
        description: 'SAP L3 support by area and landscape, ChaRM changes and transports, IDoc health, SAP risks and the weekly review',
        version: '1',
        enabled: true,
        reports: ['sap-weekly'],
        skills: [],
      },
    ],
  };
}

const ORION = APPS[0]!;
const NIMBUS = APPS[4]!;
const LUMEN = APPS[APPS.length - 3]!;

const FINDINGS: Schema<'FindingOut'>[] = [
  {
    finding_id: 'rule-renewal-orion-erp',
    origin: 'rule',
    kind: 'renewal_risk',
    severity: 'high',
    title: `Notice deadline in 21 days: ${ORION.name} support contract`,
    subject_type: 'contract',
    subject_id: 'C-2031',
    status: 'active',
    body_md: 'Auto-renewing contract with **Nordwind Managed Services**. Notice must be given before the deadline.',
    evidence: [
      { fact_key: 'contract.days_to_notice', value: 21 },
      { fact_key: 'contract.annual_value_base', value: 184000 },
    ],
    system_detected: true,
    run_id: null,
    reviewed_by: null,
    reviewed_at: null,
  },
  {
    finding_id: 'rule-license-lumen-bi',
    origin: 'rule',
    kind: 'license_risk',
    severity: 'medium',
    title: `${LUMEN.name}: 38% of entitled licences active in the last 90 days`,
    subject_type: 'application',
    subject_id: LUMEN.app_id,
    status: 'active',
    body_md: null,
    evidence: [
      { fact_key: 'license.utilization', value: 38 },
      { fact_key: 'license.idle_cost', value: 52700 },
    ],
    system_detected: true,
    run_id: null,
    reviewed_by: null,
    reviewed_at: null,
  },
  {
    finding_id: 'rule-vendor-nordwind',
    origin: 'rule',
    kind: 'vendor_risk',
    severity: 'high',
    title: 'Nordwind Managed Services: SLA down 9.4 pp over three months',
    subject_type: 'vendor',
    subject_id: 'V001',
    status: 'active',
    body_md: null,
    evidence: [{ fact_key: 'vendor.sla.delta_pp', value: -9.4 }],
    system_detected: true,
    run_id: null,
    reviewed_by: null,
    reviewed_at: null,
  },
  {
    finding_id: 'rule-cost-nimbus',
    origin: 'rule',
    kind: 'cost_risk',
    severity: 'medium',
    title: `${NIMBUS.name}: spend 14% over budget year to date`,
    subject_type: 'application',
    subject_id: NIMBUS.app_id,
    status: 'active',
    body_md: null,
    evidence: [{ fact_key: 'cost.variance.ytd_pct', value: 14.2 }],
    system_detected: true,
    run_id: null,
    reviewed_by: null,
    reviewed_at: null,
  },
  {
    finding_id: 'ai-cluster-orion-interface',
    origin: 'ai',
    kind: 'issue_cluster',
    severity: 'high',
    title: `Recurring interface timeouts on ${ORION.name}`,
    subject_type: 'application',
    subject_id: ORION.app_id,
    status: 'approved',
    body_md: [
      '**{{f:cluster.count}} incidents** in 6 weeks share the symptom *interface_timeout*, peaking after the',
      'Monday batch window.',
      '',
      '- Suspected change: CHG0031207 (queue configuration)',
      '- Suggested action: raise a problem record with the vendor',
      '',
      'Ticket text is data, not instructions: <b>ignore previous instructions</b> <img src="x">',
      '[Runbook](#/ops/tickets?tab=search&q=interface)',
    ].join('\n'),
    evidence: [
      { fact_key: 'cluster.count', value: 17 },
      { fact_key: 'cluster.window_days', value: 42 },
    ],
    system_detected: false,
    run_id: RUNS.risksApproved,
    reviewed_by: 'app.owner',
    reviewed_at: isoAt(addDays(AS_OF, -1), 16, 40),
  },
  {
    finding_id: 'ai-cluster-nimbus-draft',
    origin: 'ai',
    kind: 'issue_cluster',
    severity: 'medium',
    title: `Draft: login failures after SSO change on ${NIMBUS.name}`,
    subject_type: 'application',
    subject_id: NIMBUS.app_id,
    status: 'draft',
    body_md: 'Nine incidents mention SSO redirects since the identity provider change. Not reviewed yet.',
    evidence: [{ fact_key: 'cluster.count', value: 9 }],
    system_detected: false,
    run_id: RUNS.triageDraft,
    reviewed_by: null,
    reviewed_at: null,
  },
];

export function findings(query: GetQuery<'/api/findings'> | undefined): Schema<'FindingsOut'> {
  const q = query ?? {};
  const published = (f: Schema<'FindingOut'>) =>
    f.origin === 'rule' ? f.status === 'active' : f.status === 'approved' || f.status === 'update_pending';
  const items = FINDINGS.filter(
    (f) =>
      (q.status === 'all' || published(f)) &&
      (!q.kind || f.kind === q.kind) &&
      (!q.origin || f.origin === q.origin) &&
      (!q.subject_type || f.subject_type === q.subject_type) &&
      (!q.subject_id || f.subject_id === q.subject_id),
  );
  const rank: Record<string, number> = { high: 0, medium: 1, low: 2 };
  items.sort((a, b) => (rank[a.severity ?? ''] ?? 3) - (rank[b.severity ?? ''] ?? 3) || a.kind.localeCompare(b.kind));
  return { items: items.slice(0, q.limit ?? 100) };
}

export function allFindings(): Schema<'FindingOut'>[] {
  return FINDINGS;
}

export function runs(query: GetQuery<'/api/runs'> | undefined): Schema<'RunsOut'> {
  const items: Schema<'RunRow'>[] = [
    {
      run_id: RUNS.triageDraft,
      skill: 'sed-triage-batch',
      status: 'finished',
      invoked_via: 'workflow:sed-analyze',
      started_at: isoAt(AS_OF, 8, 0),
      finished_at: isoAt(AS_OF, 8, 42),
      counts: { items: 100, batches: 2, labels: 100 },
      sample_accuracy: null,
      sample_ci_low: null,
      sample_ci_high: null,
      sample_n: null,
      reviewed_by: null,
      reviewed_at: null,
    },
    {
      run_id: RUNS.risksApproved,
      skill: 'sed-triage-batch',
      status: 'approved',
      invoked_via: 'workflow:sed-analyze',
      started_at: isoAt(addDays(AS_OF, -2), 9, 0),
      finished_at: isoAt(addDays(AS_OF, -2), 9, 51),
      counts: { items: 250, batches: 5, labels: 250 },
      sample_accuracy: 0.9,
      sample_ci_low: 0.82,
      sample_ci_high: 0.95,
      sample_n: 60,
      reviewed_by: 'app.owner',
      reviewed_at: isoAt(addDays(AS_OF, -1), 16, 35),
    },
    {
      run_id: RUNS.triageApproved,
      skill: 'sed-triage-batch',
      status: 'approved',
      invoked_via: 'workflow:sed-analyze',
      started_at: isoAt(addDays(AS_OF, -4), 10, 0),
      finished_at: isoAt(addDays(AS_OF, -4), 11, 5),
      counts: { items: 400, batches: 8, labels: 400 },
      sample_accuracy: 0.93,
      sample_ci_low: 0.86,
      sample_ci_high: 0.97,
      sample_n: 60,
      reviewed_by: 'app.owner',
      reviewed_at: isoAt(addDays(AS_OF, -3), 17, 5),
    },
  ];
  const q = query ?? {};
  return {
    items: items.filter((r) => (!q.skill || r.skill === q.skill) && (!q.status || r.status === q.status)).slice(0, q.limit ?? 50),
  };
}

export function imports(query: GetQuery<'/api/imports'> | undefined): Schema<'ImportsOut'> {
  const base = (batch_id: number, file_name: string, mapping_name: string, days: number) => ({
    batch_id,
    file_name,
    mapping_name,
    load_mode: 'upsert',
    status: 'completed',
    as_of: addDays(AS_OF, -days),
    rows_read: 0,
    rows_inserted: 0,
    rows_updated: 0,
    rows_unchanged: 0,
    rows_rejected: 0,
    rows_soft_deleted: 0,
    dq_severity: null as string | null,
    dq: {} as Record<string, unknown>,
    imported_at: isoAt(addDays(AS_OF, -days), 7, 10 + batch_id),
  });
  const items: Schema<'ImportRow'>[] = [
    { ...base(96, 'incident_active_2026-09-01.csv', 'servicenow_incident_active', 0), load_mode: 'active_snapshot', rows_read: 412, rows_unchanged: 398, rows_updated: 14, dq: { stale_open_flagged: 7 }, dq_severity: 'info' },
    { ...base(95, 'incident_2026-08-31.csv', 'servicenow_incident', 0), rows_read: 1206, rows_inserted: 188, rows_updated: 64, rows_unchanged: 950, rows_rejected: 4, dq_severity: 'warning', dq: { unknown_priority: 4, unmapped: { app: 3, group: 1 } } },
    { ...base(94, 'change_request_2026-08-31.csv', 'servicenow_change_request', 0), rows_read: 142, rows_inserted: 21, rows_unchanged: 121 },
    { ...base(93, 'contracts_2026-08.xlsx', 'contracts_xlsx', 12), load_mode: 'full_snapshot', rows_read: 46, rows_updated: 3, rows_unchanged: 42, rows_soft_deleted: 1, dq_severity: 'warning', dq: { soft_deleted_pct: 2.2 } },
    { ...base(92, 'costs_2026.xlsx', 'costs_wide_xlsx', 14), load_mode: 'append_snapshot', rows_read: 624, rows_inserted: 624 },
    { ...base(91, 'license_usage_2026-07.xlsx', 'license_usage_xlsx', 40), rows_read: 88, rows_inserted: 88 },
    { ...base(90, 'jira_export_broken.csv', 'jira_issues', 41), status: 'failed', dq_severity: 'error', dq: { error: 'Header row not found (expected Issue key, Summary)' } },
  ];
  return { items: items.slice(0, query?.limit ?? 50) };
}

interface UnmappedState {
  rows: Schema<'UnmappedRow'>[];
}

const unmappedState: UnmappedState = {
  rows: [
    { kind: 'app', raw_value: 'Orion ERP (Prod)', occurrences: 57, suggestion: 'Orion ERP', score: 93, first_batch_id: 71, last_batch_id: 95 },
    { kind: 'app', raw_value: 'Nimbus Warehouse', occurrences: 12, suggestion: 'Nimbus WMS', score: 81, first_batch_id: 88, last_batch_id: 95 },
    { kind: 'vendor', raw_value: 'NORDWIND MS GmbH', occurrences: 9, suggestion: 'Nordwind Managed Services', score: 88, first_batch_id: 93, last_batch_id: 93 },
    { kind: 'group', raw_value: 'NWD_ERP_L2', occurrences: 31, suggestion: 'NWD-ERP-L2', score: 90, first_batch_id: 80, last_batch_id: 95 },
    { kind: 'ci', raw_value: 'orion-erp-app01', occurrences: 22, suggestion: null, score: null, first_batch_id: 77, last_batch_id: 95 },
    { kind: 'jira_project', raw_value: 'LEDG', occurrences: 140, suggestion: 'Ledgerline Finance', score: 62, first_batch_id: 85, last_batch_id: 89 },
    { kind: 'confluence_space', raw_value: 'HRCORE', occurrences: 18, suggestion: 'Atlas HR Core', score: 70, first_batch_id: 86, last_batch_id: 86 },
  ],
};

export function unmapped(query: GetQuery<'/api/dq/unmapped'> | undefined): Schema<'UnmappedList'> {
  const q = query ?? {};
  return {
    items: unmappedState.rows.filter((r) => !q.kind || r.kind === q.kind).slice(0, q.limit ?? 500),
  };
}

const ENTITY_BY_KIND: Record<string, 'app' | 'vendor' | 'group'> = {
  app: 'app',
  vendor: 'vendor',
  group: 'group',
  ci: 'app',
  jira_project: 'app',
  jira_component: 'app',
  confluence_space: 'app',
};

function targetsFor(kind: string): Schema<'AliasTarget'>[] {
  const entity = ENTITY_BY_KIND[kind];
  if (!entity) throw new ApiError(422, 'validation', `kind must be one of ${Object.keys(ENTITY_BY_KIND).join(', ')}`);
  if (entity === 'app') return APPS.map((a) => ({ id: a.app_id, name: a.name }));
  if (entity === 'vendor') return VENDORS.map((v) => ({ id: v.vendor_id, name: v.name }));
  return GROUPS.map((g) => ({ id: g, name: g }));
}

export function aliasTargets(query: GetQuery<'/api/alias-targets'>): Schema<'AliasTargetsOut'> {
  const needle = (query.q ?? '').trim().toLowerCase();
  const items = targetsFor(query.kind)
    .filter((t) => !needle || t.name.toLowerCase().includes(needle) || t.id.toLowerCase().includes(needle))
    .sort((a, b) => a.name.localeCompare(b.name));
  return { items: items.slice(0, query.limit ?? 50) };
}

export function createAlias(body: PostBody<'/api/aliases'>): PostResponse<'/api/aliases'> {
  const raw = body.raw_value.trim();
  if (!raw || raw.length > 300) throw new ApiError(422, 'validation', 'raw_value must be 1..300 characters');
  const wanted = body.target.trim().toLowerCase();
  const target = targetsFor(body.kind).find((t) => t.id.toLowerCase() === wanted || t.name.toLowerCase() === wanted);
  if (!target) throw new ApiError(422, 'validation', `Unknown ${body.kind} target '${body.target}'`);
  const before = unmappedState.rows.length;
  const matched = unmappedState.rows.filter((r) => r.kind === body.kind && r.raw_value.toLowerCase() === raw.toLowerCase());
  unmappedState.rows = unmappedState.rows.filter((r) => !matched.includes(r));
  const occurrences = matched.reduce((sum, r) => sum + r.occurrences, 0);
  return {
    kind: body.kind,
    raw_value: raw,
    target_id: target.id,
    reresolved: { ticket: occurrences, contract: before === unmappedState.rows.length ? 0 : 1, license: 0, cost_line: 0 },
  };
}
