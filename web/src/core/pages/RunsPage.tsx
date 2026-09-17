import { Anchor, Badge, Code, SegmentedControl, SimpleGrid, Stack, Text } from '@mantine/core';
import { Link, useNavigate } from 'react-router';

import type { RunRow, Schema } from '../../api/types';
import { useApi } from '../../api/useApi';
import { useShell } from '../../app/ShellContext';
import { type Column, DataTable } from '../../components/DataTable';
import { formatDateTime, formatInt, formatRatio } from '../../components/format';
import { PageHeader } from '../../components/PageHeader';
import { SectionCard } from '../../components/SectionCard';
import { useSearchParam } from '../../hooks/useFilters';

export const RUN_STATUS_COLOR: Record<string, string> = {
  running: 'blue',
  completed: 'orange',
  approved: 'teal',
  rejected: 'red',
  failed: 'red',
};

const STATUSES = ['all', 'completed', 'approved', 'running', 'rejected', 'failed'] as const;

export function accuracyText(run: Pick<RunRow, 'sample_accuracy' | 'sample_ci_low' | 'sample_ci_high' | 'sample_n'>): string {
  if (run.sample_accuracy === null) return '–';
  return `${formatRatio(run.sample_accuracy)} (${formatRatio(run.sample_ci_low)}–${formatRatio(run.sample_ci_high)}, n=${run.sample_n ?? '?'})`;
}

const COLUMNS: Column<RunRow>[] = [
  {
    key: 'run_id',
    header: 'Run',
    value: (r) => r.run_id,
    render: (r) => (
      <Anchor component={Link} to={`/runs/${encodeURIComponent(r.run_id)}`} size="sm" ff="monospace" onClick={(e) => e.stopPropagation()}>
        {r.run_id}
      </Anchor>
    ),
  },
  { key: 'skill', header: 'Skill', value: (r) => r.skill, render: (r) => <Badge size="sm" variant="default">{r.skill}</Badge> },
  {
    key: 'status',
    header: 'Status',
    value: (r) => r.status,
    render: (r) => (
      <Badge size="sm" variant="light" color={RUN_STATUS_COLOR[r.status] ?? 'gray'}>
        {r.status}
      </Badge>
    ),
  },
  { key: 'invoked_via', header: 'Invoked via', value: (r) => r.invoked_via },
  { key: 'started_at', header: 'Started', value: (r) => r.started_at, render: (r) => formatDateTime(r.started_at), nowrap: true },
  { key: 'finished_at', header: 'Finished', value: (r) => r.finished_at, render: (r) => formatDateTime(r.finished_at), nowrap: true },
  { key: 'items', header: 'Items', value: (r) => r.counts.items ?? null, render: (r) => formatInt(r.counts.items ?? null), align: 'right' },
  { key: 'accuracy', header: 'Sample accuracy (95% CI)', value: (r) => r.sample_accuracy, render: (r) => accuracyText(r), nowrap: true },
  { key: 'reviewed_by', header: 'Reviewed by', value: (r) => r.reviewed_by },
];

type RateRow = Schema<'ReviewRateRow'>;

const RATE_COLUMNS: Column<RateRow>[] = [
  { key: 'month', header: 'Month', value: (r) => r.month, nowrap: true },
  { key: 'skill', header: 'Skill', value: (r) => r.skill },
  {
    key: 'skill_hash',
    header: 'Version',
    value: (r) => r.skill_hash,
    render: (r) => (
      <Text size="xs" ff="monospace" title={r.skill_hash}>
        {r.skill_hash.slice(0, 12)}
      </Text>
    ),
  },
  { key: 'runs', header: 'Runs', value: (r) => r.runs, render: (r) => formatInt(r.runs), align: 'right' },
  { key: 'sample_accuracy', header: 'Sample accuracy', value: (r) => r.sample_accuracy, render: (r) => formatRatio(r.sample_accuracy), align: 'right' },
  { key: 'findings_drafted', header: 'Findings', value: (r) => r.findings_drafted, render: (r) => formatInt(r.findings_drafted), align: 'right' },
  { key: 'approval_rate', header: 'Approved', value: (r) => r.approval_rate, render: (r) => formatRatio(r.approval_rate), align: 'right' },
  { key: 'edit_rate', header: 'Edited', value: (r) => r.edit_rate, render: (r) => formatRatio(r.edit_rate), align: 'right' },
  { key: 'reject_rate', header: 'Rejected', value: (r) => r.reject_rate, render: (r) => formatRatio(r.reject_rate), align: 'right' },
  { key: 'findings_open', header: 'Open', value: (r) => r.findings_open, render: (r) => formatInt(r.findings_open), align: 'right' },
];

export default function RunsPage() {
  const { meta } = useShell();
  const navigate = useNavigate();
  const [status, setStatus] = useSearchParam('status', 'all');
  const runs = useApi('/api/runs', { query: { status: status === 'all' ? null : status, limit: 200 } });
  const profile = meta.data?.profile ?? '<profile>';
  const rates = useApi('/api/ai/review-rates');

  return (
    <Stack gap="md">
      <PageHeader
        title="AI runs"
        description="Every Claude Code run with its skill, status and review result. Open a run to review its sample."
      />
      <SectionCard
        title="Runs"
        description="Newest first"
        count={runs.data?.items.length ?? null}
        actions={
          <SegmentedControl size="xs" value={status} onChange={setStatus} data={STATUSES.map((s) => ({ value: s, label: s === 'all' ? 'All' : s }))} />
        }
      >
        <DataTable
          rows={runs.data?.items}
          columns={COLUMNS}
          rowKey={(r) => r.run_id}
          loading={runs.loading}
          error={runs.error}
          onRetry={runs.reload}
          onRowClick={(r) => navigate(`/runs/${encodeURIComponent(r.run_id)}`)}
          rowLabel={(r) => `Open run ${r.run_id}`}
          emptyText="No AI runs yet"
          emptyDescription="Start one from Claude Code with the commands below."
          serverOrdered
          pageSize={50}
          minWidth={1100}
        />
      </SectionCard>
      <SectionCard
        title="Review rates by skill version"
        description="How reviewers decided per skill hash and month: a new version that is edited or rejected more shows up here"
        count={rates.data?.rows.length ?? null}
      >
        <DataTable
          rows={rates.data?.rows}
          columns={RATE_COLUMNS}
          rowKey={(r) => `${r.skill}|${r.skill_hash}|${r.month}`}
          loading={rates.loading}
          error={rates.error}
          onRetry={rates.reload}
          emptyText="No reviewed runs yet"
          serverOrdered
          pageSize={25}
          minWidth={900}
        />
      </SectionCard>
      <SectionCard title="Start or inspect runs in Claude Code" description="The dashboard never calls an AI model; runs start in Claude Code">
        <SimpleGrid cols={{ base: 1, lg: 2 }} spacing="md">
          <Stack gap={4}>
            <Text size="xs" c="dimmed">
              Full weekly analysis (import, triage, recurring issues, risks)
            </Text>
            <Code block>{`Run the sed-analyze workflow with {profile: '${profile}'}`}</Code>
          </Stack>
          <Stack gap={4}>
            <Text size="xs" c="dimmed">
              Triage a scope in-session, then list runs
            </Text>
            <Code block>{`/sed-triage-batch scope new, profile ${profile}\nuv run sed ai runs --profile ${profile} --json`}</Code>
          </Stack>
        </SimpleGrid>
      </SectionCard>
    </Stack>
  );
}
