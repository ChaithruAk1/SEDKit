/** SAP module API fixtures (SAP overview and L3 tickets). Fictional SAP groups, applications and ticket numbers. */
import { ApiError } from '../client';
import type { GetQuery, Schema } from '../types';
import { AS_OF, addDays, int, isoAt, lastWeeks, pad, pick, rng, round } from './random';

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
  const change = { ...base, kind: 'sap_change_risk', severity: 'high' };
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
    {
      ...change,
      finding_id: 'rule-sap-failed-import',
      subject_type: 'sap_transport',
      subject_id: 'HD1K900023',
      title: 'Failed production import: transport HD1K900023 into HP1 (return code 8) for change 8000009001; 14 SAP incidents within 72 h',
      evidence: [
        { fact_key: 'sap.transport.HD1K900023.return_code', value: 8 },
        { fact_key: 'sap.transport.HD1K900023.incidents_after', value: 14 },
      ],
    },
    {
      ...change,
      finding_id: 'rule-sap-urgent-mm',
      subject_id: 'mm',
      title: 'SAP MM: 65% of new changes urgent in the last 8 weeks (was 7%)',
      evidence: [
        { fact_key: 'sap.changes.mm.urgent_ratio_pct', value: 64.7 },
        { fact_key: 'sap.changes.mm.urgent_ratio_previous_pct', value: 7.1 },
      ],
    },
    {
      ...change,
      severity: 'medium',
      finding_id: 'rule-sap-stuck-ppqm',
      subject_id: 'pp_qm',
      title: 'SAP PP/QM: 4 changes stuck in their status (oldest 46 days)',
      evidence: [{ fact_key: 'sap.changes.pp_qm.stuck', value: 4 }],
    },
    {
      ...change,
      severity: 'medium',
      finding_id: 'rule-sap-waiting-ecc',
      subject_type: 'sap_landscape',
      subject_id: 'ecc',
      title: 'SAP ECC: 6 tested transports waiting for production (oldest 26 days since the QA import)',
      evidence: [{ fact_key: 'sap.transports.ecc.waiting', value: 6 }],
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
      kpi('sap.findings.count', 'System-detected SAP risks', 12, 'count'),
      kpi('sap.changes.open', 'Open SAP changes', 15, 'count'),
      kpi('sap.changes.urgent_ratio_8w', 'Urgent changes (8 weeks)', 33.3, 'pct', 4.8),
      kpi('sap.transports.failed_4w', 'Failed transport imports (28 days)', 1, 'count'),
      kpi('sap.idocs.errors_open', 'IDocs in error', 89, 'count'),
      kpi('sap.idocs.new_persistent', `New persistent IDoc errors (${weeks[0] ?? '2026-W35'})`, 85, 'count', 9.75),
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

const STAGES = [
  ['requested', 'Requested'],
  ['approved', 'Approved'],
  ['in_development', 'In development'],
  ['in_test', 'In test'],
  ['ready_for_production', 'Ready for production'],
  ['in_production', 'In production'],
] as const;
const TYPES: Record<string, string> = { normal: 'Normal', urgent: 'Urgent', standard: 'Standard', defect_correction: 'Defect correction' };
const CHANGE_TITLES = [
  'Pricing condition changes for sales organisation S100',
  'Release strategy for purchase orders above limit 50000',
  'Inspection plan changes for plant P200',
  'Adjust GL account determination for company code 1000',
  'Warehouse process type for outbound deliveries in warehouse W001',
];

export function changes(query: GetQuery<'/api/sap/changes'> | undefined): Schema<'SapChangesOut'> {
  const q = query ?? {};
  const area = q.area ?? null;
  const landscape = q.landscape ?? null;
  if (area && !AREAS.some((a) => a.value === area)) throw new ApiError(422, 'validation', `Unknown SAP area '${area}'`);
  if (landscape && !LANDSCAPES.some((l) => l.value === landscape)) {
    throw new ApiError(422, 'validation', `Unknown SAP landscape '${landscape}'`);
  }
  const r = rng(911 + (area ?? '').length * 7 + (landscape ?? '').length);
  const weeks = lastWeeks(AS_OF, q.weeks ?? 12);
  const kpi = (key: string, label: string, value: number | null, unit: string, compare: number | null = null) => ({
    key,
    label,
    value,
    unit,
    compare,
    delta: value !== null && compare !== null ? round(value - compare) : null,
    definition: null,
  });
  const areaOf = (code: string) => AREAS.find((a) => a.value === code) ?? { value: 'unassigned', label: 'Unassigned' };
  const changeRow = (i: number, code: string, stage: (typeof STAGES)[number], type = 'normal') => ({
    change_id: `80000${String(9000 + i).padStart(5, '0')}`,
    title: pick(r, CHANGE_TITLES),
    change_type: type,
    type_label: TYPES[type] ?? 'Other',
    area: code,
    area_label: areaOf(code).label,
    landscape: code === 'ewm' || code === 'sd' ? 's4' : 'ecc',
    stage: stage[0],
    stage_label: stage[1],
    created_at: isoAt(addDays(AS_OF, -(20 + i)), 10),
  });
  const stuck = [0, 1, 2, 3].map((i) => ({
    ...changeRow(32 + i, 'pp_qm', STAGES[3]),
    status: 'To Be Tested',
    days_in_status: round(45.5 - i * 0.1, 1),
    threshold_days: 30,
    jira_keys: [`SAPS4-${40 + i}`],
  }));
  const waiting = Array.from({ length: 6 }, (_, i) => ({
    transport: `ED1K9000${83 + i}`,
    change_id: `80000090${36 + Math.floor(i / 2)}`,
    title: 'Adjust GL account determination for company code 1000',
    landscape: 'ecc',
    qa_system: 'EQ1',
    qa_imported_at: isoAt(addDays(AS_OF, -(25)), 10),
    days_waiting: round(25.6 - i * 0.02, 1),
  }));
  const keep = <T extends { area?: string; landscape: string }>(rows: T[]) =>
    rows.filter((row) => (!area || row.area === undefined || row.area === area) && (!landscape || row.landscape === landscape));
  const without = [0, 1, 2, 3, 4].map((i) => changeRow(39 + i, 'ewm', STAGES[2]));
  return {
    as_of: AS_OF,
    at: `${AS_OF}T22:00:00Z`,
    period: weeks.at(-1) ?? '2026-W35',
    area,
    landscape,
    areas: AREAS,
    landscapes: LANDSCAPES,
    kpis: [
      kpi('sap.changes.open', 'Open changes', 15, 'count'),
      kpi('sap.changes.urgent_ratio_8w', 'Urgent changes (8 weeks)', 33.3, 'pct', 4.8),
      kpi('sap.changes.stuck', 'Stuck changes', 4, 'count'),
      kpi('sap.changes.without_jira', 'Without a Jira story', 5, 'count'),
      kpi('sap.changes.prod_imports', `Production imports (${weeks.at(-1) ?? '2026-W35'})`, 4, 'count'),
      kpi('sap.transports.failed_4w', 'Failed imports (28 days)', 1, 'count'),
      kpi('sap.transports.waiting', 'Waiting for production', 6, 'count'),
    ],
    stages: STAGES.slice(1).map(([stage, label]) => {
      const normal = int(r, 0, 6);
      const urgent = int(r, 0, 2);
      return { stage, label, normal, urgent, standard: 0, defect_correction: 0, general: 0, other: 0, total: normal + urgent };
    }),
    urgent_by_area: AREAS.slice(0, 5).map((a) => {
      const created = a.value === 'mm' ? 17 : int(r, 2, 14);
      const urgent = a.value === 'mm' ? 11 : int(r, 0, 1);
      const previous = int(r, 4, 14);
      const ratio = round((100 * urgent) / created, 1);
      const prev = a.value === 'mm' ? 7.1 : round((100 * int(r, 0, 1)) / previous, 1);
      return {
        area: a.value,
        label: a.label,
        created,
        urgent,
        ratio_pct: ratio,
        previous_created: previous,
        previous_urgent: a.value === 'mm' ? 1 : 0,
        previous_ratio_pct: prev,
        delta_pp: round(ratio - prev, 1),
      };
    }),
    production_imports: weeks.map((period, i) => ({
      period,
      imports: i === weeks.length - 3 ? 34 : int(r, 2, 8),
      failed: i === weeks.length - 1 ? 1 : 0,
      changes: int(r, 1, 5),
    })),
    stuck: keep(stuck),
    waiting: keep(waiting),
    failed: keep([
      {
        transport: 'HD1K900023',
        system_id: 'HP1',
        role: 'prod',
        landscape: 's4',
        return_code: 8,
        imported_at: isoAt(addDays(AS_OF, -(8)), 10),
        change_id: '8000009001',
        title: 'Urgent correction of billing document output for sales organisation S100',
        change_type: 'urgent',
      },
    ]),
    incidents_after_imports: keep([
      {
        change_id: '8000009001',
        transport: 'HD1K900023',
        transports: 1,
        title: 'Urgent correction of billing document output for sales organisation S100',
        change_type: 'urgent',
        area: 'sd',
        area_label: 'SD',
        landscape: 's4',
        system_id: 'HP1',
        imported_at: isoAt(addDays(AS_OF, -(8)), 10),
        return_code: 8,
        incidents: 14,
        incidents_before: 2,
        lift: 12,
        numbers: ['INC24317800', 'INC24317801', 'INC24317802', 'INC24317803', 'INC24317804'],
      },
    ]),
    without_jira_count: keep(without).length,
    without_jira: keep(without),
  };
}

const SYSTEMS = [
  { value: 'ED1', label: 'ED1 (SAP ECC, dev)' },
  { value: 'EQ1', label: 'EQ1 (SAP ECC, qa)' },
  { value: 'EP1', label: 'EP1 (SAP ECC, prod)' },
  { value: 'HD1', label: 'HD1 (SAP S/4HANA, dev)' },
  { value: 'HQ1', label: 'HQ1 (SAP S/4HANA, qa)' },
  { value: 'HP1', label: 'HP1 (SAP S/4HANA, prod)' },
];

export function idocs(query: GetQuery<'/api/sap/idocs'> | undefined): Schema<'SapIdocsOut'> {
  const q = query ?? {};
  const system = q.system ?? null;
  const area = q.area ?? null;
  const direction = q.direction ?? null;
  if (system && !SYSTEMS.some((s) => s.value === system)) throw new ApiError(422, 'validation', `Unknown SAP system '${system}'`);
  if (area && !AREAS.some((a) => a.value === area)) throw new ApiError(422, 'validation', `Unknown SAP area '${area}'`);
  if (direction && direction !== 'inbound' && direction !== 'outbound') {
    throw new ApiError(422, 'validation', `Unknown IDoc direction '${direction}'`);
  }
  const r = rng(733 + (system ?? '').length + (area ?? '').length * 3 + (direction ?? '').length * 5);
  const weeks = lastWeeks(AS_OF, q.weeks ?? 12);
  const kpi = (key: string, label: string, value: number | null, unit: string, compare: number | null = null) => ({
    key,
    label,
    value,
    unit,
    compare,
    delta: value !== null && compare !== null ? round(value - compare) : null,
    definition: null,
  });
  const types = [
    { system_id: 'EP1', landscape: 'ecc', message_type: 'ORDERS', direction: 'inbound', area: 'sd', errors: 64, aged: 64, partners: 1, oldest_hours: 1047.9 },
    { system_id: 'HP1', landscape: 's4', message_type: 'INVOIC', direction: 'outbound', area: 'fi_co', errors: 25, aged: 25, partners: 20, oldest_hours: 176.6 },
  ].filter(
    (t) => (!system || t.system_id === system) && (!area || t.area === area) && (!direction || t.direction === direction),
  );
  const open = types.reduce((sum, t) => sum + t.errors, 0);
  return {
    as_of: AS_OF,
    at: `${AS_OF}T22:00:00Z`,
    period: weeks.at(-1) ?? '2026-W35',
    system,
    landscape: q.landscape ?? null,
    area,
    direction,
    systems: SYSTEMS,
    areas: AREAS,
    landscapes: LANDSCAPES,
    kpis: [
      kpi('sap.idocs.errors_open', 'IDocs in error', open, 'count'),
      kpi('sap.idocs.errors_aged', 'Errors open > 48 h', open, 'count'),
      kpi('sap.idocs.new_persistent', `New persistent errors (${weeks.at(-1) ?? '2026-W35'})`, 85, 'count', 9.75),
      kpi('sap.idocs.reprocess_median_h', `Reprocessing median (${weeks.at(-1) ?? '2026-W35'})`, 8.5, 'hours'),
      kpi('sap.idocs.reprocessed_in_grace_pct', `Reprocessed within grace (${weeks.at(-1) ?? '2026-W35'})`, 69.6, 'pct'),
    ],
    aging: { lt4h: 0, h4_24: 0, d1_2: 0, d2_7: Math.min(open, 24), gt7d: Math.max(0, open - 24) },
    by_type: types,
    partners: types.map((t) => ({
      system_id: t.system_id,
      message_type: t.message_type,
      partner: t.message_type === 'ORDERS' ? 'PARTNER_0007' : 'PARTNER_0002',
      errors: t.message_type === 'ORDERS' ? 64 : 2,
      oldest_hours: t.oldest_hours,
    })),
    weekly: weeks.map((period, i) => {
      const last = i === weeks.length - 1;
      const growing = [2, 4, 7, 11, 16, 24][i - (weeks.length - 6)] ?? 0;
      return {
        period,
        idocs: int(r, 2300, 2700) + (i === weeks.length - 6 ? 600 : 0),
        new_errors: growing + int(r, 10, 30) + (last ? 140 : 0),
        persistent: growing + int(r, 0, 4) + (last ? 60 : 0),
        reprocessed: int(r, 10, 30) + (last ? 80 : 0),
        reprocess_median_h: round(4 + r() * 8, 1),
      };
    }),
    top_texts: types.map((t) => ({
      text:
        t.message_type === 'ORDERS'
          ? 'Sold-to party # not maintained for sales area S100/01/00'
          : 'EDI: Syntax error in IDoc (mandatory segment E1EDP01 missing) for billing document #',
      errors: t.errors,
      message_types: [t.message_type],
    })),
    spikes:
      !system || system === 'HP1'
        ? [
            {
              change_id: '8000009001',
              transport: 'HD1K900023',
              title: 'Urgent correction of billing document output for sales organisation S100',
              change_type: 'urgent',
              system_id: 'HP1',
              imported_at: isoAt(addDays(AS_OF, -8), 16, 25),
              return_code: 8,
              errors: 60,
              errors_before: 0,
              lift: 60,
            },
          ]
        : [],
    open_errors_count: open,
    open_errors: Array.from({ length: Math.min(open, 30) }, (_, i) => {
      const t = types[i % Math.max(types.length, 1)] ?? { system_id: 'EP1', message_type: 'ORDERS', direction: 'inbound' };
      return {
        system_id: t.system_id,
        docnum: String((t.system_id === 'EP1' ? 3000000000 : 7000000000) + 5000 + i).padStart(16, '0'),
        direction: t.direction,
        message_type: t.message_type,
        partner: t.message_type === 'ORDERS' ? 'PARTNER_0007' : `PARTNER_${pad(int(r, 1, 40), 4)}`,
        status_code: t.message_type === 'ORDERS' ? '51' : '26',
        text: t.message_type === 'ORDERS' ? 'Sold-to party 100023 not maintained for sales area S100/01/00' : 'EDI: Syntax error in IDoc (mandatory segment E1EDP01 missing) for billing document 912345',
        first_error_at: isoAt(addDays(AS_OF, -(30 - i)), 9),
        age_hours: round((30 - i) * 24, 1),
      };
    }),
  };
}
