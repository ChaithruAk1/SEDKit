import { Anchor, Badge, Grid, Group, SegmentedControl, Select, SimpleGrid, Stack, Text } from '@mantine/core';
import { useMemo } from 'react';
import { Link } from 'react-router';
import { Bar, BarChart, CartesianGrid, ComposedChart, Legend, Line, LineChart, Tooltip, XAxis, YAxis } from 'recharts';

import type { Schema } from '../../../api/types';
import { useApi } from '../../../api/useApi';
import { CHART_COLORS, ChartCard } from '../../../components/ChartCard';
import { type Column, DataTable } from '../../../components/DataTable';
import { ErrorState } from '../../../components/ErrorState';
import { formatDate, formatInt, formatNumber, formatPct } from '../../../components/format';
import { PageHeader } from '../../../components/PageHeader';
import { SectionCard } from '../../../components/SectionCard';
import { useFilters, useSearchParam } from '../../../hooks/useFilters';
import { ticketHref } from '../links';

type AttentionRow = Schema<'SapAttentionRow'>;
type PriorityRow = Schema<'SapSlaPriorityRow'>;
type AreaFlow = { area: string; label: string; arrived: number; closed: number; net: number };

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <Stack gap={0}>
      <Text size="xs" c="dimmed" fw={600} tt="uppercase">
        {label}
      </Text>
      <Text fz={22} fw={700}>
        {value}
      </Text>
    </Stack>
  );
}

