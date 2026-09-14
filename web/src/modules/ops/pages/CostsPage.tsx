import { Badge, Grid, Group, SegmentedControl, Select, Stack, Text } from '@mantine/core';
import { useMemo } from 'react';
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, Tooltip, XAxis, YAxis } from 'recharts';

import type { GetQuery, Schema } from '../../../api/types';
import { useApi } from '../../../api/useApi';
import { CHART_COLORS, ChartCard } from '../../../components/ChartCard';
import { type Column, DataTable } from '../../../components/DataTable';
import { ErrorState } from '../../../components/ErrorState';
import { FindingList } from '../../../components/FindingList';
import { formatMoney, formatPct, formatPp } from '../../../components/format';
import { PageHeader } from '../../../components/PageHeader';
import { SectionCard } from '../../../components/SectionCard';
import { useFilters, useSearchParam } from '../../../hooks/useFilters';
import { licenseColumns, renewalColumns } from '../components/contractColumns';
import { useFindings } from '../components/useFindings';
import { findingSubjectHref } from '../links';

type GroupBy = NonNullable<NonNullable<GetQuery<'/api/ops/costs'>>['group_by']>;
const GROUP_BY: { value: GroupBy; label: string }[] = [
  { value: 'app', label: 'Application' },
  { value: 'vendor', label: 'Vendor' },
  { value: 'category', label: 'Category' },
  { value: 'app_category', label: 'App / category' },
];
const MONTH_CHOICES = ['1', '3', '6', '12', '24'];
const DAY_CHOICES = ['90', '180', '365'];
const RISK_KINDS = ['renewal_risk', 'license_risk', 'vendor_risk', 'cost_risk'] as const;
const CHART_ROWS = 12;

const COST_COLUMNS: Column<Schema<'CostRow'>>[] = [
  { key: 'label', header: 'Group', value: (r) => r.label },
  { key: 'actual', header: 'Actual', value: (r) => r.actual, render: (r) => formatMoney(r.actual), align: 'right', nowrap: true },
  { key: 'budget', header: 'Budget', value: (r) => r.budget, render: (r) => formatMoney(r.budget), align: 'right', nowrap: true },
  {
    key: 'variance_pct',
    header: 'Variance',
    value: (r) => r.variance_pct,
    render: (r) => (
      <Text size="sm" c={r.variance_pct === null ? 'dimmed' : r.variance_pct > 0 ? 'red' : 'teal'}>
        {r.variance_pct === null ? 'no budget' : `${r.variance_pct > 0 ? '+' : ''}${formatPct(r.variance_pct)}`}
      </Text>
    ),
    align: 'right',
  },
];

const VENDOR_COLUMNS: Column<Schema<'VendorTrendRow'>>[] = [
  { key: 'vendor', header: 'Vendor', value: (v) => v.vendor },
  {
    key: 'delta_pp',
    header: 'SLA change (3 m vs prior 3 m)',
    value: (v) => v.delta_pp,
    render: (v) => (
      <Text size="sm" fw={600} c={v.delta_pp === null ? 'dimmed' : v.delta_pp < 0 ? 'red' : 'teal'}>
        {v.delta_pp === null ? 'too few tickets' : formatPp(v.delta_pp)}
      </Text>
    ),
    align: 'right',
  },
  {
    key: 'latest',
    header: 'Latest SLA',
    value: (v) => v.series[v.series.length - 1]?.sla_pct ?? null,
    render: (v) => formatPct(v.series[v.series.length - 1]?.sla_pct ?? null),
    align: 'right',
  },
  {
    key: 'tickets',
    header: 'Tickets (period)',
    value: (v) => v.series.reduce((s, p) => s + p.tickets, 0),
    align: 'right',
  },
];

