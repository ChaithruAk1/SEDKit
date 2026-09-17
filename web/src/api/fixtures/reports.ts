/**
 * Report fixtures: the report catalog with sections, recent artifacts, section readiness and background build jobs.
 * A build job finishes on the second poll so the page shows its running and done states.
 */
import { ApiError } from '../client';
import type { GetQuery, PostBody, Schema } from '../types';
import { jobSeq, startJob } from './jobs';
import { AS_OF, addDays, isoAt } from './random';

type Job = Schema<'JobOut'>;

const REPORTS: Schema<'ReportInfo'>[] = [
  {
    key: 'weekly',
    module: 'ops',
    title: 'Weekly Application Operations Review',
    period_kinds: ['week'],
    needs_vendor: false,
    formats: ['xlsx', 'md', 'pptx'],
    sections: ['headline', 'highlights', 'lowlights', 'recurring_issues', 'actions'],
  },
  {
    key: 'monthly',
    module: 'ops',
    title: 'Monthly Service Review',
    period_kinds: ['month'],
    needs_vendor: false,
    formats: ['xlsx', 'md', 'pptx'],
    sections: ['exec_summary', 'service_performance', 'top_issues', 'improvements', 'upcoming_changes', 'asks'],
  },
  {
    key: 'quarterly',
    module: 'ops',
    title: 'Quarterly Budget & Governance',
    period_kinds: ['quarter'],
    needs_vendor: false,
    formats: ['xlsx', 'md', 'pptx'],
    sections: ['exec_summary', 'spend_vs_budget', 'license_optimization', 'renewals_and_vendor_risk', 'portfolio_health', 'decisions_needed'],
  },
  {
    key: 'vendor',
    module: 'ops',
    title: 'Vendor Review',
    period_kinds: ['quarter', 'month'],
    needs_vendor: true,
    formats: ['xlsx', 'md', 'pptx'],
    sections: ['relationship_summary', 'performance', 'commercial', 'risks', 'negotiation_points'],
  },
  {
    key: 'sap-weekly',
    module: 'sap',
    title: 'Weekly SAP Operations Review',
    period_kinds: ['week'],
    needs_vendor: false,
    formats: ['xlsx', 'md', 'pptx'],
    sections: ['headline', 'l3_support', 'changes', 'idocs', 'actions'],
  },
];

let ARTIFACTS: Schema<'ArtifactRow'>[] = [
  {
    artifact_id: 'art-weekly-w35-pptx',
    report: 'weekly',
    period: '2026-W35',
    vendor_id: null,
    snapshot_id: 'snap-weekly-2026-W35-fixture',
    format: 'pptx',
    ai_mode: 'approved',
    built_at: isoAt(AS_OF, 9, 5),
    file_name: 'weekly_2026-W35_SYNTHETIC.pptx',
    sha256: 'fixture-sha',
    ai_run_ids: ['run-20260901-triage-02'],
    omitted: ['actions: no draft'],
  },
  {
    artifact_id: 'art-monthly-08-xlsx',
    report: 'monthly',
    period: '2026-08',
    vendor_id: null,
    snapshot_id: 'snap-monthly-2026-08-fixture',
    format: 'xlsx',
    ai_mode: 'none',
    built_at: isoAt(addDays(AS_OF, -1), 17, 20),
    file_name: 'monthly_2026-08_SYNTHETIC.xlsx',
    sha256: 'fixture-sha',
    ai_run_ids: [],
    omitted: [],
  },
];

export function reports(query: GetQuery<'/api/reports'> | undefined): Schema<'ReportsOut'> {
  return { reports: REPORTS, artifacts: ARTIFACTS.slice(0, query?.limit ?? 50) };
}

