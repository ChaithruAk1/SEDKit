import { Grid, Group, SegmentedControl, Select, SimpleGrid, Stack, Text } from '@mantine/core';
import { Bar, BarChart, CartesianGrid, ComposedChart, Legend, Line, Tooltip, XAxis, YAxis } from 'recharts';

import type { Schema } from '../../../api/types';
import { useApi } from '../../../api/useApi';
import { useShell } from '../../../app/ShellContext';
import { CHART_COLORS, ChartCard } from '../../../components/ChartCard';
import { type Column, DataTable } from '../../../components/DataTable';
import { ErrorState } from '../../../components/ErrorState';
import { formatDateTime, formatInt, formatNumber } from '../../../components/format';
import { KpiTile, KpiTileSkeleton } from '../../../components/KpiTile';
import { PageHeader } from '../../../components/PageHeader';
import { SectionCard } from '../../../components/SectionCard';
import { useFilters, useSearchParam } from '../../../hooks/useFilters';

type TypeRow = Schema<'SapIdocTypeRow'>;
type PartnerRow = Schema<'SapIdocPartnerRow'>;
type TextRow = Schema<'SapIdocTextRow'>;
type SpikeRow = Schema<'SapIdocSpikeRow'>;
type ErrorRow = Schema<'SapIdocErrorRow'>;

const AGING_LABELS: [keyof Schema<'SapIdocAging'>, string][] = [
  ['lt4h', '< 4 h'],
  ['h4_24', '4–24 h'],
  ['d1_2', '1–2 days'],
  ['d2_7', '2–7 days'],
  ['gt7d', '> 7 days'],
];

function age(hours: number): string {
  return hours >= 48 ? `${formatNumber(hours / 24, 0)} d` : `${formatNumber(hours, 0)} h`;
}

