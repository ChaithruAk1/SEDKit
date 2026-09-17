import { Anchor, Badge, Grid, Group, SimpleGrid, Stack, Text } from '@mantine/core';
import { CartesianGrid, Legend, Line, LineChart, Tooltip, XAxis, YAxis } from 'recharts';
import { Link, useParams } from 'react-router';

import type { Schema } from '../../../api/types';
import { useApi } from '../../../api/useApi';
import { CHART_COLORS, ChartCard } from '../../../components/ChartCard';
import { type Column, DataTable } from '../../../components/DataTable';
import { ErrorState } from '../../../components/ErrorState';
import { FindingList } from '../../../components/FindingList';
import { formatDate, formatInt, formatNumber, formatPct, humanize } from '../../../components/format';
import { PageHeader } from '../../../components/PageHeader';
import { SectionCard } from '../../../components/SectionCard';
import { useFilters } from '../../../hooks/useFilters';
import { RagBadge } from './DeliveryPortfolioPage';

type Task = Schema<'DeliveryTask'>;
type Raid = Schema<'DeliveryRaid'>;
type Doc = Schema<'DeliveryDoc'>;

const TASK_COLUMNS: Column<Task>[] = [
  {
    key: 'name',
    header: 'Task',
    value: (r) => r.name,
    render: (r) => (
      <Group gap={6} wrap="nowrap">
        <Text size="sm">{r.name ?? r.task_id}</Text>
        {r.is_milestone ? (
          <Badge size="xs" variant="outline">
            milestone
          </Badge>
        ) : null}
      </Group>
    ),
  },
  { key: 'baseline_finish', header: 'Baseline', value: (r) => r.baseline_finish, render: (r) => formatDate(r.baseline_finish), nowrap: true },
  { key: 'finish', header: 'Forecast', value: (r) => r.finish, render: (r) => formatDate(r.finish), nowrap: true },
  { key: 'actual_finish', header: 'Actual', value: (r) => r.actual_finish, render: (r) => formatDate(r.actual_finish), nowrap: true },
  {
    key: 'slip_days',
    header: 'Slip (days)',
    value: (r) => r.slip_days,
    render: (r) => <Text size="sm" c={(r.slip_days ?? 0) >= 14 ? 'red' : undefined}>{formatInt(r.slip_days)}</Text>,
    align: 'right',
  },
  { key: 'replans', header: 'Replans', value: (r) => r.replans, render: (r) => formatInt(r.replans), align: 'right' },
  { key: 'percent_complete', header: 'Done', value: (r) => r.percent_complete, render: (r) => formatPct(r.percent_complete, 0), align: 'right' },
  {
    key: 'overdue',
    header: '',
    sortable: false,
    render: (r) =>
      r.overdue ? (
        <Badge size="xs" color="red">
          overdue
        </Badge>
      ) : null,
  },
];

const RAID_COLUMNS: Column<Raid>[] = [
  { key: 'raid_id', header: 'ID', value: (r) => r.raid_id, nowrap: true },
  { key: 'raid_type', header: 'Type', value: (r) => r.raid_type, render: (r) => humanize(r.raid_type) },
  {
    key: 'title',
    header: 'Title',
    value: (r) => r.title,
    render: (r) => (
      <Text size="sm" style={{ overflowWrap: 'anywhere' }}>
        {r.title}
      </Text>
    ),
  },
  { key: 'severity', header: 'Severity', value: (r) => r.severity, render: (r) => humanize(r.severity) },
  {
    key: 'open',
    header: 'Status',
    value: (r) => (r.open ? 0 : 1),
    render: (r) => (
      <Badge size="xs" variant="light" color={r.open ? (r.days_overdue ? 'red' : 'orange') : 'gray'}>
        {r.open ? (r.days_overdue ? `open, ${r.days_overdue} d overdue` : 'open') : 'closed'}
      </Badge>
    ),
  },
  { key: 'due_date', header: 'Due', value: (r) => r.due_date, render: (r) => formatDate(r.due_date), nowrap: true },
];

const DOC_KINDS: Record<string, string> = { adr: 'ADR', requirements: 'Requirements', other: 'Other' };

const DOC_COLUMNS: Column<Doc>[] = [
  { key: 'title', header: 'Page', value: (r) => r.title },
  { key: 'kind', header: 'Kind', value: (r) => r.kind, render: (r) => DOC_KINDS[r.kind] ?? humanize(r.kind) },
  { key: 'last_updated', header: 'Last updated', value: (r) => r.last_updated, render: (r) => formatDate(r.last_updated), nowrap: true },
];

