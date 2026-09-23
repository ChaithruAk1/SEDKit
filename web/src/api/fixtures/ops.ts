/** Ops module API fixtures (tickets, SLA, backlog, apps, costs, contracts, licences, vendors). */
import { ApiError } from '../client';
import type { GetQuery, Schema } from '../types';
import {
  AM_TAXONOMY,
  APPS,
  type FixtureApp,
  GROUPS,
  RUNS,
  SHORT_DESCRIPTIONS,
  SN_CATEGORIES,
  VENDORS,
  appById,
  vendorById,
} from './catalog';
import { allFindings, freshness } from './core';
import { AS_OF, addDays, int, isoAt, isoWeek, lastMonths, lastWeeks, pad, pick, rng, round } from './random';

type TicketDetail = Schema<'TicketDetail'>;
type TicketRow = Schema<'TicketRow'>;
type Common = NonNullable<GetQuery<'/api/ops/overview'>>;

const OPEN_STATES = ['New', 'In Progress', 'On Hold'];
const CLOSED_STATES = ['Resolved', 'Closed'];
const KINDS: TicketRow['kind'][] = ['incident', 'incident', 'incident', 'incident', 'sc_req_item', 'problem', 'change_request'];
const PREFIX: Record<string, string> = { incident: 'INC', sc_req_item: 'RITM', problem: 'PRB', change_request: 'CHG' };

function buildTickets(): TicketDetail[] {
  const r = rng(42);
  const out: TicketDetail[] = [];
  for (let i = 0; i < 240; i += 1) {
    const app = pick(r, APPS);
    const vendor = vendorById(app.vendor_id);
    const kind = pick(r, KINDS);
    const category = pick(r, Object.keys(AM_TAXONOMY));
    const subcategory = pick(r, AM_TAXONOMY[category] ?? ['other']);
    const openedDaysAgo = int(r, 0, 150);
    const opened = addDays(AS_OF, -openedDaysAgo);
    const isOpen = r() < (openedDaysAgo < 20 ? 0.55 : 0.12);
    const resolveHours = int(r, 2, 400);
    const resolved = isOpen ? null : new Date(Date.parse(isoAt(opened, 8)) + resolveHours * 3_600_000).toISOString();
    const priority = r() < 0.08 ? 1 : r() < 0.15 ? 2 : int(r, 3, 4);
    const labelled = kind === 'incident' && r() < 0.7;
    const draft = labelled && openedDaysAgo < 3;
    const number = `${PREFIX[kind] ?? 'TKT'}${pad(10000 + i * 37, 7)}`;
    const reopen = r() < 0.07 ? 1 : 0;
    const reassign = r() < 0.1 ? int(r, 4, 7) : int(r, 0, 2);
    const shortDescription = pick(r, SHORT_DESCRIPTIONS[category] ?? ['Unexpected error']);
    const snCategory = pick(r, SN_CATEGORIES);
    const labels: TicketDetail['labels'] = labelled
      ? [
          {
            stage: 'triage',
            run_id: draft ? RUNS.triageDraft : RUNS.triageApproved,
            run_status: draft ? 'finished' : 'approved',
            am_category: category,
            am_subcategory: subcategory,
            symptom_key: `${app.name.split(' ')[0]?.toLowerCase() ?? 'app'}.${subcategory}`,
            misfiled_as: snCategory === 'Network' && category !== 'infrastructure' ? snCategory : null,
            confidence: round(0.55 + r() * 0.44, 2),
            rationale: `Description mentions ${subcategory.replace(/_/g, ' ')} on ${app.name}.`,
          },
        ]
      : [];
    const label = labels[0];
    out.push({
      ticket_id: `${kind}:${number}`,
      number,
      kind,
      priority,
      state: isOpen ? pick(r, OPEN_STATES) : pick(r, CLOSED_STATES),
      is_open: isOpen,
      stale_open: isOpen && openedDaysAgo > 90 && r() < 0.5,
      app_id: app.app_id,
      app_name: app.name,
      vendor_name: vendor?.name ?? null,
      assignment_group: r() < 0.06 ? null : pick(r, vendor?.groups ?? GROUPS),
      opened_at: isoAt(opened, int(r, 6, 19), int(r, 0, 59)),
      resolved_at: resolved,
      short_description: shortDescription,
      sn_category: snCategory,
      am_category: label?.am_category ?? null,
      am_subcategory: label?.am_subcategory ?? null,
      label_confidence: label?.confidence ?? null,
      label_run_id: label?.run_id ?? null,
      description: `${shortDescription}.\n\nUser reports the problem started this morning. Steps tried: restart, cache clear.`,
      close_code: isOpen ? null : pick(r, ['Solved (Permanently)', 'Solved (Work Around)', 'Not Solved (Not Reproducible)']),
      close_notes: isOpen ? null : 'Configuration corrected and verified with the requester.',
      closed_at: resolved,
      sys_updated_on: resolved ?? isoAt(addDays(AS_OF, -int(r, 0, 5)), 12),
      caller_pid: `P-caller${pad(int(r, 1, 400), 4)}`,
      assigned_to_pid: r() < 0.1 ? null : `P-agent${pad(int(r, 1, 60), 4)}`,
      cmdb_ci_raw: `${app.name.toLowerCase().replace(/[^a-z]+/g, '-')}-app0${int(r, 1, 3)}`,
      business_service_raw: app.name,
      problem_id: r() < 0.05 ? `problem:PRB${pad(900 + i, 7)}` : null,
      caused_by: r() < 0.04 ? `CHG${pad(31000 + i, 7)}` : null,
      parent_incident: null,
      reassignment_count: reassign,
      reopen_count: reopen,
      made_sla: isOpen ? null : resolveHours < (priority <= 2 ? 8 : 120),
      // Columns a real export carries that SED has no field for, kept by the mapping's `raw_keep`.
      export_fields: {
        Channel: pick(r, ['Self-service', 'Phone', 'Email', 'Chat']),
        'Incident state': isOpen ? 'In Progress' : 'Closed',
        'Support Level': pick(r, ['L1', 'L2', 'L3']),
        'Initial Assignment Group': 'Service Desk',
      },
      labels,
    });
  }
  return out.sort((a, b) => (b.opened_at ?? '').localeCompare(a.opened_at ?? ''));
}

