import {
  Badge,
  Grid,
  Group,
  MultiSelect,
  Pagination,
  SegmentedControl,
  Select,
  Stack,
  Switch,
  Tabs,
  Text,
  TextInput,
} from '@mantine/core';
import { useDebouncedCallback } from '@mantine/hooks';
import { IconSearch } from '@tabler/icons-react';
import { useEffect, useMemo, useState } from 'react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  LineChart,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import type { GetQuery, Schema } from '../../../api/types';
import { useApi } from '../../../api/useApi';
import { CHART_COLORS, ChartCard } from '../../../components/ChartCard';
import { type Column, DataTable } from '../../../components/DataTable';
import { ErrorState } from '../../../components/ErrorState';
import { FindingList } from '../../../components/FindingList';
import { formatHours, formatInt, formatPct } from '../../../components/format';
import { PageHeader } from '../../../components/PageHeader';
import { SectionCard } from '../../../components/SectionCard';
import { useFilters, usePatchSearchParams, useSearchParam } from '../../../hooks/useFilters';
import { TicketDrawer, useTicketParam } from '../components/TicketDrawer';
import { ticketColumns } from '../components/ticketColumns';
import { useFindings } from '../components/useFindings';
import { findingSubjectHref } from '../links';

type Granularity = 'week' | 'month';
type TicketQuery = NonNullable<GetQuery<'/api/ops/tickets'>>;
type Sort = NonNullable<TicketQuery['sort']>;

const SORTS: { value: Sort; label: string }[] = [
  { value: 'opened_desc', label: 'Newest first' },
  { value: 'opened_asc', label: 'Oldest first' },
  { value: 'priority', label: 'Priority' },
  { value: 'updated_desc', label: 'Recently updated' },
];
const KINDS = [
  { value: 'incident', label: 'Incidents' },
  { value: 'sc_req_item', label: 'Requests' },
  { value: 'problem', label: 'Problems' },
  { value: 'change_request', label: 'Changes' },
];

function useGranularity(): [Granularity, (g: Granularity) => void] {
  const [value, setValue] = useSearchParam('granularity', 'week');
  return [value === 'month' ? 'month' : 'week', (g) => setValue(g)];
}

