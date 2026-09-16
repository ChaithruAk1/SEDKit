/** SAP module API fixtures (SAP overview and L3 tickets). Fictional SAP groups, applications and ticket numbers. */
import { ApiError } from '../client';
import type { GetQuery, Schema } from '../types';
import { AS_OF, int, isoAt, lastWeeks, pick, rng, round } from './random';

const AREAS = [
  { value: 'fi_co', label: 'FI/CO' },
  { value: 'sd', label: 'SD' },
  { value: 'mm', label: 'MM' },
  { value: 'pp_qm', label: 'PP/QM' },
  { value: 'ewm', label: 'EWM' },
  { value: 'basis', label: 'Basis' },
  { value: 'security', label: 'Security & authorisations' },
  { value: 'integration', label: 'Integration' },
  { value: 'abap', label: 'ABAP development' },
  { value: 'unassigned', label: 'Unassigned' },
];
const LANDSCAPES = [
  { value: 'ecc', label: 'SAP ECC' },
  { value: 's4', label: 'SAP S/4HANA' },
  { value: 'unknown', label: 'Unknown landscape' },
];
const REASONS = ['past SLA target', 'aged > 30d', 'P2 open, near SLA target', 'reopened', 'unassigned'];
const SHORT = [
  'Warehouse task not confirmed in warehouse W001',
  'Posting error in company code 1000 for document 4100231',
  'Sales order 4200117 blocked for delivery',
  'Background job Z_BILLING_2 cancelled',
  'Missing authorisation for transaction VA02',
];

type AreaCounts = { area: string; label: string; open: number; aged: number };

function areaCounts(): AreaCounts[] {
  const r = rng(314);
  return AREAS.map((a) => {
    const open = a.value === 'ewm' ? 44 : a.value === 'unassigned' ? 6 : int(r, 2, 14);
    return { area: a.value, label: a.label, open, aged: a.value === 'ewm' ? 22 : int(r, 0, Math.floor(open / 3)) };
  });
}

function sapFindings(): Schema<'FindingOut'>[] {
  const base = {
    origin: 'rule' as const,
    kind: 'sap_backlog_risk',
    subject_type: 'sap_area',
    subject_id: 'ewm',
    status: 'active',
    body_md: null,
    system_detected: true,
    run_id: null,
    reviewed_by: null,
    reviewed_at: null,
  };
  return [
    {
      ...base,
      finding_id: 'rule-sap-growth-ewm',
      severity: 'high',
      title: 'SAP EWM backlog growing: +45 tickets over 8 weeks (8 weeks with more arrivals than closures)',
      evidence: [
        { fact_key: 'sap.area.ewm.net_growth', value: 45 },
        { fact_key: 'sap.area.ewm.weeks_growing', value: 8 },
      ],
    },
    {
      ...base,
      finding_id: 'rule-sap-aged-ewm',
      severity: 'high',
      title: 'SAP EWM: 22 open tickets older than 30 days',
      evidence: [{ fact_key: 'sap.area.ewm.aged_30d', value: 22 }],
    },
  ];
}

export function overview(): Schema<'SapOverview'> {
  const r = rng(271);
  const weeks = lastWeeks(AS_OF, 1);
  const counts = areaCounts();
  const open = counts.reduce((sum, a) => sum + a.open, 0);
  const aged = counts.reduce((sum, a) => sum + a.aged, 0);
  const kpi = (key: string, label: string, value: number | null, unit: string, compare: number | null = null) => ({
    key,
    label,
    value,
    unit,
    compare,
    delta: value !== null && compare !== null ? round(value - compare) : null,
    definition: null,
  });
  return {
    as_of: AS_OF,
    period: weeks[0] ?? '2026-W35',
    data_as_of_last_import: AS_OF,
    configured: true,
    kpis: [
      kpi('sap.l3.backlog', 'Open SAP incidents', open, 'count'),
      kpi('sap.l3.aged_30d', 'Open for more than 30 days', aged, 'count'),
      kpi('sap.l3.opened', 'Opened (2026-W35)', 38, 'count', 41.5),
      kpi('sap.l3.resolved', 'Resolved (2026-W35)', 31, 'count', 39.25),
      kpi('sap.l3.sla.pct', 'SLA met (2026-W35)', 84.2, 'pct', 88.1),
      kpi('sap.l3.mttr.median_h', 'MTTR median (2026-W35)', 31.4, 'hours', 27.9),
      kpi('sap.l3.p1p2.open', 'Open P1/P2', 3, 'count'),
      kpi('sap.findings.count', 'System-detected SAP risks', 2, 'count'),
    ],
    areas: counts.map((a) => {
      const opened = int(r, 1, 12);
      const resolved = int(r, 0, opened + 2);
      return {
        area: a.area,
        label: a.label,
        open: a.open,
        aged_30d: a.aged,
        opened,
        resolved,
        sla_pct: resolved ? round(70 + r() * 30, 2) : null,
      };
    }),
    landscapes: [
      { landscape: 'ecc', label: 'SAP ECC', open: Math.round(open * 0.4) },
      { landscape: 's4', label: 'SAP S/4HANA', open: open - Math.round(open * 0.4) },
    ],
    findings: sapFindings(),
  };
}