export default function SapTicketsPage() {
  const { query, search } = useFilters();
  const [area, setArea] = useSearchParam('area', '');
  const [landscape, setLandscape] = useSearchParam('landscape', '');
  const l3 = useApi('/api/sap/l3', {
    query: { ...query, area: area || undefined, landscape: landscape || undefined },
  });
  const data = l3.data;

  const flowByArea = useMemo<AreaFlow[]>(() => {
    const acc = new Map<string, AreaFlow>();
    for (const row of data?.flow ?? []) {
      const item = acc.get(row.area) ?? { area: row.area, label: row.label, arrived: 0, closed: 0, net: 0 };
      item.arrived += row.arrived;
      item.closed += row.closed;
      item.net += row.net;
      acc.set(row.area, item);
    }
    return [...acc.values()];
  }, [data]);
  const flowWeeks = new Set((data?.flow ?? []).map((r) => r.period)).size;

  const attentionColumns: Column<AttentionRow>[] = [
    {
      key: 'number',
      header: 'Number',
      value: (r) => r.number,
      render: (r) => (
        <Anchor component={Link} to={ticketHref(r.ticket_id, search)} size="sm">
          {r.number}
        </Anchor>
      ),
      nowrap: true,
    },
    { key: 'priority', header: 'P', value: (r) => r.priority, render: (r) => (r.priority ? `P${r.priority}` : '–') },
    { key: 'area', header: 'Area', value: (r) => r.area_label },
    { key: 'app', header: 'Application', value: (r) => r.app },
    { key: 'state', header: 'State', value: (r) => r.state },
    { key: 'age', header: 'Age (days)', value: (r) => r.age_days, render: (r) => formatNumber(r.age_days, 1), align: 'right' },
    { key: 'reasons', header: 'Why', value: (r) => r.reasons },
    { key: 'short', header: 'Short description', value: (r) => r.short_description },
  ];
  const priorityColumns: Column<PriorityRow>[] = [
    { key: 'priority', header: 'Priority', value: (r) => r.priority },
    { key: 'pct', header: 'SLA met', value: (r) => r.pct, render: (r) => formatPct(r.pct), align: 'right' },
    { key: 'met', header: 'Met', value: (r) => r.met, render: (r) => formatInt(r.met), align: 'right' },
    { key: 'total', header: 'Resolved', value: (r) => r.total, render: (r) => formatInt(r.total), align: 'right' },
  ];
  const flowColumns: Column<AreaFlow>[] = [
    { key: 'label', header: 'Area', value: (r) => r.label },
    { key: 'arrived', header: 'Arrived', value: (r) => r.arrived, render: (r) => formatInt(r.arrived), align: 'right' },
    { key: 'closed', header: 'Closed', value: (r) => r.closed, render: (r) => formatInt(r.closed), align: 'right' },
    {
      key: 'net',
      header: 'Net',
      value: (r) => r.net,
      render: (r) => (
        <Text span size="sm" c={r.net > 0 ? 'red' : undefined} fw={r.net > 0 ? 600 : undefined}>
          {r.net > 0 ? `+${formatInt(r.net)}` : formatInt(r.net)}
        </Text>
      ),
      align: 'right',
    },
  ];

  const selectors = (
    <Group gap="sm" wrap="wrap">
      <Select
        size="xs"
        placeholder="All areas"
        clearable
        value={area || null}
        onChange={(value) => setArea(value)}
        data={(data?.areas ?? []).map((o) => ({ value: o.value, label: o.label }))}
        aria-label="SAP area"
        w={200}
      />
      <SegmentedControl
        size="xs"
        value={landscape || 'all'}
        onChange={(value) => setLandscape(value === 'all' ? null : value)}
        data={[{ value: 'all', label: 'All landscapes' }, ...(data?.landscapes ?? []).map((o) => ({ value: o.value, label: o.label }))]}
      />
    </Group>
  );

  return (
    <Stack gap="md">
      <PageHeader
        title="SAP L3 tickets"
        description={
          data
            ? `Backlog at ${formatDate(data.at)} · SLA source ${data.sla_source}`
            : 'SAP incidents by area and landscape'
        }
        actions={selectors}
      />
      {l3.error ? <ErrorState error={l3.error} onRetry={l3.reload} /> : null}

      <SimpleGrid cols={{ base: 2, md: 5 }} spacing="sm">
        <Stat label="Open" value={formatInt(data?.backlog_total)} />
        <Stat label="0–7 days" value={formatInt(data?.aging.d0_7)} />
        <Stat label="8–30 days" value={formatInt(data?.aging.d8_30)} />
        <Stat label="31–90 days" value={formatInt(data?.aging.d31_90)} />
        <Stat label="> 90 days" value={formatInt(data?.aging.d90p)} />
      </SimpleGrid>

      <ChartCard
        title="SAP incidents per week"
        description="Opened vs resolved; the line is the net change"
        loading={l3.loading}
        error={l3.error}
        onRetry={l3.reload}
        empty={!data?.trend.length}
      >
        <ComposedChart data={data?.trend ?? []} margin={{ top: 8, right: 8, left: -12, bottom: 0 }}>
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
        <Grid.Col span={{ base: 12, lg: 7 }}>
          <ChartCard
            title="Backlog aging by area"
            description="Open SAP incidents by age"
            loading={l3.loading}
            error={l3.error}
            onRetry={l3.reload}
            empty={!data?.by_area.length}
          >
            <BarChart data={data?.by_area ?? []} layout="vertical" margin={{ top: 8, right: 8, left: 24, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" horizontal={false} />
              <XAxis type="number" allowDecimals={false} />
              <YAxis type="category" dataKey="label" width={120} />
              <Tooltip />
              <Legend />
              <Bar dataKey="d0_7" name="0–7d" stackId="age" fill={CHART_COLORS[2]} />
              <Bar dataKey="d8_30" name="8–30d" stackId="age" fill={CHART_COLORS[0]} />
              <Bar dataKey="d31_90" name="31–90d" stackId="age" fill={CHART_COLORS[3]} />
              <Bar dataKey="d90p" name=">90d" stackId="age" fill={CHART_COLORS[1]} />
            </BarChart>
          </ChartCard>
        </Grid.Col>
        <Grid.Col span={{ base: 12, lg: 5 }}>
          <ChartCard
            title="SLA met per week"
            description="Resolution SLA of SAP incidents resolved each week"
            loading={l3.loading}
            error={l3.error}
            onRetry={l3.reload}
            empty={!data?.trend.length}
          >
            <LineChart data={data?.trend ?? []} margin={{ top: 8, right: 8, left: -12, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="period" tickMargin={6} />
              <YAxis domain={[0, 100]} unit="%" />
              <Tooltip formatter={(value) => formatPct(Number(value))} />
              <Line dataKey="sla_pct" name="SLA met" stroke={CHART_COLORS[0]} strokeWidth={2} type="monotone" connectNulls />
            </LineChart>
          </ChartCard>
        </Grid.Col>
      </Grid>

      <Grid gap="md">
        <Grid.Col span={{ base: 12, lg: 7 }}>
          <SectionCard
            title="Arrivals vs closures by area"
            description={`Last ${flowWeeks || 8} weeks; a positive net means the backlog grew`}
          >
            <DataTable rows={flowByArea} columns={flowColumns} rowKey={(r) => r.area} loading={l3.loading} emptyText="No SAP tickets in these weeks" minWidth={360} />
          </SectionCard>
        </Grid.Col>
        <Grid.Col span={{ base: 12, lg: 5 }}>
          <SectionCard title="SLA by priority" description={`Resolved in ${data?.trend.at(-1)?.period ?? 'the last week'}`}>
            <DataTable rows={data?.sla_by_priority} columns={priorityColumns} rowKey={(r) => r.priority} loading={l3.loading} emptyText="Nothing resolved" minWidth={320} serverOrdered />
          </SectionCard>
        </Grid.Col>
      </Grid>

      <SectionCard
        title="Needs attention"
        description="Open SAP incidents: P1/P2, near or past SLA, aged, reopened, ping-pong reassignments or unassigned"
        count={data?.attention_count ?? null}
        actions={data && data.attention_count > data.attention.length ? <Badge variant="light">first {data.attention.length}</Badge> : null}
      >
        <DataTable rows={data?.attention} columns={attentionColumns} rowKey={(r) => r.number} loading={l3.loading} emptyText="Nothing needs attention" pageSize={25} serverOrdered />
      </SectionCard>
    </Stack>
  );
}