export default function SapIdocsPage() {
  const { query } = useFilters();
  const { meta } = useShell();
  const [system, setSystem] = useSearchParam('system', '');
  const [area, setArea] = useSearchParam('area', '');
  const [direction, setDirection] = useSearchParam('direction', '');
  const view = useApi('/api/sap/idocs', {
    query: { ...query, system: system || undefined, area: area || undefined, direction: direction || undefined },
  });
  const data = view.data;
  const definitions = meta.data?.definitions ?? {};

  const typeColumns: Column<TypeRow>[] = [
    { key: 'system', header: 'System', value: (r) => r.system_id, nowrap: true },
    { key: 'type', header: 'Message type', value: (r) => r.message_type },
    { key: 'direction', header: 'Direction', value: (r) => r.direction },
    { key: 'errors', header: 'In error', value: (r) => r.errors, render: (r) => <b>{formatInt(r.errors)}</b>, align: 'right' },
    { key: 'aged', header: 'Aged', value: (r) => r.aged, render: (r) => formatInt(r.aged), align: 'right' },
    { key: 'partners', header: 'Partners', value: (r) => r.partners, render: (r) => formatInt(r.partners), align: 'right' },
    { key: 'oldest', header: 'Oldest', value: (r) => r.oldest_hours, render: (r) => age(r.oldest_hours), align: 'right' },
  ];
  const partnerColumns: Column<PartnerRow>[] = [
    { key: 'partner', header: 'Partner', value: (r) => r.partner, nowrap: true },
    { key: 'system', header: 'System', value: (r) => r.system_id },
    { key: 'type', header: 'Message type', value: (r) => r.message_type },
    { key: 'errors', header: 'In error', value: (r) => r.errors, render: (r) => formatInt(r.errors), align: 'right' },
    { key: 'oldest', header: 'Oldest', value: (r) => r.oldest_hours, render: (r) => age(r.oldest_hours), align: 'right' },
  ];
  const textColumns: Column<TextRow>[] = [
    { key: 'text', header: 'Error text (numbers stripped)', value: (r) => r.text },
    { key: 'types', header: 'Message types', value: (r) => r.message_types.join(', ') },
    { key: 'errors', header: 'In error', value: (r) => r.errors, render: (r) => formatInt(r.errors), align: 'right' },
  ];
  const spikeColumns: Column<SpikeRow>[] = [
    {
      key: 'change',
      header: 'Change',
      value: (r) => r.change_id,
      render: (r) => (
        <Stack gap={0}>
          <Text size="sm" fw={600}>
            {r.change_id ?? 'Transport without change'}
          </Text>
          {r.title ? (
            <Text size="xs" c="dimmed" lineClamp={2}>
              {r.title}
            </Text>
          ) : null}
        </Stack>
      ),
    },
    { key: 'import', header: 'Production import', value: (r) => r.imported_at, render: (r) => `${r.system_id} · ${formatDateTime(r.imported_at)}` },
    { key: 'rc', header: 'RC', value: (r) => r.return_code, render: (r) => formatInt(r.return_code), align: 'right' },
    { key: 'after', header: 'Errors after', value: (r) => r.errors, render: (r) => formatInt(r.errors), align: 'right' },
    { key: 'before', header: 'Before', value: (r) => r.errors_before, render: (r) => formatInt(r.errors_before), align: 'right' },
    {
      key: 'lift',
      header: 'Lift',
      value: (r) => r.lift,
      render: (r) => (
        <Text span size="sm" fw={600} c="red">
          +{formatInt(r.lift)}
        </Text>
      ),
      align: 'right',
    },
  ];
  const errorColumns: Column<ErrorRow>[] = [
    { key: 'docnum', header: 'IDoc', value: (r) => r.docnum, render: (r) => `${r.system_id} ${r.docnum}`, nowrap: true },
    { key: 'type', header: 'Message type', value: (r) => r.message_type },
    { key: 'partner', header: 'Partner', value: (r) => r.partner },
    { key: 'status', header: 'Status', value: (r) => r.status_code },
    { key: 'text', header: 'Status text', value: (r) => r.text },
    { key: 'first', header: 'First error', value: (r) => r.first_error_at, render: (r) => formatDateTime(r.first_error_at) },
    { key: 'age', header: 'Age', value: (r) => r.age_hours, render: (r) => age(r.age_hours), align: 'right' },
  ];

  const selectors = (
    <Group gap="sm" wrap="wrap">
      <Select
        size="xs"
        placeholder="All systems"
        clearable
        value={system || null}
        onChange={(value) => setSystem(value)}
        data={(data?.systems ?? []).map((o) => ({ value: o.value, label: o.label }))}
        aria-label="SAP system"
        w={220}
      />
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
        value={direction || 'all'}
        onChange={(value) => setDirection(value === 'all' ? null : value)}
        data={[
          { value: 'all', label: 'Both directions' },
          { value: 'inbound', label: 'Inbound' },
          { value: 'outbound', label: 'Outbound' },
        ]}
      />
    </Group>
  );

  return (
    <Stack gap="md">
      <PageHeader
        title="SAP IDocs"
        description={
          data
            ? `IDoc health at ${formatDateTime(data.at)} · weekly figures to ${data.period}`
            : 'IDoc errors by system, message type and partner'
        }
        actions={selectors}
      />
      {view.error ? <ErrorState error={view.error} onRetry={view.reload} /> : null}

      <SimpleGrid cols={{ base: 1, xs: 2, md: 5 }} spacing="sm">
        {data
          ? data.kpis.map((kpi) => <KpiTile key={kpi.key} kpi={kpi} definition={definitions[kpi.key]?.text} />)
          : view.loading
            ? Array.from({ length: 5 }, (_, i) => <KpiTileSkeleton key={i} />)
            : null}
      </SimpleGrid>

      <Grid gap="md">
        <Grid.Col span={{ base: 12, lg: 8 }}>
          <ChartCard
            title="IDoc errors per week"
            description="New errors and persistent ones (not reprocessed within the grace time); the line is IDoc volume"
            loading={view.loading}
            error={view.error}
            onRetry={view.reload}
            empty={!data?.weekly.length}
          >
            <ComposedChart data={data?.weekly ?? []} margin={{ top: 8, right: 8, left: -12, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="period" tickMargin={6} />
              <YAxis yAxisId="errors" allowDecimals={false} />
              <YAxis yAxisId="volume" orientation="right" allowDecimals={false} />
              <Tooltip />
              <Legend />
              <Bar yAxisId="errors" dataKey="new_errors" name="New errors" fill={CHART_COLORS[3]} radius={[3, 3, 0, 0]} />
              <Bar yAxisId="errors" dataKey="persistent" name="Persistent" fill={CHART_COLORS[1]} radius={[3, 3, 0, 0]} />
              <Line yAxisId="volume" dataKey="idocs" name="IDocs" stroke={CHART_COLORS[0]} dot={false} strokeWidth={2} type="monotone" />
            </ComposedChart>
          </ChartCard>
        </Grid.Col>
        <Grid.Col span={{ base: 12, lg: 4 }}>
          <ChartCard
            title="Open errors by age"
            description="Since the first error status"
            loading={view.loading}
            error={view.error}
            onRetry={view.reload}
            empty={!data}
          >
            <BarChart
              data={AGING_LABELS.map(([key, label]) => ({ label, errors: data?.aging[key] ?? 0 }))}
              margin={{ top: 8, right: 8, left: -12, bottom: 0 }}
            >
              <CartesianGrid strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="label" tickMargin={6} />
              <YAxis allowDecimals={false} />
              <Tooltip />
              <Bar dataKey="errors" name="Open errors" fill={CHART_COLORS[1]} radius={[3, 3, 0, 0]} />
            </BarChart>
          </ChartCard>
        </Grid.Col>
      </Grid>

      <Grid gap="md">
        <Grid.Col span={{ base: 12, lg: 7 }}>
          <SectionCard title="Errors by message type" description="Open errors per system, message type and direction" count={data?.by_type.length ?? null}>
            <DataTable rows={data?.by_type} columns={typeColumns} rowKey={(r) => `${r.system_id}:${r.message_type}:${r.direction}`} loading={view.loading} emptyText="No IDocs in error" minWidth={520} serverOrdered />
          </SectionCard>
        </Grid.Col>
        <Grid.Col span={{ base: 12, lg: 5 }}>
          <SectionCard title="Partners with most errors" description="Open errors per partner" count={data?.partners.length ?? null}>
            <DataTable rows={data?.partners} columns={partnerColumns} rowKey={(r) => `${r.system_id}:${r.message_type}:${r.partner}`} loading={view.loading} emptyText="No IDocs in error" minWidth={420} serverOrdered />
          </SectionCard>
        </Grid.Col>
      </Grid>

      <SectionCard
        title="Error spikes after production imports"
        description="Persistent errors on the system within the spike window after a production import, against the same window before (a correlation signal, not causation)"
        count={data?.spikes.length ?? null}
      >
        <DataTable rows={data?.spikes} columns={spikeColumns} rowKey={(r) => `${r.change_id}:${r.system_id}:${r.imported_at}`} loading={view.loading} emptyText="No error spikes after production imports" minWidth={620} serverOrdered />
      </SectionCard>

      <SectionCard title="Most frequent error texts" description="Open errors grouped by their status text">
        <DataTable rows={data?.top_texts} columns={textColumns} rowKey={(r) => r.text} loading={view.loading} emptyText="No IDocs in error" minWidth={480} serverOrdered />
      </SectionCard>

      <SectionCard title="IDocs in error" description="Oldest first" count={data?.open_errors_count ?? null}>
        <DataTable rows={data?.open_errors} columns={errorColumns} rowKey={(r) => `${r.system_id}:${r.docnum}`} loading={view.loading} emptyText="No IDocs in error" minWidth={760} pageSize={25} serverOrdered />
      </SectionCard>
    </Stack>
  );
}
