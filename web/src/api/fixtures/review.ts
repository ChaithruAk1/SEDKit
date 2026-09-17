/**
 * Review fixtures: the review queue, one label run with its review sample, and the review POSTs. State lives in this
 * module so decisions made in fixtures mode change what the pages show next (until the page is reloaded).
 */
import { ApiError } from '../client';
import type { GetQuery, PostBody, PostResponse, Schema } from '../types';
import { APPS, RUNS } from './catalog';
import { AS_OF, addDays, isoAt } from './random';

type ReviewItem = Schema<'ReviewItem'>;
type SampleCard = Schema<'SampleCard'>;

const REVIEWER = 'app.owner';
const ORION = APPS[0]!;
const NIMBUS = APPS[4]!;
const LEDGER = APPS[1]!;

function item(over: Partial<ReviewItem> & Pick<ReviewItem, 'finding_id' | 'kind' | 'title' | 'status'>): ReviewItem {
  return {
    origin: 'ai',
    run_id: RUNS.triageDraft,
    severity: 'medium',
    confidence: 0.8,
    subject_type: 'app',
    subject_id: ORION.app_id,
    body_md: null,
    pending_body_md: null,
    carried_forward_from: null,
    evidence: [],
    ticket_count: null,
    periodicity: null,
    suspected_change: null,
    recommendation: null,
    decision_due: null,
    material_change: [],
    ...over,
  };
}

let QUEUE: ReviewItem[] = [
  item({
    finding_id: 'f-draft-orion-timeouts',
    kind: 'issue_cluster',
    status: 'draft',
    severity: 'high',
    confidence: 0.86,
    title: `Interface posting timeouts on ${ORION.name} after a change`,
    body_md: [
      '**{{f:T001.tickets}} incidents** in {{f:T001.days}} days report posting timeouts on the outbound interface.',
      '',
      '- Started the day after the queue configuration change.',
      '- No problem record exists yet.',
    ].join('\n'),
    evidence: [
      { fact_key: 'T001.tickets', value: 230 },
      { fact_key: 'T001.days', value: 18 },
    ],
    ticket_count: 230,
    periodicity: 'episode',
    suspected_change: 'CHG0031207',
    recommendation: 'raise_problem',
  }),
  item({
    finding_id: 'f-draft-ledger-monthly',
    kind: 'issue_cluster',
    status: 'draft',
    severity: 'medium',
    confidence: 0.74,
    title: `${LEDGER.name}: batch failures on the first business days of every month`,
    subject_id: LEDGER.app_id,
    body_md: 'About {{f:T004.tickets_per_cycle}} incidents per month-end cycle for {{f:T004.months}} months.',
    evidence: [
      { fact_key: 'T004.tickets_per_cycle', value: 35 },
      { fact_key: 'T004.months', value: 12 },
    ],
    ticket_count: 420,
    periodicity: 'monthly',
    recommendation: 'raise_problem',
  }),
  item({
    finding_id: 'f-stale-nimbus-sso',
    kind: 'issue_cluster',
    status: 'stale_input',
    title: `Login failures after the SSO change on ${NIMBUS.name}`,
    subject_id: NIMBUS.app_id,
    body_md: 'Built on a label run that was later rejected. Re-run recurring issues before approving.',
    evidence: [{ fact_key: 'T002.tickets', value: 9 }],
    ticket_count: 9,
    periodicity: 'burst',
    recommendation: 'monitor',
  }),
  item({
    finding_id: 'f-update-nordwind',
    kind: 'vendor_risk',
    status: 'update_pending',
    severity: 'high',
    title: 'Nordwind Managed Services: resolution SLA keeps falling',
    subject_type: 'vendor',
    subject_id: 'V001',
    run_id: RUNS.risksApproved,
    carried_forward_from: 'f-approved-nordwind-w34',
    body_md: 'SLA fell by {{f:S001.sla_delta_pp}} pp over three months. Raise it at the next service review.',
    pending_body_md:
      'SLA fell by {{f:S001.sla_delta_pp}} pp over three months while reassignments rose. Escalate before the renewal decision.',
    evidence: [{ fact_key: 'S001.sla_delta_pp', value: -9.4 }],
    recommendation: 'Escalate to the vendor manager',
    decision_due: addDays(AS_OF, 30),
  }),
  item({
    finding_id: 'rule-renewal-orion-erp',
    origin: 'rule',
    run_id: null,
    kind: 'renewal_risk',
    status: 'active',
    severity: 'high',
    confidence: null,
    title: `Notice deadline in 21 days: ${ORION.name} support contract`,
    subject_type: 'contract',
    subject_id: 'C-2031',
    body_md: 'Auto-renewing contract. Notice must be given before the deadline.',
    evidence: [
      { fact_key: 'contract.days_to_notice', value: 21 },
      { fact_key: 'contract.annual_value_base', value: 184000 },
    ],
  }),
];

