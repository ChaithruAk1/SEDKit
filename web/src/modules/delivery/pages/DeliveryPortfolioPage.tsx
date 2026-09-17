import { Anchor, Badge, SimpleGrid, Stack, Text } from '@mantine/core';
import { Link } from 'react-router';

import type { Schema } from '../../../api/types';
import { useApi } from '../../../api/useApi';
import { type Column, DataTable } from '../../../components/DataTable';
import { ErrorState } from '../../../components/ErrorState';
import { FindingList } from '../../../components/FindingList';
import { formatDate, formatInt, formatMoney, formatPct } from '../../../components/format';
import { PageHeader } from '../../../components/PageHeader';
import { SectionCard } from '../../../components/SectionCard';
import { useFilters } from '../../../hooks/useFilters';

type ProjectRow = Schema<'DeliveryProjectRow'>;

export const RAG_COLOR: Record<string, string> = { red: 'red', amber: 'orange', green: 'teal' };

export function RagBadge({ rag, label }: { rag: string | null | undefined; label?: string }) {
  if (!rag) return <Text size="xs" c="dimmed">–</Text>;
  return (
    <Badge size="sm" variant="light" color={RAG_COLOR[rag] ?? 'gray'}>
      {label ? `${label} ${rag}` : rag}
    </Badge>
  );
}

export default function DeliveryPortfolioPage() {
  const { query, search } = useFilters();
  const portfolio = useApi('/api/delivery/portfolio', { query });
  const data = portfolio.data;

  const columns: Column<ProjectRow>[] = [
    {
      key: 'name',
      header: 'Project',
      value: (r) => r.name,
      render: (r) => (
        <Stack gap={0}>
          <Anchor component={Link} to={`/delivery/projects/${encodeURIComponent(r.project_id)}${search}`} size="sm">
            {r.name}
          </Anchor>
          <Text size="xs" c="dimmed">
            {`${r.project_id} · ${r.phase ?? 'phase n/a'}${r.app_raw ? ` · ${r.app_raw}` : ''}`}
          </Text>
        </Stack>
      ),
    },
    {
      key: 'computed_rag',
      header: 'Health',
      value: (r) => ({ red: 0, amber: 1, green: 2 })[r.computed_rag] ?? 3,
      render: (r) => (
        <Stack gap={2}>
          <RagBadge rag={r.computed_rag} />
          {r.reported_rag && r.reported_rag !== r.computed_rag ? (
            <Text size="xs" c="dimmed">{`reported ${r.reported_rag}`}</Text>
          ) : null}
        </Stack>
      ),
    },
    {
      key: 'reasons',
      header: 'Why',
      value: (r) => r.reasons.join('; '),
      render: (r) => (
        <Text size="xs" style={{ overflowWrap: 'anywhere' }}>
          {r.reasons.join('; ') || 'on track'}
        </Text>
      ),
    },
    {
      key: 'next_milestone',
      header: 'Next milestone',
      value: (r) => r.next_milestone?.finish ?? null,
      render: (r) =>
        r.next_milestone ? (
          <Stack gap={0}>
            <Text size="sm">{r.next_milestone.name}</Text>
            <Text size="xs" c={r.next_milestone.slip_days ? 'red' : 'dimmed'}>
              {formatDate(r.next_milestone.finish)}
              {r.next_milestone.slip_days ? ` (+${r.next_milestone.slip_days} d)` : ''}
            </Text>
          </Stack>
        ) : (
          '–'
        ),
    },
    { key: 'worst_slip_days', header: 'Worst slip (days)', value: (r) => r.worst_slip_days, render: (r) => formatInt(r.worst_slip_days), align: 'right' },
    { key: 'open_high_raid', header: 'Open high RAID', value: (r) => r.open_high_raid, render: (r) => formatInt(r.open_high_raid), align: 'right' },
    { key: 'points_done_pct', header: 'Points done', value: (r) => r.points_done_pct, render: (r) => formatPct(r.points_done_pct, 0), align: 'right' },
    {
      key: 'forecast_finish',
      header: 'Forecast / target',
      value: (r) => r.forecast_finish,
      render: (r) => (
        <Text size="xs" c={r.forecast_finish && r.target_date && r.forecast_finish > r.target_date ? 'red' : undefined}>
          {`${r.forecast_finish ? formatDate(r.forecast_finish) : 'no velocity'} / ${formatDate(r.target_date)}`}
        </Text>
      ),
      nowrap: true,
    },
    { key: 'budget_base', header: 'Budget', value: (r) => r.budget_base, render: (r) => formatMoney(r.budget_base, { compact: true }), align: 'right' },
  ];

  return (
    <Stack gap="md">
      <PageHeader
        title="Delivery portfolio"
        description={data ? `New business applications · as of ${formatDate(data.as_of)}` : 'New business applications'}
      />
      {portfolio.error ? <ErrorState error={portfolio.error} onRetry={portfolio.reload} /> : null}
      <SimpleGrid cols={{ base: 2, md: 4 }} spacing="sm">
        {(['projects', 'red', 'amber', 'green'] as const).map((key) => (
          <SectionCard key={key} title={key === 'projects' ? 'Projects' : `Health ${key}`}>
            <Text fz={28} fw={700} c={key === 'projects' ? undefined : RAG_COLOR[key]}>
              {data ? formatInt(data.counts[key] ?? 0) : '…'}
            </Text>
          </SectionCard>
        ))}
      </SimpleGrid>
      <SectionCard
        title="Projects"
        description="Health is computed from plan slips, RAID items, scope growth and forecast finish; the reported RAG is shown when it differs"
        count={data?.projects.length ?? null}
      >
        <DataTable
          rows={data?.projects}
          columns={columns}
          rowKey={(r) => r.project_id}
          loading={portfolio.loading}
          error={portfolio.error}
          onRetry={portfolio.reload}
          initialSort={{ key: 'computed_rag', dir: 'asc' }}
          emptyText="No delivery projects imported"
          emptyDescription="Drop the project register, plan exports and RAID log in the inbox and run `sed import --inbox`."
          minWidth={1100}
        />
      </SectionCard>
      <SectionCard title="Delivery risks" description="System-detected from plans, RAID log and Jira" count={data?.findings.length ?? null}>
        <FindingList
          findings={data?.findings}
          emptyText="No delivery risks"
          subjectHref={(f) => (f.subject_id ? `/delivery/projects/${encodeURIComponent(f.subject_id)}${search}` : null)}
        />
      </SectionCard>
    </Stack>
  );
}