export function readiness(query: GetQuery<'/api/reports/readiness'>): Schema<'ReadinessOut'> {
  const report = REPORTS.find((r) => r.key === query.report);
  if (!report) throw new ApiError(422, 'validation', `Unknown report '${query.report}'`);
  const statuses: [string, string, string[]][] = [
    ['approved', 'approved', []],
    ['stale', 'draft', ['fact inc.backlog changed since drafting']],
    ['blocked', 'approved', ['cited finding ai-cluster-nimbus-draft is draft']],
    ['missing', 'draft', ['not approved yet']],
    ['missing', 'missing', []],
    ['missing', 'missing', []],
  ];
  const sections = report.sections.map((key, i) => {
    const [approved, draft, reasons] = statuses[i % statuses.length]!;
    return {
      key,
      title: key.replaceAll('_', ' ').replace(/^\w/, (c) => c.toUpperCase()),
      required: true,
      approved_status: approved,
      draft_status: draft,
      has_newer_draft: draft === 'draft',
      finding_id: draft === 'missing' ? null : `f-section-${key}`,
      reasons,
    };
  });
  return {
    report: report.key,
    period: query.period,
    vendor_id: query.vendor ?? null,
    snapshot_id: `snap-${report.key}-${query.period}-fixture`,
    sections_required: sections.length,
    sections_approved: sections.filter((s) => s.approved_status === 'approved').length,
    sections_with_drafts: sections.filter((s) => s.draft_status === 'draft').length,
    cited_findings_unapproved: ['ai-cluster-nimbus-draft'],
    complete: false,
    sections,
  };
}

export function startBuild(body: PostBody<'/api/reports/build'>): Job {
  const report = REPORTS.find((r) => r.key === body.report);
  if (!report) throw new ApiError(422, 'validation', `Unknown report '${body.report}'`);
  if (report.needs_vendor && !body.vendor) throw new ApiError(412, 'precondition', `The ${body.report} report needs a vendor`);
  return startJob('report_build', { ...body }, finishBuild);
}

function finishBuild(job: Job): void {
  const body = job.params as PostBody<'/api/reports/build'>;
  const formats = body.formats ?? ['xlsx', 'md', 'pptx'];
  if (body.require_complete && body.ai_mode !== 'none') {
    Object.assign(job, {
      status: 'failed',
      finished_at: isoAt(AS_OF, 10, 59),
      error: { kind: 'precondition', message: '3 required AI sections are not ready for --ai approved', details: { sections: ['lowlights: stale'] } },
    });
    return;
  }
  const suffix = `${body.vendor ? `_${body.vendor}` : ''}_SYNTHETIC${body.ai_mode === 'draft' ? '_DRAFT' : ''}`;
  const built = formats.map((format) => ({
    format,
    artifact_id: `art-${body.report}-${body.period}-${format}-${job.job_id}`,
    file_name: `${body.report}_${body.period}${suffix}.${format}`,
    sha256: 'fixture-sha',
    template_map: null,
  }));
  ARTIFACTS = [
    ...built.map((a) => ({
      artifact_id: a.artifact_id,
      report: body.report,
      period: body.period,
      vendor_id: body.vendor ?? null,
      snapshot_id: `snap-${body.report}-${body.period}-fixture`,
      format: a.format,
      ai_mode: body.ai_mode ?? 'approved',
      built_at: isoAt(AS_OF, 11, jobSeq(job) % 60),
      file_name: a.file_name,
      sha256: 'fixture-sha',
      ai_run_ids: [],
      omitted: ['headline: no draft'],
    })),
    ...ARTIFACTS,
  ];
  Object.assign(job, {
    status: 'done',
    finished_at: isoAt(AS_OF, 10, 58),
    result: {
      report: body.report,
      period: body.period,
      snapshot_id: `snap-${body.report}-${body.period}-fixture`,
      ai_mode: body.ai_mode,
      artifacts: built,
      readiness: { ai_sections_required: 5, ai_sections_shown: 1, omitted: ['headline: no draft', 'actions: no draft'] },
    },
  });
}