export default function DeliveryProjectPage() {
  const { projectId = '' } = useParams();
  const { query, search } = useFilters();
  const detail = useApi('/api/delivery/projects/{project_id}', { params: { project_id: projectId }, query });
  const data = detail.data;
  const project = data?.project;
  const progress = data?.progress;

  return (
    <Stack gap="md">
      <PageHeader
        title={project?.name ?? projectId}
        description={
          <Anchor component={Link} to={`/delivery${search}`} size="sm">
            Delivery portfolio
          </Anchor>
        }
        badges={project ? <RagBadge rag={project.computed_rag} /> : undefined}
      />
      {detail.error ? <ErrorState error={detail.error} onRetry={detail.reload} /> : null}
      {project ? (
        <SectionCard title="Project">
          <SimpleGrid cols={{ base: 2, md: 4 }} spacing="sm">
            <Field label="Phase" value={project.phase ?? '–'} />
            <Field label="Reported RAG" value={project.reported_rag ?? '–'} />
            <Field label="Target go-live" value={formatDate(project.target_date)} />
            <Field label="Forecast finish" value={project.forecast_finish ? formatDate(project.forecast_finish) : 'no velocity'} />
            <Field label="Jira projects" value={project.jira_keys.join(', ') || '–'} />
            <Field label="Confluence space" value={project.confluence_space ?? '–'} />
            <Field label="Plan status date" value={formatDate(project.plan_status_date)} />
            <Field label="Health reasons" value={project.reasons.join('; ') || 'on track'} />
          </SimpleGrid>
        </SectionCard>
      ) : null}
      <Grid gap="md">
        <Grid.Col span={{ base: 12, lg: 7 }}>
          <ChartCard
            title="Scope and delivered story points"
            description={
              progress
                ? `Velocity ${formatNumber(progress.velocity_per_week, 1)} points/week · scope +${formatNumber(progress.points_added_window, 0)} points recently (${formatPct(progress.scope_growth_pct, 0)})`
                : undefined
            }
            loading={detail.loading}
            empty={!progress?.weekly.length}
          >
            <LineChart data={progress?.weekly ?? []}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="week_ending" tick={{ fontSize: 11 }} />
              <YAxis tick={{ fontSize: 11 }} />
              <Tooltip />
              <Legend />
              <Line type="monotone" dataKey="scope_points" name="Scope" stroke={CHART_COLORS[1]} dot={false} />
              <Line type="monotone" dataKey="done_points" name="Done" stroke={CHART_COLORS[0]} dot={false} />
            </LineChart>
          </ChartCard>
        </Grid.Col>
        <Grid.Col span={{ base: 12, lg: 5 }}>
          <SectionCard title="Delivery risks" count={data?.findings.length ?? null}>
            <FindingList findings={data?.findings} emptyText="No delivery risks" compact />
          </SectionCard>
        </Grid.Col>
      </Grid>
      <SectionCard title="Plan" description={data ? `Latest of ${data.plan_versions} plan versions` : undefined} count={data?.tasks.length ?? null}>
        <DataTable rows={data?.tasks} columns={TASK_COLUMNS} rowKey={(r) => r.task_id} loading={detail.loading} emptyText="No plan imported" minWidth={900} />
      </SectionCard>
      <SectionCard title="RAID log" count={data?.raid.length ?? null}>
        <DataTable rows={data?.raid} columns={RAID_COLUMNS} rowKey={(r) => r.raid_id} loading={detail.loading} emptyText="No RAID items" minWidth={800} />
      </SectionCard>
      <SectionCard
        title="Requirements and design pages"
        description={data ? `${data.documents.requirements} requirement pages, ${data.documents.adrs} ADRs` : undefined}
        count={data?.documents.pages ?? null}
      >
        <DataTable rows={data?.documents.items} columns={DOC_COLUMNS} rowKey={(r) => r.page_id} loading={detail.loading} emptyText="No pages in the space" pageSize={10} />
      </SectionCard>
    </Stack>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <Stack gap={0}>
      <Text size="xs" c="dimmed">
        {label}
      </Text>
      <Text size="sm" style={{ overflowWrap: 'anywhere' }}>
        {value}
      </Text>
    </Stack>
  );
}