export function queue(query: GetQuery<'/api/review/queue'> | undefined): Schema<'ReviewQueueOut'> {
  const q = query ?? {};
  const items = QUEUE.filter((i) => (q.include_rule !== false || i.origin === 'ai') && (!q.kind || i.kind === q.kind)).slice(
    0,
    q.limit ?? 200,
  );
  const counts: Record<string, number> = {};
  for (const i of items) {
    const key = i.origin === 'ai' ? i.status : 'rule_active';
    counts[key] = (counts[key] ?? 0) + 1;
  }
  return { items, counts };
}

function decide(finding: ReviewItem, action: string, body: { note?: string | null; body_md?: string | null; until?: string | null }): string {
  const note = body.note?.trim() || null;
  const fail = (message: string): never => {
    throw new ApiError(412, 'precondition', message);
  };
  if (action === 'acknowledge' || action === 'suppress_until') {
    if (finding.origin !== 'rule') fail(`Cannot ${action} finding ${finding.finding_id}: it is an AI finding`);
    if (!note) throw new ApiError(422, 'validation', `${action} needs a note explaining why`);
    if (action === 'suppress_until' && !body.until) throw new ApiError(422, 'validation', 'suppress_until needs a date');
    return action === 'acknowledge' ? 'acknowledged' : 'active';
  }
  if (finding.origin !== 'ai') fail(`Cannot ${action} finding ${finding.finding_id}: rule findings take acknowledge or suppress_until`);
  if (action === 'approve' && !['draft', 'stale_input'].includes(finding.status)) fail('approve needs status draft or stale_input');
  if (action === 'approve_update' && finding.status !== 'update_pending') fail('approve_update needs status update_pending');
  if (action === 'edit' && !body.body_md?.trim()) throw new ApiError(422, 'validation', 'edit needs the new body text');
  if (action === 'reject') {
    if (!note) throw new ApiError(422, 'validation', 'reject needs a note explaining why');
    return 'rejected';
  }
  return 'approved';
}

function applyReview(ids: string[], action: string, body: { note?: string | null; body_md?: string | null; until?: string | null }): Schema<'FindingReviewOut'> {
  const found = ids.map((id) => QUEUE.find((i) => i.finding_id === id) ?? null);
  const missing = ids.find((_, index) => found[index] === null);
  if (missing) throw new ApiError(412, 'precondition', `Unknown finding '${missing}'`);
  // All or nothing, like the server's single transaction.
  const statuses = found.map((finding) => decide(finding!, action, body));
  QUEUE = QUEUE.filter((i) => !ids.includes(i.finding_id));
  return {
    action,
    reviewed_by: REVIEWER,
    results: ids.map((finding_id, index) => ({ finding_id, action, status: statuses[index]! })),
  };
}

export function reviewFinding(findingId: string, body: PostBody<'/api/findings/{finding_id}/review'>): Schema<'FindingReviewOut'> {
  return applyReview([findingId], body.action, body);
}

export function bulkReview(body: PostBody<'/api/findings/bulk-review'>): PostResponse<'/api/findings/bulk-review'> {
  return applyReview(body.finding_ids, body.action, body);
}

// -- label run with its review sample ------------------------------------------------------------------------------

const CATEGORY_OPTIONS: Schema<'CategoryOption'>[] = [
  { code: 'access', description: 'Access, accounts and authorisations', subcategories: [{ code: 'password_reset', only: null }, { code: 'sap_authorisation', only: 'sap' }] },
  { code: 'integration', description: 'Interfaces and message flows', subcategories: [{ code: 'message_failure', only: null }, { code: 'sap_idoc_error', only: 'sap' }] },
  { code: 'performance', description: 'Slowness and timeouts', subcategories: [{ code: 'slowness', only: null }] },
  { code: 'data_quality', description: 'Wrong or missing data', subcategories: [{ code: 'master_data', only: null }] },
  { code: 'batch_job', description: 'Scheduled jobs and batch runs', subcategories: [{ code: 'job_failure', only: null }, { code: 'sap_month_end_close', only: 'sap' }] },
  { code: 'defect', description: 'Application defects', subcategories: [{ code: 'functional_error', only: null }] },
  { code: 'how_to', description: 'Questions and guidance', subcategories: [] },
  { code: 'infrastructure', description: 'Hosting, network and platform', subcategories: [] },
  { code: 'other', description: 'None of the above', subcategories: [] },
];