const TICKETS = buildTickets();

function toRow(t: TicketDetail): TicketRow {
  return {
    ticket_id: t.ticket_id,
    number: t.number,
    kind: t.kind,
    priority: t.priority,
    state: t.state,
    is_open: t.is_open,
    stale_open: t.stale_open,
    app_id: t.app_id,
    app_name: t.app_name,
    vendor_name: t.vendor_name,
    assignment_group: t.assignment_group,
    opened_at: t.opened_at,
    resolved_at: t.resolved_at,
    short_description: t.short_description,
    sn_category: t.sn_category,
    am_category: t.am_category,
    am_subcategory: t.am_subcategory,
    label_confidence: t.label_confidence,
    label_run_id: t.label_run_id,
  };
}

function scopedApps(q: Common | undefined): FixtureApp[] {
  const apps = q?.app ?? [];
  return APPS.filter(
    (a) =>
      (apps.length === 0 || apps.includes(a.app_id)) &&
      (!q?.family || a.family === q.family) &&
      (!q?.vendor || a.vendor_id === q.vendor),
  );
}

function scopedTickets(q: Common | undefined): TicketDetail[] {
  const ids = new Set(scopedApps(q).map((a) => a.app_id));
  return TICKETS.filter((t) => t.app_id !== null && ids.has(t.app_id) && (!q?.group || t.assignment_group === q.group));
}

function severityRank(severity: string | null): number {
  return { critical: 0, high: 1, medium: 2, low: 3 }[severity ?? ''] ?? 4;
}

function asOf(q: Common | undefined): string {
  return q?.as_of ?? AS_OF;
}

function ageDays(t: TicketDetail, at: string): number {
  return round((Date.parse(`${at}T00:00:00Z`) - Date.parse(t.opened_at ?? `${at}T00:00:00Z`)) / 86_400_000, 1);
}

// ---------------------------------------------------------------------------
// overview and attention
// ---------------------------------------------------------------------------

