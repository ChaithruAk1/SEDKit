/**
 * The audit trail in fixtures mode: a handful of fictional entries covering each outcome, a change with its values
 * before and after, and an action whose outcome was never recorded. Filters work on the fixed list.
 */
import type { GetQuery, GetResponse } from '../types';

type Page = GetResponse<'/api/audit'>;
type Entry = Page['items'][number];

const AVERY = { actor: 'avery@example.com', actor_name: 'Avery Example', method: 'google', verified: true };
const WINDOWS = { actor: 'windows:synthetic-user', actor_name: 'synthetic-user', method: 'windows', verified: false };

function entry(seq: number, at: string, fields: Partial<Entry> & Pick<Entry, 'action' | 'action_label' | 'summary'>): Entry {
  return {
    seq,
    at,
    ...AVERY,
    channel: 'dashboard',
    outcome: 'done',
    target_type: null,
    target_id: null,
    detail: {},
    changes: null,
    correlation_id: null,
    open: false,
    prev_hash: `fixture-prev-${seq}`,
    entry_hash: `fixture-entry-${seq}`,
    ...fields,
  };
}

const ENTRIES: Entry[] = [
  entry(1, '2026-09-21T07:58:02Z', { ...WINDOWS, channel: 'command_line', action: 'serve_start', action_label: 'SED started', summary: 'SED started: the dashboard asks for a sign-in.', detail: { mode: 'sign_in', port: 8000 } }),
  entry(2, '2026-09-21T08:01:40Z', { action: 'sign_in', action_label: 'Sign-in', summary: 'Signed in with Google.', detail: { provider: 'google', expires_at: '2026-09-21T20:01:40Z' } }),
  entry(3, '2026-09-21T08:05:11Z', {
    action: 'download',
    action_label: 'Download',
    summary: 'Downloaded 128 tickets as a workbook.',
    target_type: 'ticket_workbook',
    target_id: 'tickets_20260921T080511Z_SYNTHETIC.xlsx',
    detail: { rows: 128, matching: 128, filters: { priority: [1, 2], sort: 'opened_desc' } },
  }),
  entry(4, '2026-09-21T09:30:00Z', { ...WINDOWS, channel: 'command_line', action: 'pull', action_label: 'Pull', outcome: 'started', summary: 'Pull from ServiceNow and import', correlation_id: 'fixture-pull', detail: { connector: 'servicenow', dashboard_signed_in: 'avery@example.com' } }),
  entry(5, '2026-09-21T09:31:12Z', { ...WINDOWS, channel: 'command_line', action: 'pull', action_label: 'Pull', summary: 'Pull from ServiceNow and import: 412 rows pulled; 1 file imported, 412 rows read.', correlation_id: 'fixture-pull', detail: { dashboard_signed_in: 'avery@example.com' } }),
  entry(6, '2026-09-21T10:02:45Z', { actor: 'stranger@example.org', actor_name: 'stranger@example.org', method: 'github', verified: true, action: 'sign_in', action_label: 'Sign-in', outcome: 'refused', summary: 'Sign-in with GitHub refused: stranger@example.org is not on the list of people allowed to use SED.' }),
  entry(7, '2026-09-21T11:15:00Z', {
    ...WINDOWS,
    channel: 'command_line',
    action: 'sign_in_settings',
    action_label: 'Sign-in settings changed',
    outcome: 'started',
    summary: 'Allowed jordan@example.com to sign in',
    correlation_id: 'fixture-settings',
    changes: [{ field: 'allow.emails', before: ['avery@example.com'], after: ['avery@example.com', 'jordan@example.com'] }],
  }),
  entry(8, '2026-09-21T11:15:01Z', { ...WINDOWS, channel: 'command_line', action: 'sign_in_settings', action_label: 'Sign-in settings changed', summary: 'Allowed jordan@example.com to sign in', correlation_id: 'fixture-settings' }),
  entry(9, '2026-09-21T12:40:00Z', { ...WINDOWS, channel: 'command_line', action: 'import', action_label: 'Import', outcome: 'started', summary: 'Import the files waiting in the inbox', correlation_id: 'fixture-import', open: true }),
  entry(10, '2026-09-21T13:05:20Z', { action: 'clear', action_label: 'Data cleared', outcome: 'failed', summary: 'Clear the data imported from Jira: failed (the database was busy)', correlation_id: 'fixture-clear' }),
];

const ACTIONS: Page['actions'] = [
  { key: 'sign_in', label: 'Sign-in' },
  { key: 'sign_out', label: 'Sign-out' },
  { key: 'serve_start', label: 'SED started' },
  { key: 'download', label: 'Download' },
  { key: 'pull', label: 'Pull' },
  { key: 'import', label: 'Import' },
  { key: 'clear', label: 'Data cleared' },
  { key: 'sign_in_settings', label: 'Sign-in settings changed' },
];

export function auditPage(query: GetQuery<'/api/audit'> | undefined): Page {
  const q = (query?.q ?? '').toLowerCase();
  const matching = ENTRIES.filter(
    (e) =>
      (!query?.person || e.actor === query.person) &&
      (!query?.action || e.action === query.action) &&
      (!query?.outcome || e.outcome === query.outcome) &&
      (!query?.correlation || e.correlation_id === query.correlation) &&
      (!query?.since || e.at.slice(0, 10) >= query.since) &&
      (!query?.until || e.at.slice(0, 10) <= query.until) &&
      (!q || `${e.summary} ${e.actor} ${e.actor_name}`.toLowerCase().includes(q)),
  ).reverse();
  const page = query?.page ?? 1;
  const size = query?.page_size ?? 100;
  return {
    items: matching.slice((page - 1) * size, page * size),
    total: matching.length,
    page,
    page_size: size,
    people: [
      { id: 'avery@example.com', name: 'Avery Example', verified: true },
      { id: 'stranger@example.org', name: 'stranger@example.org', verified: true },
      { id: 'windows:synthetic-user', name: 'synthetic-user', verified: false },
    ],
    actions: ACTIONS,
    integrity: { entries: ENTRIES.length, intact: true, first_break: null, first_at: ENTRIES[0]?.at ?? null, last_at: '2026-09-21T13:05:20Z' },
  };
}