function TrendsTab() {
  const { query } = useFilters();
  const [granularity, setGranularity] = useGranularity();
  const n = 12;
  const volumes = useApi('/api/ops/tickets/volumes', { query: { ...query, granularity, n, kind: 'incident' } });
  const sla = useApi('/api/ops/tickets/sla', { query: { ...query, granularity, n } });
  const mttr = useApi('/api/ops/tickets/mttr', { query: { ...query, granularity, n } });

  const switcher = (
    <SegmentedControl
      size="xs"
      value={granularity}
      onChange={(v) => setGranularity(v as Granularity)}
      data={[
        { value: 'week', label: 'Weekly' },
        { value: 'month', label: 'Monthly' },
      ]}
    />
  );

  const priorityColumns: Column<Schema<'SlaPriorityRow'>>[] = [
    { key: 'priority', header: 'Priority', value: (r) => r.priority },
    { key: 'pct', header: 'SLA met', value: (r) => r.pct, render: (r) => formatPct(r.pct), align: 'right' },
    { key: 'met', header: 'Met', value: (r) => r.met, render: (r) => formatInt(r.met), align: 'right' },
    { key: 'total', header: 'Resolved', value: (r) => r.total, render: (r) => formatInt(r.total), align: 'right' },
  ];

  return (
    <Stack gap="md">
      <Group justify="flex-end">{switcher}</Group>
      <ChartCard
        title="Incident volumes"
        description="Opened vs resolved per period; the line is the net change"
        loading={volumes.loading}
        error={volumes.error}
        onRetry={volumes.reload}
        empty={!volumes.data?.items.length}
      >
        <ComposedChart data={volumes.data?.items ?? []} margin={{ top: 8, right: 8, left: -12, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="period" tickMargin={6} />
          <YAxis allowDecimals={false} />
          <Tooltip />
          <Legend />
          <Bar dataKey="opened" name="Opened" fill={CHART_COLORS[0]} radius={[3, 3, 0, 0]} />
          <Bar dataKey="resolved" name="Resolved" fill={CHART_COLORS[2]} radius={[3, 3, 0, 0]} />
          <Line dataKey="net" name="Net" stroke={CHART_COLORS[1]} strokeWidth={2} dot={false} type="monotone" />
        </ComposedChart>
      </ChartCard>
      <Grid gap="md">
        <Grid.Col span={{ base: 12, lg: 6 }}>
          <ChartCard
            title="SLA met"
            description={sla.data ? `Resolution SLA, source: ${sla.data.sla_source}` : 'Resolution SLA'}
            loading={sla.loading}
            error={sla.error}
            onRetry={sla.reload}
            empty={!sla.data?.items.length}
          >
            <LineChart data={sla.data?.items ?? []} margin={{ top: 8, right: 8, left: -12, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="period" tickMargin={6} />
              <YAxis domain={[0, 100]} unit="%" />
              <Tooltip formatter={(value) => formatPct(Number(value))} />
              <Line dataKey="pct" name="SLA met" stroke={CHART_COLORS[0]} strokeWidth={2} type="monotone" connectNulls />
            </LineChart>
          </ChartCard>
        </Grid.Col>
        <Grid.Col span={{ base: 12, lg: 6 }}>
          <ChartCard
            title="Time to resolve"
            description="Calendar hours from opened to resolved"
            loading={mttr.loading}
            error={mttr.error}
            onRetry={mttr.reload}
            empty={!mttr.data?.items.length}
          >
            <LineChart data={mttr.data?.items ?? []} margin={{ top: 8, right: 8, left: -12, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="period" tickMargin={6} />
              <YAxis unit="h" />
              <Tooltip formatter={(value) => formatHours(Number(value))} />
              <Legend />
              <Line dataKey="median_h" name="Median" stroke={CHART_COLORS[0]} strokeWidth={2} type="monotone" connectNulls />
              <Line dataKey="mean_h" name="Mean" stroke={CHART_COLORS[5]} strokeWidth={1.5} type="monotone" connectNulls />
              <Line dataKey="p90_h" name="P90" stroke={CHART_COLORS[4]} strokeDasharray="4 3" type="monotone" connectNulls />
            </LineChart>
          </ChartCard>
        </Grid.Col>
      </Grid>
      <SectionCard title="SLA by priority" description="Across the charted periods">
        <DataTable
          rows={sla.data?.by_priority}
          columns={priorityColumns}
          rowKey={(r) => r.priority}
          loading={sla.loading}
          error={sla.error}
          serverOrdered
          minWidth={360}
        />
      </SectionCard>
    </Stack>
  );
}

function BacklogTab() {
  const { query } = useFilters();
  const backlog = useApi('/api/ops/tickets/backlog', { query });
  const data = backlog.data;
  const aging = data
    ? [
        { bucket: '0-7 d', count: data.aging.d0_7 },
        { bucket: '8-30 d', count: data.aging.d8_30 },
        { bucket: '31-90 d', count: data.aging.d31_90 },
        { bucket: '> 90 d', count: data.aging.d90p },
      ]
    : [];
  const flow = useMemo(() => {
    const byPeriod = new Map<string, { period: string; arrived: number; closed: number }>();
    for (const row of data?.flow ?? []) {
      const entry = byPeriod.get(row.period) ?? { period: row.period, arrived: 0, closed: 0 };
      entry.arrived += row.arrived;
      entry.closed += row.closed;
      byPeriod.set(row.period, entry);
    }
    return [...byPeriod.values()];
  }, [data]);

  const groupColumns: Column<Schema<'BacklogGroupRow'>>[] = [
    { key: 'group', header: 'Assignment group', value: (r) => r.group ?? 'Unassigned' },
    { key: 'total', header: 'Open', value: (r) => r.total, render: (r) => formatInt(r.total), align: 'right' },
    { key: 'd0_7', header: '0-7 d', value: (r) => r.d0_7, align: 'right' },
    { key: 'd8_30', header: '8-30 d', value: (r) => r.d8_30, align: 'right' },
    { key: 'd31_90', header: '31-90 d', value: (r) => r.d31_90, align: 'right' },
    {
      key: 'd90p',
      header: '> 90 d',
      value: (r) => r.d90p,
      render: (r) => <Text size="sm" c={r.d90p ? 'red' : undefined}>{formatInt(r.d90p)}</Text>,
      align: 'right',
    },
  ];

  return (
    <Stack gap="md">
      <Group gap="xs">
        <Badge size="lg" variant="light">
          {data ? `${formatInt(data.total)} open incidents` : 'Backlog'}
        </Badge>
        {data ? (
          <Text size="sm" c="dimmed">
            at {data.at}; {formatInt(data.stale_excluded)} stale-open tickets excluded
          </Text>
        ) : null}
      </Group>
      <Grid gap="md">
        <Grid.Col span={{ base: 12, lg: 5 }}>
          <ChartCard
            title="Backlog aging"
            loading={backlog.loading}
            error={backlog.error}
            onRetry={backlog.reload}
            empty={!data}
            height={240}
          >
            <BarChart data={aging} margin={{ top: 8, right: 8, left: -12, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="bucket" />
              <YAxis allowDecimals={false} />
              <Tooltip />
              <Bar dataKey="count" name="Open incidents" fill={CHART_COLORS[0]} radius={[3, 3, 0, 0]} />
            </BarChart>
          </ChartCard>
        </Grid.Col>
        <Grid.Col span={{ base: 12, lg: 7 }}>
          <ChartCard
            title="Flow"
            description="Arrived vs closed per period, all groups"
            loading={backlog.loading}
            error={backlog.error}
            onRetry={backlog.reload}
            empty={flow.length === 0}
            height={240}
          >
            <BarChart data={flow} margin={{ top: 8, right: 8, left: -12, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="period" />
              <YAxis allowDecimals={false} />
              <Tooltip />
              <Legend />
              <Bar dataKey="arrived" name="Arrived" fill={CHART_COLORS[1]} radius={[3, 3, 0, 0]} />
              <Bar dataKey="closed" name="Closed" fill={CHART_COLORS[2]} radius={[3, 3, 0, 0]} />
            </BarChart>
          </ChartCard>
        </Grid.Col>
      </Grid>
      <SectionCard title="Backlog by assignment group" count={data?.by_group.length ?? null}>
        <DataTable
          rows={data?.by_group}
          columns={groupColumns}
          rowKey={(r) => r.group ?? '(unassigned)'}
          loading={backlog.loading}
          error={backlog.error}
          onRetry={backlog.reload}
          initialSort={{ key: 'total', dir: 'desc' }}
          emptyText="No open incidents"
          minWidth={560}
        />
      </SectionCard>
    </Stack>
  );
}

const PAGE_SIZE = 50;

function SearchTab() {
  const { query } = useFilters();
  const patch = usePatchSearchParams();
  const [q] = useSearchParam('q', '');
  const [kind] = useSearchParam('kind', '');
  const [priorityParam] = useSearchParam('priority', '');
  const [openParam] = useSearchParam('open', '');
  const [staleParam] = useSearchParam('stale', '');
  const [sortParam] = useSearchParam('sort', 'opened_desc');
  const [pageParam] = useSearchParam('page', '1');
  const [, setTicket] = useTicketParam();
  const [text, setText] = useState(q);

  // Follow the URL (back/forward, links) without clobbering what is being typed (e.g. a trailing space).
  useEffect(() => {
    setText((current) => (current.trim() === q ? current : q));
  }, [q]);
  const pushText = useDebouncedCallback((value: string) => patch({ q: value.trim().slice(0, 200) || null, page: null }), 350);

  const priorities = priorityParam
    .split(',')
    .map(Number)
    .filter((p) => Number.isInteger(p) && p >= 1 && p <= 5);
  const sort = (SORTS.find((s) => s.value === sortParam)?.value ?? 'opened_desc') satisfies Sort;
  const page = Math.max(1, Number.parseInt(pageParam, 10) || 1);

  const tickets = useApi('/api/ops/tickets', {
    query: {
      ...query,
      q: q || null,
      kind: kind || null,
      priority: priorities.length ? priorities : undefined,
      open: openParam === 'open' ? true : openParam === 'closed' ? false : null,
      stale: staleParam === 'true' ? true : null,
      sort,
      page,
      page_size: PAGE_SIZE,
    },
  });
  const data = tickets.data;
  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;
  const columns = useMemo(() => ticketColumns(), []);

  return (
    <Stack gap="md">
      <Group gap="xs" align="flex-end" wrap="wrap">
        <TextInput
          label="Search"
          description="Scrubbed ticket text; word* matches prefixes"
          placeholder="e.g. interface timeout"
          leftSection={<IconSearch size={14} />}
          value={text}
          maxLength={200}
          onChange={(event) => {
            setText(event.currentTarget.value);
            pushText(event.currentTarget.value);
          }}
          w={340}
        />
        <Select
          label="Kind"
          placeholder="All kinds"
          data={KINDS}
          value={kind || null}
          onChange={(value) => patch({ kind: value, page: null })}
          clearable
          w={150}
        />
        <MultiSelect
          label="Priority"
          placeholder={priorities.length ? undefined : 'Any'}
          data={['1', '2', '3', '4', '5'].map((p) => ({ value: p, label: `P${p}` }))}
          value={priorities.map(String)}
          onChange={(value) => patch({ priority: value.join(',') || null, page: null })}
          w={170}
        />
        <SegmentedControl
          value={openParam || 'any'}
          onChange={(value) => patch({ open: value === 'any' ? null : value, page: null })}
          data={[
            { value: 'any', label: 'Any' },
            { value: 'open', label: 'Open' },
            { value: 'closed', label: 'Closed' },
          ]}
        />
        <Switch
          label="Stale open only"
          checked={staleParam === 'true'}
          onChange={(event) => patch({ stale: event.currentTarget.checked ? 'true' : null, page: null })}
          mb={8}
        />
        <Select
          label="Sort"
          data={SORTS}
          value={sort}
          onChange={(value) => patch({ sort: value === 'opened_desc' ? null : value, page: null })}
          allowDeselect={false}
          w={170}
        />
      </Group>
      <SectionCard
        title="Tickets"
        count={data?.total ?? null}
        description={data ? `Page ${data.page} of ${pages}` : undefined}
        actions={
          pages > 1 ? (
            <Pagination size="sm" total={pages} value={Math.min(page, pages)} onChange={(p) => patch({ page: p === 1 ? null : String(p) })} />
          ) : undefined
        }
      >
        <DataTable
          rows={data?.items}
          columns={columns}
          rowKey={(t) => t.ticket_id}
          loading={tickets.loading}
          error={tickets.error}
          onRetry={tickets.reload}
          onRowClick={(t) => setTicket(t.ticket_id)}
          rowLabel={(t) => `Open ticket ${t.number}`}
          serverOrdered
          emptyText="No tickets match"
          emptyDescription="Adjust the search or filters."
          minWidth={1100}
        />
        {pages > 1 ? (
          <Group justify="flex-end" mt="sm">
            <Pagination size="sm" total={pages} value={Math.min(page, pages)} onChange={(p) => patch({ page: p === 1 ? null : String(p) })} />
          </Group>
        ) : null}
      </SectionCard>
    </Stack>
  );
}

function RecurringTab() {
  const { filters, search } = useFilters();
  const clusters = useFindings({ kind: 'issue_cluster', as_of: filters.as_of, limit: 200 }, filters.include_drafts);
  return (
    <SectionCard
      title="Recurring issues"
      description={`${
        filters.include_drafts
          ? 'Issue clusters: approved and draft AI findings'
          : 'Approved issue clusters (AI findings reviewed by a person); turn on "Include AI drafts" to see unreviewed ones'
      }${filters.app.length || filters.family || filters.vendor || filters.group ? ' · whole portfolio (filters not applied)' : ''}`}
      count={clusters.items?.length ?? null}
    >
      {clusters.error ? <ErrorState error={clusters.error} onRetry={clusters.reload} compact /> : null}
      <FindingList
        findings={clusters.items}
        emptyText={clusters.loading ? 'Loading…' : 'No recurring issues published'}
        subjectHref={(f) => findingSubjectHref(f, search)}
      />
    </SectionCard>
  );
}

const TABS = ['trends', 'backlog', 'recurring', 'search'] as const;

export default function TicketsPage() {
  const [tabParam, setTab] = useSearchParam('tab', 'trends');
  const tab = (TABS as readonly string[]).includes(tabParam) ? tabParam : 'trends';
  return (
    <Stack gap="md">
      <PageHeader title="Tickets" description="Trends, backlog, recurring issues and full-text search. Click a ticket for details." />
      <Tabs value={tab} onChange={(value) => setTab(value)} keepMounted={false}>
        <Tabs.List mb="md">
          <Tabs.Tab value="trends">Trends</Tabs.Tab>
          <Tabs.Tab value="backlog">Backlog</Tabs.Tab>
          <Tabs.Tab value="recurring">Recurring issues</Tabs.Tab>
          <Tabs.Tab value="search">Search</Tabs.Tab>
        </Tabs.List>
        <Tabs.Panel value="trends">
          <TrendsTab />
        </Tabs.Panel>
        <Tabs.Panel value="backlog">
          <BacklogTab />
        </Tabs.Panel>
        <Tabs.Panel value="recurring">
          <RecurringTab />
        </Tabs.Panel>
        <Tabs.Panel value="search">
          <SearchTab />
        </Tabs.Panel>
      </Tabs>
      <TicketDrawer />
    </Stack>
  );
}