function attentionReasons(t: TicketDetail, at: string): string[] {
  if (!t.is_open || t.kind !== 'incident' || t.stale_open) return [];
  const reasons: string[] = [];
  const age = ageDays(t, at);
  if (t.priority !== null && t.priority <= 2) reasons.push(`P${t.priority} open`);
  if (t.priority !== null && t.priority <= 2 && age > 1) reasons.push('past SLA target');
  else if (t.priority === 3 && age > 1.3) reasons.push('near SLA target');
  if (age > 30) reasons.push('aged > 30d');
  if ((t.reopen_count ?? 0) > 0) reasons.push('reopened');
  if ((t.reassignment_count ?? 0) >= 4) reasons.push('ping-pong reassignments');
  if (!t.assigned_to_pid) reasons.push('unassigned');
  return reasons;
}

export function attention(q: NonNullable<GetQuery<'/api/ops/attention'>> | undefined): Schema<'AttentionOut'> {
  const at = asOf(q);
  const rows = scopedTickets(q)
    .map((t) => ({ t, reasons: attentionReasons(t, at) }))
    .filter((x) => x.reasons.length > 0);
  const byReason: Record<string, number> = {};
  for (const { reasons } of rows) {
    for (const reason of reasons) {
      const key = reason.startsWith('P') ? (reason.split(' ')[0] ?? reason) : reason;
      byReason[key] = (byReason[key] ?? 0) + 1;
    }
  }
  return {
    as_of: at,
    data_as_of_last_import: AS_OF,
    count: rows.length,
    by_reason: byReason,
    items: rows.slice(0, q?.limit ?? 200).map(({ t, reasons }) => ({
      ticket_id: t.ticket_id,
      number: t.number,
      priority: t.priority,
      app: t.app_name,
      state: t.state,
      assignment_group: t.assignment_group,
      assigned_to_pid: t.assigned_to_pid,
      opened_at: t.opened_at,
      age_days: ageDays(t, at),
      reasons,
      short_description: t.short_description,
    })),
  };
}

export function overview(q: Common | undefined): Schema<'OpsOverview'> {
  const at = asOf(q);
  const tickets = scopedTickets(q).filter((t) => t.kind === 'incident');
  const backlog = tickets.filter((t) => t.is_open && !t.stale_open).length;
  const apps = scopedApps(q);
  const actual = round(apps.reduce((s, a) => s + a.annual_license_cost_base * 0.71, 0), 0);
  const budget = round(apps.reduce((s, a) => s + a.annual_license_cost_base * 0.66, 0), 0);
  const period = q?.period ?? lastWeeks(at, 1)[0] ?? isoWeek(at);
  const kpi = (key: string, label: string, value: number | null, unit: string, compare: number | null = null) => ({
    key,
    label,
    value,
    unit,
    compare,
    delta: value !== null && compare !== null ? round(value - compare, 2) : null,
    definition: null,
  });
  return {
    period,
    as_of: at,
    data_as_of_last_import: AS_OF,
    kpis: [
      kpi('inc.backlog', 'Incident backlog', backlog, 'count', backlog + 6),
      kpi('inc.sla.pct', 'SLA met', 86.4, 'pct', 89.1),
      kpi('inc.mttr.median_h', 'MTTR (median)', 18.5, 'hours', 21.0),
      kpi('inc.p1p2.opened', 'P1/P2 opened', tickets.filter((t) => (t.priority ?? 9) <= 2).length % 9, 'count', 5),
      kpi('cost.actual.ytd', 'Spend YTD', actual, 'eur'),
      kpi('cost.budget.ytd', 'Budget YTD', budget, 'eur'),
      kpi('cost.variance.ytd_pct', 'Variance YTD', budget ? round((100 * (actual - budget)) / budget, 2) : null, 'pct'),
      kpi('renewals.90d.count', 'Renewals in 90 days', 6, 'count'),
      kpi('notice.30d.count', 'Notice deadlines in 30 days', 2, 'count'),
      kpi('license.idle_cost', 'Idle licence cost', 131450, 'eur'),
      kpi('review.queue.count', 'Review queue', 3, 'count'),
    ],
    attention_count: attention({ ...q, limit: 1000 }).count,
    stale_open: tickets.filter((t) => t.stale_open).length,
    top_risks: allFindings()
      .filter((f) => (f.origin === 'rule' ? f.status === 'active' : f.status === 'approved') && f.kind !== 'report_section')
      .sort((a, b) => severityRank(a.severity) - severityRank(b.severity))
      .slice(0, 5),
    review_queue_count: 3,
    freshness: freshness(),
  };
}

