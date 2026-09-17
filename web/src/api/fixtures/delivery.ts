/** Delivery module API fixtures (portfolio and project detail). Fictional projects, Jira keys and Confluence spaces. */
import { ApiError } from '../client';
import type { Schema } from '../types';
import { AS_OF, addDays, round } from './random';

type Row = Schema<'DeliveryProjectRow'>;
type Task = Schema<'DeliveryTask'>;
type Raid = Schema<'DeliveryRaid'>;
type Finding = Schema<'FindingOut'>;

interface ProjectSeed {
  id: string;
  name: string;
  app: string;
  phase: string;
  reported: string;
  jira: string;
  space: string;
  start: number;
  target: number;
  budget: number;
  /** Extra forecast days per milestone M1..M8 (positive = late). */
  slips: number[];
  replans: number[];
  velocity: number;
  scope: number;
  done: number;
  growth: number;
}

const SEEDS: ProjectSeed[] = [
  { id: 'PRJ-101', name: 'Orion ERP e-invoicing rollout', app: 'Orion ERP', phase: 'Build', reported: 'amber', jira: 'EINV', space: 'EINV', start: -200, target: 120, budget: 1_450_000, slips: [0, 0, 0, 35, 35, 35, 35, 35], replans: [0, 0, 0, 2, 1, 1, 1, 1], velocity: 21, scope: 420, done: 260, growth: 4 },
  { id: 'PRJ-102', name: 'Northstar CRM partner portal', app: 'Northstar CRM', phase: 'Build', reported: 'green', jira: 'PORT', space: 'PORT', start: -150, target: 60, budget: 820_000, slips: [0, 0, 0, 0, 0, 0, 0, 0], replans: [0, 0, 0, 0, 0, 0, 0, 0], velocity: 18, scope: 510, done: 210, growth: 38 },
  { id: 'PRJ-103', name: 'Nimbus WMS voice picking', app: 'Nimbus WMS', phase: 'Test', reported: 'amber', jira: 'VOICE', space: 'VOICE', start: -240, target: 45, budget: 560_000, slips: [0, 0, 0, 0, 0, 5, 5, 5], replans: [0, 0, 0, 0, 0, 1, 0, 0], velocity: 15, scope: 260, done: 205, growth: 6 },
  { id: 'PRJ-104', name: 'Lumen BI self-service analytics', app: 'Lumen BI', phase: 'Build', reported: 'green', jira: 'SELF', space: 'SELF', start: -120, target: 150, budget: 640_000, slips: [0, 0, 0, 0, 0, 0, 0, 0], replans: [0, 0, 0, 0, 0, 0, 0, 0], velocity: 24, scope: 300, done: 140, growth: 3 },
];

const MILESTONES = ['Requirements signed off', 'Design approved', 'Build sprint 3 done', 'System test complete', 'UAT complete', 'Training delivered', 'Go-live', 'Hypercare closed'];

function tasks(seed: ProjectSeed): Task[] {
  const span = seed.target - seed.start;
  return MILESTONES.map((name, i) => {
    const baseline = addDays(AS_OF, seed.start + Math.round((span * (i + 1)) / MILESTONES.length));
    const slip = seed.slips[i] ?? 0;
    const finish = addDays(baseline, slip);
    const done = finish < addDays(AS_OF, -7);
    return {
      task_id: `M${i + 1}`,
      name,
      is_milestone: true,
      baseline_finish: baseline,
      finish,
      actual_finish: done ? finish : null,
      percent_complete: done ? 100 : finish < addDays(AS_OF, 30) ? 60 : 0,
      slip_days: slip || 0,
      replans: seed.replans[i] ?? 0,
      overdue: !done && finish < AS_OF,
    };
  });
}