const SYMPTOMS: [string, string, string, number][] = [
  ['integration', 'message_failure', 'orders_queue_messages_stuck', 0.91],
  ['access', 'password_reset', 'password_reset_request', 0.95],
  ['performance', 'slowness', 'report_generation_slow', 0.82],
  ['batch_job', 'job_failure', 'nightly_posting_job_failed', 0.88],
  ['data_quality', 'master_data', 'duplicate_supplier_record', 0.77],
  ['defect', 'functional_error', 'invoice_total_rounding_error', 0.69],
  ['how_to', '', 'how_to_export_report', 0.84],
  ['integration', 'message_failure', 'interface_posting_timeout', 0.58],
];

function card(index: number, kind: 'random' | 'lowest_conf'): SampleCard {
  const [category, sub, symptom, confidence] = SYMPTOMS[index % SYMPTOMS.length]!;
  const app = APPS[index % APPS.length]!;
  const number = `INC${String(1_204_300 + index * 37).padStart(7, '0')}`;
  return {
    key: `incident:${number}|resolved`,
    item_id: `incident:${number}`,
    stage: 'resolved',
    sample_kind: kind,
    stratum: category,
    weight: kind === 'random' ? 12.5 : 1,
    verdict: null,
    correction: null,
    label: {
      am_category: category,
      am_subcategory: sub || null,
      symptom_key: symptom,
      misfiled_as: symptom === 'password_reset_request' ? 'request' : 'none',
      confidence: kind === 'lowest_conf' ? Math.round(confidence * 50) / 100 : confidence,
      rationale: `Resolution notes describe ${symptom.replaceAll('_', ' ')}.`,
    },
    ticket: {
      number,
      kind: 'incident',
      priority: 3 + (index % 2),
      app: app.name,
      sn_category: index % 3 === 0 ? 'Software' : 'Inquiry / Help',
      short_description: `${app.name}: ${symptom.replaceAll('_', ' ')}`,
    },
  };
}

const SAMPLE: SampleCard[] = [
  ...Array.from({ length: 8 }, (_, i) => card(i, 'random')),
  ...Array.from({ length: 4 }, (_, i) => card(i + 20, 'lowest_conf')),
];
let RUN_STATUS = 'completed';
let RUN_REVIEW: Pick<Schema<'RunRow'>, 'sample_accuracy' | 'sample_ci_low' | 'sample_ci_high' | 'sample_n' | 'reviewed_by' | 'reviewed_at'> = {
  sample_accuracy: null,
  sample_ci_low: null,
  sample_ci_high: null,
  sample_n: null,
  reviewed_by: null,
  reviewed_at: null,
};

export function runReviewState(runId: string): Partial<Schema<'RunRow'>> {
  return runId === RUNS.triageDraft ? { status: RUN_STATUS, ...RUN_REVIEW } : {};
}

export function runDetail(runId: string, runs: Schema<'RunRow'>[]): Schema<'RunDetailOut'> {
  const run = runs.find((r) => r.run_id === runId);
  if (!run) throw new ApiError(412, 'precondition', `Unknown AI run '${runId}'`);
  const label = runId === RUNS.triageDraft;
  return {
    run,
    skill_hash: 'fixture-skill-hash',
    model_reported: 'claude-sonnet-5',
    input_run_ids: [],
    random: label ? SAMPLE.filter((c) => c.sample_kind === 'random') : [],
    lowest_confidence: label ? SAMPLE.filter((c) => c.sample_kind === 'lowest_conf') : [],
    matrix: label
      ? [
          { sn_category: 'Software', am_category: 'integration', n: 31 },
          { sn_category: 'Software', am_category: 'defect', n: 18 },
          { sn_category: 'Inquiry / Help', am_category: 'access', n: 22 },
          { sn_category: 'Inquiry / Help', am_category: 'how_to', n: 12 },
          { sn_category: 'Hardware', am_category: 'infrastructure', n: 9 },
          { sn_category: '(none)', am_category: 'batch_job', n: 8 },
        ]
      : [],
    misfiled: label ? { request: 14, change: 2 } : {},
    findings: runId === RUNS.risksApproved ? { approved: 3, update_pending: 1 } : {},
    eval_passed: runId === RUNS.triageApproved ? true : null,
    eval_checks: runId === RUNS.triageApproved ? { min_scored: true, category_accuracy: true, misfiled_recall: true } : {},
    categories: label ? CATEGORY_OPTIONS : [],
    misfiled_as: label ? ['none', 'request', 'change', 'problem'] : [],
  };
}