export function filters(): Schema<'OpsFiltersOut'> {
  return {
    families: [...new Set(APPS.map((a) => a.family))].sort(),
    apps: APPS.map((a) => ({ app_id: a.app_id, name: a.name, family: a.family })),
  };
}

// ---------------------------------------------------------------------------
// tickets
// ---------------------------------------------------------------------------

function matchesText(t: TicketDetail, q: string): boolean {
  const tokens = q.toLowerCase().split(/\s+/).filter(Boolean).slice(0, 10);
  const haystack = `${t.short_description ?? ''} ${t.description ?? ''} ${t.number}`.toLowerCase();
  return tokens.every((token) => haystack.includes(token.replace(/\*$/, '').replace(/"/g, '')));
}

export function tickets(q: NonNullable<GetQuery<'/api/ops/tickets'>> | undefined): Schema<'TicketPage'> {
  const page = q?.page ?? 1;
  const pageSize = q?.page_size ?? 50;
  if (q?.q && q.q.length > 200) throw new ApiError(422, 'validation', 'q: at most 200 characters');
  const priorities = q?.priority ?? [];
  const items = scopedTickets(q).filter(
    (t) =>
      (!q?.q || matchesText(t, q.q)) &&
      (!q?.kind || t.kind === q.kind) &&
      (priorities.length === 0 || (t.priority !== null && priorities.includes(t.priority))) &&
      (!q?.state || t.state === q.state) &&
      (q?.open === undefined || q.open === null || t.is_open === q.open) &&
      (q?.stale === undefined || q.stale === null || t.stale_open === q.stale) &&
      (!q?.sn_category || t.sn_category === q.sn_category) &&
      (!q?.am_category || t.am_category === q.am_category),
  );
  const sort = q?.sort ?? 'opened_desc';
  items.sort((a, b) => {
    if (sort === 'opened_asc') return (a.opened_at ?? '').localeCompare(b.opened_at ?? '');
    if (sort === 'priority') return (a.priority ?? 9) - (b.priority ?? 9) || (b.opened_at ?? '').localeCompare(a.opened_at ?? '');
    if (sort === 'updated_desc') return (b.sys_updated_on ?? '').localeCompare(a.sys_updated_on ?? '');
    return (b.opened_at ?? '').localeCompare(a.opened_at ?? '');
  });
  return {
    page,
    page_size: pageSize,
    total: items.length,
    items: items.slice((page - 1) * pageSize, page * pageSize).map(toRow),
  };
}

export function ticketDetail(ticketId: string): TicketDetail {
  const ticket = TICKETS.find((t) => t.ticket_id === ticketId);
  if (!ticket) throw new ApiError(404, 'not_found', `Unknown ticket '${ticketId}'`);
  return ticket;
}

function periodsFor(granularity: 'week' | 'month', n: number, at: string): string[] {
  return granularity === 'week' ? lastWeeks(at, n) : lastMonths(at, n);
}

function wave(seed: number, index: number, base: number, amplitude: number): number {
  const r = rng(seed * 1000 + index);
  return Math.max(0, Math.round(base + Math.sin(index / 2) * amplitude + (r() - 0.5) * amplitude));
}

export function volumes(q: NonNullable<GetQuery<'/api/ops/tickets/volumes'>> | undefined): Schema<'VolumesOut'> {
  const granularity = q?.granularity ?? 'week';
  const scale = (granularity === 'week' ? 1 : 4.3) * Math.max(scopedApps(q).length / APPS.length, 0.05);
  const items = periodsFor(granularity, q?.n ?? 12, asOf(q)).map((period, i) => {
    const opened = Math.round(wave(1, i, 64, 12) * scale);
    const resolved = Math.round(wave(2, i, 61, 14) * scale);
    return { period, opened, resolved, net: opened - resolved };
  });
  return { granularity, items };
}

export function sla(q: NonNullable<GetQuery<'/api/ops/tickets/sla'>> | undefined): Schema<'SlaOut'> {
  const granularity = q?.granularity ?? 'week';
  const items = periodsFor(granularity, q?.n ?? 12, asOf(q)).map((period, i) => {
    const total = wave(3, i, 58, 10) * (granularity === 'week' ? 1 : 4);
    const met = Math.min(total, Math.round(total * (0.8 + ((i * 7) % 10) / 70)));
    return { period, total, met, pct: total ? round((100 * met) / total, 2) : null };
  });
  return {
    granularity,
    sla_source: 'task_sla',
    items,
    by_priority: [
      { priority: 'P1', met: 5, total: 7, pct: 71.43 },
      { priority: 'P2', met: 31, total: 38, pct: 81.58 },
      { priority: 'P3', met: 402, total: 455, pct: 88.35 },
      { priority: 'P4', met: 211, total: 226, pct: 93.36 },
    ],
  };
}

export function mttr(q: NonNullable<GetQuery<'/api/ops/tickets/mttr'>> | undefined): Schema<'MttrOut'> {
  const granularity = q?.granularity ?? 'week';
  const items = periodsFor(granularity, q?.n ?? 12, asOf(q)).map((period, i) => {
    const median = round(14 + Math.sin(i / 3) * 5 + ((i * 13) % 7), 1);
    return {
      period,
      count: wave(4, i, 55, 9) * (granularity === 'week' ? 1 : 4),
      median_h: median,
      mean_h: round(median * 1.6, 1),
      p90_h: round(median * 4.2, 1),
    };
  });
  return { granularity, items };
}

export function backlog(q: Common | undefined): Schema<'BacklogOut'> {
  const at = asOf(q);
  const open = scopedTickets(q).filter((t) => t.kind === 'incident' && t.is_open);
  const live = open.filter((t) => !t.stale_open);
  const bucket = (t: TicketDetail) => {
    const age = ageDays(t, at);
    return age <= 7 ? 'd0_7' : age <= 30 ? 'd8_30' : age <= 90 ? 'd31_90' : 'd90p';
  };
  const empty = () => ({ d0_7: 0, d8_30: 0, d31_90: 0, d90p: 0 });
  const aging = empty();
  const groups = new Map<string | null, ReturnType<typeof empty> & { total: number }>();
  for (const t of live) {
    const b = bucket(t);
    aging[b] += 1;
    const row = groups.get(t.assignment_group) ?? { ...empty(), total: 0 };
    row[b] += 1;
    row.total += 1;
    groups.set(t.assignment_group, row);
  }
  const weeks = lastWeeks(at, 8);
  return {
    at,
    total: live.length,
    stale_excluded: open.length - live.length,
    aging,
    by_group: [...groups.entries()]
      .map(([group, row]) => ({ group, ...row }))
      .sort((a, b) => b.total - a.total),
    flow: weeks.flatMap((period, i) =>
      ['NWD-ERP-L2', 'KEEL-MFG-L2', null].map((group, g) => ({
        period,
        group,
        arrived: wave(10 + g, i, 14 - g * 3, 4),
        closed: wave(20 + g, i, 13 - g * 3, 5),
      })),
    ),
  };
}

// ---------------------------------------------------------------------------
// apps
// ---------------------------------------------------------------------------

function appRow(app: FixtureApp): Schema<'AppRow'> {
  const r = rng(app.app_id.length * 97 + Number(app.app_id.slice(3)) % 1000);
  const own = TICKETS.filter((t) => t.app_id === app.app_id && t.kind === 'incident');
  const risks = allFindings().filter((f) => f.subject_id === app.app_id && (f.status === 'active' || f.status === 'approved'));
  return {
    app_id: app.app_id,
    name: app.name,
    family: app.family,
    criticality: app.criticality,
    lifecycle: app.lifecycle,
    primary_vendor: vendorById(app.vendor_id)?.name ?? null,
    annual_license_cost_base: app.annual_license_cost_base,
    cost_ytd_base: round(app.annual_license_cost_base * (0.55 + r() * 0.3), 0),
    incidents_per_month_3m: round(own.length / 5 + r() * 3, 1),
    sla_pct_3m: own.length ? round(78 + r() * 21, 1) : null,
    license_utilization: app.lifecycle === 'pilot' ? null : round(0.35 + r() * 0.6, 4),
    open_risks: risks.length,
  };
}

export function apps(q: Common | undefined): Schema<'AppsOut'> {
  return { items: scopedApps(q).map(appRow) };
}

export function renewals(q: NonNullable<GetQuery<'/api/ops/contracts/renewals'>> | undefined): Schema<'RenewalsOut'> {
  const days = q?.days ?? 180;
  const ids = new Set(scopedApps(q).map((a) => a.app_id));
  const items = CONTRACTS.filter((c) => ids.has(c.app_id) && c.row.days_to_end !== null)
    .filter((c) => (c.row.days_to_end ?? 9999) <= days || (c.row.days_to_notice ?? 9999) <= days)
    .map((c) => c.row);
  return { as_of: asOf(q), days, items };
}

const CONTRACTS: { app_id: string; row: Schema<'RenewalRow'> }[] = APPS.slice(0, 14).map((app, i) => {
  const daysToEnd = [45, 62, 75, 88, 101, 116, 140, 170, 210, 260, 320, 400, 30, 150][i] ?? 365;
  const notice = [30, 60, 90][i % 3] ?? 60;
  return {
    app_id: app.app_id,
    row: {
      contract_id: `C-${2031 + i}`,
      contract_number: `CTR-2024-${pad(110 + i * 3, 4)}`,
      vendor: vendorById(app.vendor_id)?.name ?? null,
      app: app.name,
      product: `${app.name} ${i % 2 ? 'subscription' : 'support & maintenance'}`,
      end_date: addDays(AS_OF, daysToEnd),
      days_to_end: daysToEnd,
      notice_deadline: addDays(AS_OF, daysToEnd - notice),
      days_to_notice: daysToEnd - notice,
      auto_renew: i % 3 !== 1,
      renewal_status: i === 12 ? 'under_negotiation' : 'active',
      annual_value_base: 36000 + ((i * 7919) % 17) * 12000,
    },
  };
});

const LICENSES: { app_id: string; row: Schema<'LicenseRow'> }[] = APPS.filter((a) => a.lifecycle !== 'pilot')
  .slice(0, 16)
  .map((app, i) => {
    const entitled = 50 + ((i * 37) % 9) * 50;
    const assigned = Math.round(entitled * (0.7 + ((i * 3) % 4) / 10));
    const active = Math.round(entitled * (0.3 + ((i * 11) % 7) / 10));
    const unit = 120 + ((i * 13) % 6) * 45;
    return {
      app_id: app.app_id,
      row: {
        license_id: `L-${pad(500 + i, 4)}`,
        app: app.name,
        vendor: vendorById(app.vendor_id)?.name ?? null,
        product: `${app.name} named user`,
        entitled_qty: entitled,
        assigned_qty: assigned,
        active_qty_90d: i === 5 ? null : Math.min(active, entitled),
        utilization: i === 5 ? null : round(Math.min(active, entitled) / entitled, 4),
        assigned_ratio: round(assigned / entitled, 4),
        unit_cost_base: unit,
        idle_cost_base: i === 5 ? null : round(Math.max(entitled - active, 0) * unit, 2),
      },
    };
  });

export function licenses(q: Common | undefined): Schema<'LicensesOut'> {
  const ids = new Set(scopedApps(q).map((a) => a.app_id));
  const items = LICENSES.filter((l) => ids.has(l.app_id)).map((l) => l.row);
  return { as_of: asOf(q), idle_cost_total: round(items.reduce((s, l) => s + (l.idle_cost_base ?? 0), 0), 2), items };
}

const COST_CATEGORIES = ['licence', 'support', 'hosting', 'project'];

export function costs(q: NonNullable<GetQuery<'/api/ops/costs'>> | undefined): Schema<'CostsOut'> {
  const groupBy = q?.group_by ?? 'app';
  const months = lastMonths(asOf(q), q?.months ?? 3);
  const factor = months.length / 12;
  const scoped = scopedApps(q);
  const lines = scoped.flatMap((app, i) =>
    COST_CATEGORIES.map((category, c) => {
      const budget = round(app.annual_license_cost_base * factor * [0.5, 0.25, 0.2, 0.05][c]!, 0);
      const drift = 0.82 + (((i + 1) * (c + 3) * 7) % 40) / 100;
      return { app, category, budget, actual: round(budget * drift, 0) };
    }),
  );
  const keyOf = (line: (typeof lines)[number]): [string, string] => {
    if (groupBy === 'vendor') {
      const vendor = vendorById(line.app.vendor_id);
      return [vendor?.vendor_id ?? 'unknown', vendor?.name ?? 'Unknown vendor'];
    }
    if (groupBy === 'category') return [line.category, line.category];
    if (groupBy === 'app_category') return [`${line.app.app_id}/${line.category}`, `${line.app.name} / ${line.category}`];
    return [line.app.app_id, line.app.name];
  };
  const rows = new Map<string, Schema<'CostRow'>>();
  for (const line of lines) {
    const [key, label] = keyOf(line);
    const row = rows.get(key) ?? { key, label, actual: 0, budget: 0, variance_pct: null };
    row.actual = round((row.actual ?? 0) + line.actual, 0);
    row.budget = round((row.budget ?? 0) + line.budget, 0);
    rows.set(key, row);
  }
  const out = [...rows.values()]
    .map((row) => ({
      ...row,
      variance_pct: row.budget ? round((100 * ((row.actual ?? 0) - row.budget)) / row.budget, 2) : null,
    }))
    .sort((a, b) => (b.actual ?? 0) - (a.actual ?? 0));
  return {
    group_by: groupBy,
    months,
    rows: out,
    total_actual: round(out.reduce((s, r) => s + (r.actual ?? 0), 0), 0),
    total_budget: round(out.reduce((s, r) => s + (r.budget ?? 0), 0), 0),
  };
}

export function vendorTrend(q: NonNullable<GetQuery<'/api/ops/vendors/sla-trend'>> | undefined): Schema<'VendorTrendsOut'> {
  const months = q?.months ?? 6;
  const labels = lastMonths(asOf(q), months);
  const items = VENDORS.filter((v) => !q?.vendor || v.vendor_id === q.vendor).map((vendor, vi) => {
    const slope = vi === 0 ? -3.1 : ((vi * 5) % 5) - 2;
    const series = labels.map((period, i) => ({
      period,
      sla_pct: round(Math.min(99, 90 + slope * (i - months / 2) + ((i * vi) % 3)), 2),
      mttr_median_h: round(12 + vi * 2 - slope * i * 0.4, 1),
      reassign_avg: round(0.8 + ((i + vi) % 4) / 5, 2),
      tickets: 30 + ((vi * 17 + i * 5) % 40),
    }));
    const values = series.map((p) => p.sla_pct ?? 0);
    const delta = values.length >= 6 ? round(avg(values.slice(-3)) - avg(values.slice(-6, -3)), 2) : null;
    return { vendor_id: vendor.vendor_id, vendor: vendor.name, delta_pp: delta, series };
  });
  items.sort((a, b) => (a.delta_pp ?? 0) - (b.delta_pp ?? 0));
  return { months, items };
}

function avg(values: number[]): number {
  return values.length ? values.reduce((s, v) => s + v, 0) / values.length : 0;
}

export function app360(appId: string, q: Common | undefined): Schema<'App360Out'> {
  const app = appById(appId);
  if (!app) throw new ApiError(404, 'not_found', `Unknown application '${appId}'`);
  const row = appRow(app);
  const own = TICKETS.filter((t) => t.app_id === app.app_id);
  const months = lastMonths(asOf(q), 12);
  const r = rng(Number(app.app_id.slice(3)) % 1000);
  return {
    app: row,
    kpis: [
      { key: 'inc.backlog', label: 'Open incidents', value: own.filter((t) => t.is_open && t.kind === 'incident').length, unit: 'count', compare: null, delta: null, definition: null },
      { key: 'inc.sla.pct', label: 'SLA met (3 months)', value: row.sla_pct_3m, unit: 'pct', compare: 88, delta: row.sla_pct_3m === null ? null : round(row.sla_pct_3m - 88, 2), definition: null },
      { key: 'inc.mttr.median_h', label: 'MTTR (median)', value: round(10 + r() * 20, 1), unit: 'hours', compare: null, delta: null, definition: null },
      { key: 'cost.actual.ytd', label: 'Spend YTD', value: row.cost_ytd_base, unit: 'eur', compare: null, delta: null, definition: null },
      { key: 'license.utilization', label: 'Licence utilisation', value: row.license_utilization === null ? null : round(row.license_utilization * 100, 1), unit: 'pct', compare: null, delta: null, definition: null },
    ],
    volumes: months.map((period, i) => {
      const opened = wave(Number(app.app_id.slice(-3)), i, 9, 4);
      const resolved = wave(Number(app.app_id.slice(-3)) + 1, i, 9, 4);
      return { period, opened, resolved, net: opened - resolved };
    }),
    open_tickets: own.filter((t) => t.is_open).slice(0, 20).map(toRow),
    changes: [0, 1, 2, 3].map((i) => ({
      number: `CHG${pad(31200 + i * 11 + Number(app.app_id.slice(-2)), 7)}`,
      change_type: i % 2 ? 'standard' : 'normal',
      close_code: i === 2 ? 'unsuccessful' : 'successful',
      start_date: isoAt(addDays(AS_OF, -7 * (i + 1)), 20),
      closed_at: isoAt(addDays(AS_OF, -7 * (i + 1) + 1), 2),
      short_description: ['Quarterly patching', 'Interface queue configuration', 'Release 4.2 deployment', 'Certificate renewal'][i] ?? 'Change',
    })),
    // One row per month, like GET /api/ops/apps/{app_id}.
    cost: months.map((month, m) => {
      const budget = round((app.annual_license_cost_base * 0.66) / 12, 0);
      const actual = round(budget * (0.85 + ((m + 2) * 9) % 35 / 100), 0);
      return { key: month, label: month, actual, budget, variance_pct: budget ? round((100 * (actual - budget)) / budget, 2) : null };
    }),
    contracts: CONTRACTS.filter((c) => c.app_id === app.app_id).map((c) => c.row),
    licenses: LICENSES.filter((l) => l.app_id === app.app_id).map((l) => l.row),
    jira: [0, 1, 2].map((i) => ({
      issue_key: `${app.name.slice(0, 4).toUpperCase().replace(/[^A-Z]/g, 'X')}-${120 + i * 7}`,
      issue_type: ['Story', 'Bug', 'Epic'][i] ?? 'Story',
      status: ['In Progress', 'Done', 'To Do'][i] ?? 'To Do',
      priority: ['Medium', 'High', 'Low'][i] ?? 'Medium',
      created: isoAt(addDays(AS_OF, -30 * (i + 1)), 9),
      resolved: i === 1 ? isoAt(addDays(AS_OF, -12), 15) : null,
      summary: ['Automate month-end reconciliation', 'Approval workflow skips second approver', 'Upgrade to next major release'][i] ?? 'Issue',
    })),
    docs: [
      { page_id: `DOC-${app.app_id.slice(-4)}-1`, space_key: app.name.slice(0, 4).toUpperCase(), title: `${app.name} runbook`, page_type: 'runbook', last_updated: isoAt(addDays(AS_OF, -64), 10) },
      { page_id: `DOC-${app.app_id.slice(-4)}-2`, space_key: app.name.slice(0, 4).toUpperCase(), title: `${app.name} architecture overview`, page_type: 'architecture', last_updated: isoAt(addDays(AS_OF, -210), 10) },
    ],
    findings: allFindings().filter((f) => f.subject_id === app.app_id && (q?.include_drafts || f.status !== 'draft')),
  };
}
