import { Badge, Group, Stack, Text, TextInput } from '@mantine/core';
import { IconSearch } from '@tabler/icons-react';
import { useMemo } from 'react';
import { useNavigate } from 'react-router';

import type { Schema } from '../../../api/types';
import { useApi } from '../../../api/useApi';
import { type Column, DataTable } from '../../../components/DataTable';
import { formatMoney, formatNumber, formatPct, formatRatio } from '../../../components/format';
import { PageHeader } from '../../../components/PageHeader';
import { SectionCard } from '../../../components/SectionCard';
import { useFilters, useSearchParam } from '../../../hooks/useFilters';
import { appHref } from '../links';

type AppRow = Schema<'AppRow'>;

const CRITICALITY_COLOR: Record<string, string> = { high: 'red', medium: 'orange', low: 'gray' };
const LIFECYCLE_COLOR: Record<string, string> = { production: 'teal', sunset: 'orange', retired: 'gray', pilot: 'blue' };

export default function AppsPage() {
  const { query, search } = useFilters();
  const navigate = useNavigate();
  const apps = useApi('/api/ops/apps', { query });
  const [needle, setNeedle] = useSearchParam('find', '');

  const rows = useMemo(() => {
    const text = needle.trim().toLowerCase();
    const items = apps.data?.items;
    if (!items || !text) return items;
    return items.filter((a) =>
      [a.name, a.app_id, a.family, a.primary_vendor].some((v) => v?.toLowerCase().includes(text)),
    );
  }, [apps.data, needle]);

  const columns: Column<AppRow>[] = [
    {
      key: 'name',
      header: 'Application',
      value: (a) => a.name,
      render: (a) => (
        <Stack gap={0}>
          <Text size="sm" fw={600}>
            {a.name}
          </Text>
          <Text size="xs" c="dimmed">
            {a.app_id}
          </Text>
        </Stack>
      ),
    },
    { key: 'family', header: 'Family', value: (a) => a.family },
    {
      key: 'criticality',
      header: 'Criticality',
      value: (a) => a.criticality,
      render: (a) =>
        a.criticality ? (
          <Badge size="sm" variant="light" color={CRITICALITY_COLOR[a.criticality] ?? 'gray'}>
            {a.criticality}
          </Badge>
        ) : (
          '–'
        ),
    },
    {
      key: 'lifecycle',
      header: 'Lifecycle',
      value: (a) => a.lifecycle,
      render: (a) =>
        a.lifecycle ? (
          <Badge size="sm" variant="dot" color={LIFECYCLE_COLOR[a.lifecycle] ?? 'gray'}>
            {a.lifecycle}
          </Badge>
        ) : (
          '–'
        ),
    },
    { key: 'primary_vendor', header: 'Primary vendor', value: (a) => a.primary_vendor },
    {
      key: 'annual_license_cost_base',
      header: 'Licence cost / yr',
      value: (a) => a.annual_license_cost_base,
      render: (a) => formatMoney(a.annual_license_cost_base),
      align: 'right',
      nowrap: true,
    },
    {
      key: 'cost_ytd_base',
      header: 'Spend YTD',
      value: (a) => a.cost_ytd_base,
      render: (a) => formatMoney(a.cost_ytd_base),
      align: 'right',
      nowrap: true,
    },
    {
      key: 'incidents_per_month_3m',
      header: 'Incidents / month',
      value: (a) => a.incidents_per_month_3m,
      render: (a) => formatNumber(a.incidents_per_month_3m, 1),
      align: 'right',
    },
    {
      key: 'sla_pct_3m',
      header: 'SLA (3 m)',
      value: (a) => a.sla_pct_3m,
      render: (a) => <Text size="sm" c={(a.sla_pct_3m ?? 100) < 85 ? 'red' : undefined}>{formatPct(a.sla_pct_3m)}</Text>,
      align: 'right',
    },
    {
      key: 'license_utilization',
      header: 'Utilisation',
      value: (a) => a.license_utilization,
      render: (a) => (
        <Text size="sm" c={(a.license_utilization ?? 1) < 0.5 ? 'orange' : undefined}>
          {formatRatio(a.license_utilization)}
        </Text>
      ),
      align: 'right',
    },
    {
      key: 'open_risks',
      header: 'Open risks',
      value: (a) => a.open_risks,
      render: (a) =>
        a.open_risks ? (
          <Badge size="sm" color="red" variant="light">
            {a.open_risks}
          </Badge>
        ) : (
          <Text size="sm" c="dimmed">
            0
          </Text>
        ),
      align: 'right',
    },
  ];

  return (
    <Stack gap="md">
      <PageHeader title="App 360" description="Portfolio grid. Open an application for tickets, changes, cost, contracts, licences, Jira and findings." />
      <SectionCard
        title="Applications"
        count={rows?.length ?? null}
        actions={
          <Group>
            <TextInput
              size="xs"
              placeholder="Find by name, id, family or vendor"
              leftSection={<IconSearch size={12} />}
              value={needle}
              onChange={(event) => setNeedle(event.currentTarget.value)}
              w={260}
            />
          </Group>
        }
      >
        <DataTable
          rows={rows}
          columns={columns}
          rowKey={(a) => a.app_id}
          loading={apps.loading}
          error={apps.error}
          onRetry={apps.reload}
          onRowClick={(a) => navigate(appHref(a.app_id, search))}
          rowLabel={(a) => `Open ${a.name}`}
          initialSort={{ key: 'open_risks', dir: 'desc' }}
          emptyText="No applications in scope"
          pageSize={50}
          minWidth={1200}
        />
      </SectionCard>
    </Stack>
  );
}