export default function CostsPage() {
  const { filters, query, search } = useFilters();
  const [groupByParam, setGroupBy] = useSearchParam('group_by', 'app');
  const [monthsParam, setMonths] = useSearchParam('months', '3');
  const [daysParam, setDays] = useSearchParam('days', '180');
  const groupBy = GROUP_BY.find((g) => g.value === groupByParam)?.value ?? 'app';
  const months = MONTH_CHOICES.includes(monthsParam) ? Number(monthsParam) : 3;
  const days = DAY_CHOICES.includes(daysParam) ? Number(daysParam) : 180;

  const costs = useApi('/api/ops/costs', { query: { ...query, group_by: groupBy, months } });
  const renewals = useApi('/api/ops/contracts/renewals', { query: { ...query, days } });
  const licenses = useApi('/api/ops/licenses/utilization', { query });
  const vendors = useApi('/api/ops/vendors/sla-trend', { query: { ...query, months: 6 } });
  const risks = useFindings({ as_of: filters.as_of, limit: 500 }, filters.include_drafts, RISK_KINDS);

  const chartRows = useMemo(() => (costs.data?.rows ?? []).slice(0, CHART_ROWS), [costs.data]);
  const vendorSeries = useMemo(() => {
    const items = vendors.data?.items ?? [];
    const periods = [...new Set(items.flatMap((v) => v.series.map((p) => p.period)))].sort();
    return periods.map((period) => {
      const point: Record<string, string | number | null> = { period };
      for (const v of items) point[v.vendor_id] = v.series.find((p) => p.period === period)?.sla_pct ?? null;
      return point;
    });
  }, [vendors.data]);
  const vendorLines = (vendors.data?.items ?? []).slice(0, CHART_COLORS.length);

  const totals = costs.data;
  // No variance without imported actuals (as the Overview KPI): a missing actual is not zero spend.
  const variance =
    totals && totals.total_budget && totals.total_actual !== null
      ? (100 * (totals.total_actual - totals.total_budget)) / totals.total_budget
      : null;

  return (
    <Stack gap="md">
      <PageHeader title="Costs & Contracts" description="Spend vs budget, renewal timeline, licence utilisation, vendor SLA trend and risk findings." />

      <ChartCard
        title="Spend vs budget"
        description={
          totals
            ? `${totals.months.join(', ')} · actual ${formatMoney(totals.total_actual)} vs budget ${formatMoney(totals.total_budget)}${
                variance === null ? '' : ` (${variance > 0 ? '+' : ''}${formatPct(variance)})`
              }${totals.rows.length > CHART_ROWS ? ` · chart shows the top ${CHART_ROWS}` : ''}`
            : undefined
        }
        actions={
          <Group gap="xs">
            <SegmentedControl size="xs" value={groupBy} onChange={setGroupBy} data={GROUP_BY} />
            <Select
              size="xs"
              w={120}
              value={String(months)}
              onChange={(value) => setMonths(value)}
              data={MONTH_CHOICES.map((m) => ({ value: m, label: `${m} month${m === '1' ? '' : 's'}` }))}
              allowDeselect={false}
              aria-label="Months"
            />
          </Group>
        }
        loading={costs.loading}
        error={costs.error}
        onRetry={costs.reload}
        empty={chartRows.length === 0}
        height={300}
      >
        <BarChart data={chartRows} margin={{ top: 8, right: 8, left: 8, bottom: 40 }}>
          <CartesianGrid strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="label" interval={0} angle={-25} textAnchor="end" height={60} />
          <YAxis tickFormatter={(v: number) => formatMoney(v, { compact: true })} width={70} />
          <Tooltip formatter={(value) => formatMoney(Number(value))} />
          <Legend verticalAlign="top" />
          <Bar dataKey="actual" name="Actual" fill={CHART_COLORS[0]} radius={[3, 3, 0, 0]} />
          <Bar dataKey="budget" name="Budget" fill={CHART_COLORS[6]} radius={[3, 3, 0, 0]} />
        </BarChart>
      </ChartCard>

      <SectionCard title="Spend by group" count={costs.data?.rows.length ?? null}>
        <DataTable
          rows={costs.data?.rows}
          columns={COST_COLUMNS}
          rowKey={(r) => r.key}
          loading={costs.loading}
          error={costs.error}
          onRetry={costs.reload}
          emptyText="No cost lines in these months"
          initialSort={{ key: 'actual', dir: 'desc' }}
          pageSize={20}
          minWidth={560}
        />
      </SectionCard>

      <SectionCard
        title="Renewal timeline"
        description={renewals.data ? `Contracts ending or with a notice deadline within ${renewals.data.days} days of ${renewals.data.as_of}` : undefined}
        count={renewals.data?.items.length ?? null}
        actions={
          <SegmentedControl
            size="xs"
            value={String(days)}
            onChange={setDays}
            data={DAY_CHOICES.map((d) => ({ value: d, label: `${d} d` }))}
          />
        }
      >
        <DataTable
          rows={renewals.data?.items}
          columns={renewalColumns()}
          rowKey={(c) => c.contract_id}
          loading={renewals.loading}
          error={renewals.error}
          onRetry={renewals.reload}
          emptyText="No renewals in this window"
          initialSort={{ key: 'days_to_notice', dir: 'asc' }}
          highlight={(c) => (c.days_to_notice ?? 999) <= 30}
          minWidth={1100}
        />
      </SectionCard>

      <Grid gap="md">
        <Grid.Col span={{ base: 12, xl: 7 }}>
          <SectionCard
            title="Licence utilisation"
            description={licenses.data ? `Latest usage snapshot on or before ${licenses.data.as_of}` : undefined}
            count={licenses.data?.items.length ?? null}
            actions={
              licenses.data ? (
                <Badge size="lg" variant="light" color="orange">
                  Idle cost {formatMoney(licenses.data.idle_cost_total)}
                </Badge>
              ) : undefined
            }
          >
            <DataTable
              rows={licenses.data?.items}
              columns={licenseColumns()}
              rowKey={(l) => l.license_id}
              loading={licenses.loading}
              error={licenses.error}
              onRetry={licenses.reload}
              emptyText="No licences"
              initialSort={{ key: 'idle_cost_base', dir: 'desc' }}
              pageSize={20}
              minWidth={1000}
            />
          </SectionCard>
        </Grid.Col>
        <Grid.Col span={{ base: 12, xl: 5 }}>
          <Stack gap="md">
            <ChartCard
              title="Vendor SLA trend"
              description="Monthly SLA % per vendor (incidents handled by the vendor's groups)"
              loading={vendors.loading}
              error={vendors.error}
              onRetry={vendors.reload}
              empty={vendorSeries.length === 0}
              height={240}
            >
              <LineChart data={vendorSeries} margin={{ top: 8, right: 8, left: -12, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="period" />
                <YAxis unit="%" domain={['auto', 100]} />
                <Tooltip formatter={(value) => formatPct(Number(value))} />
                <Legend />
                {vendorLines.map((v, i) => (
                  <Line
                    key={v.vendor_id}
                    dataKey={v.vendor_id}
                    name={v.vendor}
                    stroke={CHART_COLORS[i % CHART_COLORS.length]}
                    strokeWidth={(v.delta_pp ?? 0) < -5 ? 3 : 1.5}
                    dot={false}
                    connectNulls
                  />
                ))}
              </LineChart>
            </ChartCard>
            <SectionCard title="Vendor SLA change">
              <DataTable
                rows={vendors.data?.items}
                columns={VENDOR_COLUMNS}
                rowKey={(v) => v.vendor_id}
                loading={vendors.loading}
                error={vendors.error}
                emptyText="No vendor data"
                initialSort={{ key: 'delta_pp', dir: 'asc' }}
                minWidth={420}
              />
            </SectionCard>
          </Stack>
        </Grid.Col>
      </Grid>

      <SectionCard
        title="Risk findings"
        description={`Renewal, licence, vendor and cost risks${
          filters.app.length || filters.family || filters.vendor ? ' · whole portfolio (filters not applied)' : ''
        }`}
        count={risks.items?.length ?? null}
      >
        {risks.error ? <ErrorState error={risks.error} onRetry={risks.reload} compact /> : null}
        <FindingList
          findings={risks.items}
          emptyText={risks.loading ? 'Loading…' : 'No published risk findings'}
          subjectHref={(f) => {
            const href = findingSubjectHref(f, search);
            return href?.startsWith('/ops/costs') ? null : href;
          }}
        />
      </SectionCard>
    </Stack>
  );
}