function raid(seed: ProjectSeed): Raid[] {
  const base = { project_id: seed.id, closed_on: null, raised_on: addDays(AS_OF, -60) };
  const items: Raid[] = [
    { ...base, raid_id: `R-${seed.id.slice(4)}-01`, raid_type: 'risk', title: 'Key integration partner availability during test', severity: 'medium', status: 'Open', open: true, due_date: addDays(AS_OF, 20), days_overdue: 0 },
    { ...base, raid_id: `I-${seed.id.slice(4)}-01`, raid_type: 'issue', title: 'Test environment refresh delayed', severity: 'low', status: 'Closed', open: false, due_date: addDays(AS_OF, -20), closed_on: addDays(AS_OF, -22), days_overdue: 0 },
  ];
  if (seed.id === 'PRJ-103') {
    items.push({ ...base, raid_id: 'R-103-02', raid_type: 'risk', title: 'Voice recognition accuracy below acceptance level in the cold store', severity: 'high', status: 'Open', open: true, due_date: addDays(AS_OF, -17), days_overdue: 17 });
  }
  return items;
}

function progress(seed: ProjectSeed): Schema<'DeliveryProgress'> {
  const weekly: Schema<'DeliveryWeek'>[] = [];
  const before = seed.scope / (1 + seed.growth / 100); // scope four weeks ago
  for (let k = 11; k >= 0; k -= 1) {
    const added = k < 4 ? ((4 - k) * (seed.scope - before)) / 4 : 0;
    weekly.push({
      week_ending: addDays(AS_OF, -7 * k),
      scope_points: round(before + added, 0),
      done_points: Math.max(0, round(seed.done - seed.velocity * k, 0)),
    });
  }
  const remaining = seed.scope - seed.done;
  const forecast = seed.velocity > 0 ? addDays(AS_OF, Math.ceil((remaining / seed.velocity) * 7)) : null;
  return {
    stories: Math.round(seed.scope / 5),
    points_total: seed.scope,
    points_done: seed.done,
    points_added_window: round(seed.scope - before, 0),
    scope_growth_pct: seed.growth,
    velocity_per_week: seed.velocity,
    forecast_finish: forecast,
    weekly,
  };
}

function health(seed: ProjectSeed, taskRows: Task[], raidRows: Raid[], prog: Schema<'DeliveryProgress'>): { rag: string; reasons: string[] } {
  const reasons: string[] = [];
  let rag = 'green';
  const worst = Math.max(0, ...taskRows.filter((t) => !t.actual_finish).map((t) => t.slip_days ?? 0));
  if (worst >= 30) {
    rag = 'red';
    reasons.push(`milestone slip ${worst} days`);
  } else if (worst >= 14) {
    rag = 'amber';
    reasons.push(`milestone slip ${worst} days`);
  }
  if (raidRows.some((r) => r.open && r.days_overdue > 0 && r.severity === 'high')) {
    if (rag === 'green') rag = 'amber';
    reasons.push('overdue high RAID item');
  }
  if ((prog.scope_growth_pct ?? 0) >= 20) {
    if (rag === 'green') rag = 'amber';
    reasons.push(`scope +${prog.scope_growth_pct}% in 4 weeks`);
  }
  const target = addDays(AS_OF, seed.target);
  if (prog.forecast_finish && prog.forecast_finish > addDays(target, 7)) {
    rag = 'red';
    reasons.push('forecast finish after target date');
  }
  return { rag, reasons };
}

function finding(id: string, severity: string, title: string, subject: string, evidence: Schema<'Evidence'>[]): Finding {
  return {
    finding_id: id,
    origin: 'rule',
    kind: 'delivery_risk',
    severity,
    title,
    subject_type: 'delivery_project',
    subject_id: subject,
    status: 'active',
    body_md: null,
    evidence,
    system_detected: true,
    run_id: null,
    reviewed_by: null,
    reviewed_at: null,
  };
}