export function runVerdicts(runId: string, body: PostBody<'/api/runs/{run_id}/verdicts'>): Schema<'VerdictsOut'> {
  if (runId !== RUNS.triageDraft || RUN_STATUS !== 'completed') {
    throw new ApiError(412, 'precondition', `Run ${runId} is not a completed label run`);
  }
  let recorded = 0;
  let incorrect = 0;
  let skipped = 0;
  for (const [key, verdict] of Object.entries(body.verdicts)) {
    const target = SAMPLE.find((c) => c.key === key);
    if (!target) throw new ApiError(422, 'validation', `Verdicts rejected: ${key} is not a sampled item of this run`);
    if (verdict === null) {
      skipped += 1;
      continue;
    }
    target.verdict = verdict;
    target.correction = verdict === 'incorrect' ? (body.corrections?.[key] ?? null) : null;
    recorded += 1;
    if (verdict === 'incorrect') incorrect += 1;
  }
  return {
    run_id: runId,
    recorded,
    skipped,
    incorrect,
    random_missing: SAMPLE.filter((c) => c.sample_kind === 'random' && c.verdict === null).length,
    lowest_conf_missing: SAMPLE.filter((c) => c.sample_kind === 'lowest_conf' && c.verdict === null).length,
  };
}

export function runReview(runId: string, body: PostBody<'/api/runs/{run_id}/review'>): Schema<'RunReviewOut'> {
  if (runId !== RUNS.triageDraft || RUN_STATUS !== 'completed') {
    throw new ApiError(412, 'precondition', `Run ${runId} is ${RUN_STATUS}; only completed runs can be reviewed`);
  }
  const reviewedAt = isoAt(AS_OF, 17, 0);
  if (body.action === 'reject') {
    if (!body.note?.trim()) throw new ApiError(422, 'validation', 'reject-run needs a note explaining why');
    RUN_STATUS = 'rejected';
    RUN_REVIEW = { ...RUN_REVIEW, reviewed_by: REVIEWER, reviewed_at: reviewedAt };
    return { run_id: runId, status: 'rejected', reviewed_by: REVIEWER, corrections_applied: 0, findings_rejected: 0, dependent_findings_stale: 1 };
  }
  const random = SAMPLE.filter((c) => c.sample_kind === 'random');
  const missing = random.filter((c) => c.verdict === null).length;
  if (missing) {
    throw new ApiError(412, 'precondition', `Approve needs a verdict for every random-sample item (${missing} of ${random.length} missing)`);
  }
  const accuracy = random.filter((c) => c.verdict === 'correct').length / random.length;
  RUN_STATUS = 'approved';
  RUN_REVIEW = {
    sample_accuracy: accuracy,
    sample_ci_low: Math.max(0, accuracy - 0.25),
    sample_ci_high: Math.min(1, accuracy + 0.08),
    sample_n: random.length,
    reviewed_by: REVIEWER,
    reviewed_at: reviewedAt,
  };
  return {
    run_id: runId,
    status: 'approved',
    reviewed_by: REVIEWER,
    sample_accuracy: RUN_REVIEW.sample_accuracy,
    sample_ci_low: RUN_REVIEW.sample_ci_low,
    sample_ci_high: RUN_REVIEW.sample_ci_high,
    corrections_applied: SAMPLE.filter((c) => c.verdict === 'incorrect' && c.correction?.category).length,
    findings_rejected: 0,
    dependent_findings_stale: 0,
  };
}

export function correctLabel(body: PostBody<'/api/labels/correct'>): Schema<'LabelCorrectionOut'> {
  if (!CATEGORY_OPTIONS.some((c) => c.code === body.category)) {
    throw new ApiError(422, 'validation', `'${body.category}' is not a taxonomy category`);
  }
  return { ticket_id: body.ticket_id, stage: body.stage, run_id: `manual-${AS_OF.replaceAll('-', '')}` };
}
