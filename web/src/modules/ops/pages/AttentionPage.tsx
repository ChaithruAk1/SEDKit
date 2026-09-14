import { Alert, Badge, Chip, Group, Stack, Text } from '@mantine/core';
import { IconClock } from '@tabler/icons-react';
import { useMemo } from 'react';

import type { Schema } from '../../../api/types';
import { useApi } from '../../../api/useApi';
import { type Column, DataTable } from '../../../components/DataTable';
import { formatDate, formatDateTime, formatDays } from '../../../components/format';
import { PageHeader } from '../../../components/PageHeader';
import { SectionCard } from '../../../components/SectionCard';
import { useFilters, useSearchParam } from '../../../hooks/useFilters';
import { TicketDrawer, useTicketParam } from '../components/TicketDrawer';
import { PriorityBadge } from '../components/ticketColumns';

type AttentionRow = Schema<'AttentionRow'>;

/** `by_reason` groups "P1 open"/"P2 open" as P1/P2; the same key is used to filter rows here. */
function reasonKey(reason: string): string {
  return /^P\d\b/.test(reason) ? (reason.split(' ')[0] ?? reason) : reason;
}

const REASON_COLOR: Record<string, string> = {
  P1: 'red',
  P2: 'orange',
  'past SLA target': 'red',
  'near SLA target': 'yellow',
  reopened: 'grape',
  'ping-pong reassignments': 'violet',
  unassigned: 'blue',
};

export default function AttentionPage() {
  const { query } = useFilters();
  const attention = useApi('/api/ops/attention', { query: { ...query, limit: 1000 } });
  const [reasonParam, setReasonParam] = useSearchParam('reason', '');
  const [, setTicket] = useTicketParam();
  const selected = useMemo(() => reasonParam.split('|').filter(Boolean), [reasonParam]);
  const data = attention.data;

  const rows = useMemo(
    () =>
      (data?.items ?? []).filter(
        (row) => selected.length === 0 || row.reasons.some((reason) => selected.includes(reasonKey(reason))),
      ),
    [data, selected],
  );

  const columns: Column<AttentionRow>[] = [
    {
      key: 'number',
      header: 'Number',
      value: (r) => r.number,
      render: (r) => (
        <Text size="sm" fw={600} ff="monospace">
          {r.number}
        </Text>
      ),
      nowrap: true,
    },
    { key: 'priority', header: 'Prio', value: (r) => r.priority, render: (r) => <PriorityBadge priority={r.priority} /> },
    {
      key: 'reasons',
      header: 'Why',
      value: (r) => r.reasons.length,
      render: (r) => (
        <Group gap={4}>
          {r.reasons.map((reason) => (
            <Badge key={reason} size="xs" variant="light" color={REASON_COLOR[reasonKey(reason)] ?? 'gray'} tt="none">
              {reason}
            </Badge>
          ))}
        </Group>
      ),
    },
    {
      key: 'short_description',
      header: 'Short description',
      value: (r) => r.short_description,
      render: (r) => (
        <Text size="sm" lineClamp={2}>
          {r.short_description ?? '–'}
        </Text>
      ),
    },
    { key: 'app', header: 'Application', value: (r) => r.app },
    { key: 'state', header: 'State', value: (r) => r.state },
    { key: 'assignment_group', header: 'Group', value: (r) => r.assignment_group ?? 'Unassigned' },
    { key: 'assigned_to_pid', header: 'Assignee', value: (r) => r.assigned_to_pid ?? 'Unassigned' },
    { key: 'age_days', header: 'Age', value: (r) => r.age_days, render: (r) => formatDays(r.age_days, 1), align: 'right', nowrap: true },
    { key: 'opened_at', header: 'Opened', value: (r) => r.opened_at, render: (r) => formatDateTime(r.opened_at), nowrap: true },
  ];

  const reasons = Object.entries(data?.by_reason ?? {}).sort(([, a], [, b]) => b - a);

  return (
    <Stack gap="md">
      <PageHeader
        title="Needs attention"
        description="Deterministic list of open incidents: P1/P2, near or past SLA, aged, reopened, ping-pong reassignments, unassigned."
        badges={data ? <Badge size="lg" variant="light" color="orange">{data.count}</Badge> : null}
      />
      {data ? (
        <Alert variant="light" color="gray" icon={<IconClock size={18} />} py="xs">
          <Text size="sm">
            As of the last export: data {formatDate(data.data_as_of_last_import)}, evaluated at {formatDate(data.as_of)}.
            States may have changed in ServiceNow since then.
          </Text>
        </Alert>
      ) : null}
      {reasons.length > 0 ? (
        <Chip.Group multiple value={selected} onChange={(value) => setReasonParam(value.join('|') || null)}>
          <Group gap={6}>
            {reasons.map(([reason, count]) => (
              <Chip key={reason} value={reason} size="xs" variant="outline" color={REASON_COLOR[reason] ?? 'gray'}>
                {reason} ({count})
              </Chip>
            ))}
          </Group>
        </Chip.Group>
      ) : null}
      <SectionCard
        title="Incidents"
        count={data ? rows.length : null}
        description={data && data.items.length < data.count ? `Showing the first ${data.items.length} of ${data.count}` : undefined}
      >
        <DataTable
          rows={data ? rows : undefined}
          columns={columns}
          rowKey={(r) => r.ticket_id}
          loading={attention.loading}
          error={attention.error}
          onRetry={attention.reload}
          onRowClick={(r) => setTicket(r.ticket_id)}
          rowLabel={(r) => `Open ticket ${r.number}`}
          emptyText="Nothing needs attention"
          initialSort={{ key: 'priority', dir: 'asc' }}
          pageSize={50}
          minWidth={1100}
        />
      </SectionCard>
      <TicketDrawer />
    </Stack>
  );
}