const FINDINGS: Finding[] = [
  finding('rule-delivery-slip-101-m4', 'high', 'Orion ERP e-invoicing rollout: "UAT sign-off" slipped 35 days (2 replans)', 'PRJ-101', [
    { fact_key: 'delivery.project.PRJ-101.milestone.M4.slip_days', value: 35 },
    { fact_key: 'delivery.project.PRJ-101.milestone.M4.replans', value: 2 },
  ]),
  finding('rule-delivery-raid-103-02', 'high', 'Nimbus WMS voice picking: high risk R-103-02 overdue by 17 days', 'PRJ-103', [
    { fact_key: 'delivery.raid.R-103-02.days_overdue', value: 17 },
  ]),
  finding('rule-delivery-scope-102', 'medium', 'Northstar CRM partner portal: scope grew 38% in 4 weeks', 'PRJ-102', [
    { fact_key: 'delivery.project.PRJ-102.scope_growth_pct', value: 38 },
  ]),
  finding('rule-delivery-forecast-102', 'high', 'Northstar CRM partner portal: forecast finish is after the target go-live date', 'PRJ-102', [
    { fact_key: 'delivery.project.PRJ-102.forecast_late_days', value: 58 },
  ]),
];

interface Built {
  row: Row;
  tasks: Task[];
  raid: Raid[];
  progress: Schema<'DeliveryProgress'>;
  documents: Schema<'DeliveryDocuments'>;
}

function build(seed: ProjectSeed): Built {
  const taskRows = tasks(seed);
  const raidRows = raid(seed);
  const prog = progress(seed);
  const h = health(seed, taskRows, raidRows, prog);
  const open = taskRows.filter((t) => !t.actual_finish);
  const docs: Schema<'DeliveryDoc'>[] = [
    ...['Invoicing', 'Onboarding', 'Reporting', 'Notifications', 'Access'].map((feature, i) => ({
      page_id: `${seed.space}-${100 + i}`,
      title: `Requirements - ${feature}`,
      kind: 'requirements',
      last_updated: addDays(AS_OF, -10 - i * 6),
    })),
    ...['Use managed message queue', 'Single sign-on through the corporate identity provider', 'Store documents in the archive service'].map((title, i) => ({
      page_id: `${seed.space}-${200 + i}`,
      title: `ADR-00${i + 1} ${title}`,
      kind: 'adr',
      last_updated: addDays(AS_OF, -30 - i * 9),
    })),
  ];
  return {
    row: {
      project_id: seed.id,
      name: seed.name,
      app_id: null,
      app_raw: seed.app,
      phase: seed.phase,
      reported_rag: seed.reported,
      computed_rag: h.rag,
      reasons: h.reasons,
      target_date: addDays(AS_OF, seed.target),
      start_date: addDays(AS_OF, seed.start),
      jira_keys: [seed.jira],
      confluence_space: seed.space,
      budget_base: seed.budget,
      plan_status_date: addDays(AS_OF, -1),
      next_milestone: open.sort((a, b) => (a.finish ?? '').localeCompare(b.finish ?? ''))[0] ?? null,
      worst_slip_days: Math.max(0, ...open.map((t) => t.slip_days ?? 0)),
      open_high_raid: raidRows.filter((r) => r.open && r.severity === 'high').length,
      overdue_raid: raidRows.filter((r) => r.days_overdue > 0).length,
      points_done_pct: round((100 * prog.points_done) / prog.points_total, 1),
      forecast_finish: prog.forecast_finish,
    },
    tasks: taskRows,
    raid: raidRows,
    progress: prog,
    documents: { requirements: 5, adrs: 3, pages: docs.length, last_updated: addDays(AS_OF, -10), items: docs },
  };
}

export function portfolio(): Schema<'DeliveryPortfolioOut'> {
  const rows = SEEDS.map((s) => build(s).row);
  const counts: Record<string, number> = { projects: rows.length };
  for (const rag of ['red', 'amber', 'green']) counts[rag] = rows.filter((r) => r.computed_rag === rag).length;
  return { as_of: AS_OF, projects: rows, counts, findings: FINDINGS };
}

export function project(projectId: string): Schema<'DeliveryProjectOut'> {
  const seed = SEEDS.find((s) => s.id === projectId);
  if (!seed) throw new ApiError(412, 'precondition', `Unknown delivery project '${projectId}'`);
  const built = build(seed);
  return {
    as_of: AS_OF,
    project: built.row,
    tasks: built.tasks,
    plan_versions: 3,
    raid: built.raid,
    progress: built.progress,
    documents: built.documents,
    findings: FINDINGS.filter((f) => f.subject_id === projectId),
  };
}