export function l3(query: GetQuery<'/api/sap/l3'> | undefined): Schema<'SapL3Out'> {
  const q = query ?? {};
  const area = q.area ?? null;
  const landscape = q.landscape ?? null;
  if (area && !AREAS.some((a) => a.value === area)) throw new ApiError(422, 'validation', `Unknown SAP area '${area}'`);
  if (landscape && !LANDSCAPES.some((l) => l.value === landscape)) {
    throw new ApiError(422, 'validation', `Unknown SAP landscape '${landscape}'`);
  }
  const r = rng(577 + (area ?? '').length * 11 + (landscape ?? '').length);
  const counts = areaCounts().filter((a) => !area || a.area === area);
  const factor = landscape === 'ecc' ? 0.4 : landscape === 's4' ? 0.6 : landscape === 'unknown' ? 0 : 1;
  const byArea = counts
    .map((a) => {
      const total = Math.round(a.open * factor);
      const aged = Math.min(total, Math.round(a.aged * factor));
      const young = total - aged;
      const d0_7 = Math.floor(young / 2);
      return { area: a.area, label: a.label, total, d0_7, d8_30: young - d0_7, d31_90: aged, d90p: 0 };
    })
    .filter((a) => a.total > 0);
  const total = byArea.reduce((sum, a) => sum + a.total, 0);
  const weeks = lastWeeks(AS_OF, q.weeks ?? 12);
  const trend = weeks.map((period, i) => {
    const opened = Math.round((int(r, 25, 45) + (i > weeks.length - 9 && (!area || area === 'ewm') ? 15 : 0)) * (area ? 0.3 : 1) * factor);
    const resolved = Math.max(0, opened - int(r, -4, 8));
    return { period, opened, resolved, net: opened - resolved, sla_pct: resolved ? round(78 + r() * 18, 2) : null };
  });
  const flow = weeks.slice(-8).flatMap((period) =>
    counts.map((a) => {
      const arrived = Math.round(int(r, 1, 9) * factor) + (a.area === 'ewm' ? 6 : 0);
      const closed = Math.max(0, arrived - (a.area === 'ewm' ? int(r, 4, 8) : int(r, -2, 2)));
      return { period, area: a.area, label: a.label, arrived, closed, net: arrived - closed };
    }),
  );
  const attention = Array.from({ length: Math.min(total, 40) }, (_, i) => {
    const a: { area: string; label: string } = byArea.length ? pick(r, byArea) : { area: 'ewm', label: 'EWM' };
    const number = `INC2436${String(7000 + i).padStart(4, '0')}`;
    return {
      ticket_id: `incident:${number}`,
      number,
      priority: pick(r, [2, 3, 3, 4]),
      area: a.area,
      area_label: a.label,
      app: pick(r, ['SAP ECC', 'SAP S/4HANA']),
      state: pick(r, ['In Progress', 'On Hold', 'Work in Progress']),
      assignment_group: a.area === 'unassigned' ? 'IT-SERVICE-DESK-L1' : `SAP-${a.area.toUpperCase()}-L3`,
      opened_at: isoAt(AS_OF, 9),
      age_days: round(2 + r() * 60, 1),
      reasons: pick(r, REASONS),
      short_description: pick(r, SHORT),
    };
  });
  return {
    as_of: AS_OF,
    at: `${AS_OF}T22:00:00Z`,
    sla_source: 'task_sla',
    area,
    landscape,
    areas: AREAS,
    landscapes: LANDSCAPES,
    backlog_total: total,
    aging: {
      d0_7: byArea.reduce((s, a) => s + a.d0_7, 0),
      d8_30: byArea.reduce((s, a) => s + a.d8_30, 0),
      d31_90: byArea.reduce((s, a) => s + a.d31_90, 0),
      d90p: 0,
    },
    by_area: byArea,
    by_landscape: [
      { landscape: 'ecc', label: 'SAP ECC', open: Math.round(total * 0.4) },
      { landscape: 's4', label: 'SAP S/4HANA', open: total - Math.round(total * 0.4) },
    ],
    trend,
    flow,
    sla_by_priority: [
      { priority: 'P2', total: 4, met: 3, pct: 75 },
      { priority: 'P3', total: 22, met: 19, pct: 86.36 },
      { priority: 'P4', total: 9, met: 9, pct: 100 },
    ],
    attention_count: attention.length,
    attention,
  };
}
