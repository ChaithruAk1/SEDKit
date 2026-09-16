import { Anchor, Badge, Grid, Group, SegmentedControl, Select, SimpleGrid, Stack, Text } from '@mantine/core';
import { Fragment } from 'react';
import { Bar, BarChart, CartesianGrid, Legend, Tooltip, XAxis, YAxis } from 'recharts';
import { Link } from 'react-router';

import type { Schema } from '../../../api/types';
import { useApi } from '../../../api/useApi';
import { CHART_COLORS, ChartCard } from '../../../components/ChartCard';
import { type Column, DataTable } from '../../../components/DataTable';
import { ErrorState } from '../../../components/ErrorState';
import { formatDate, formatDateTime, formatInt, formatNumber, formatPct } from '../../../components/format';
import { KpiTile, KpiTileSkeleton } from '../../../components/KpiTile';
import { PageHeader } from '../../../components/PageHeader';
import { SectionCard } from '../../../components/SectionCard';
import { useShell } from '../../../app/ShellContext';
import { useFilters, useSearchParam } from '../../../hooks/useFilters';
import { ticketHref } from '../links';

type StageRow = Schema<'SapStageRow'>;
type UrgentRow = Schema<'SapUrgentAreaRow'>;
type StuckRow = Schema<'SapStuckChangeRow'>;
type WaitingRow = Schema<'SapWaitingTransportRow'>;
type FailedRow = Schema<'SapFailedImportRow'>;
type AfterRow = Schema<'SapImportIncidentsRow'>;
type ChangeRow = Schema<'SapChangeRow'>;

const LANDSCAPE_LABELS: Record<string, string> = { ecc: 'SAP ECC', s4: 'SAP S/4HANA', unknown: 'Unknown landscape' };

function Title({ id, title }: { id: string | null; title: string | null }) {
  return (
    <Stack gap={0}>
      <Text size="sm" fw={600}>
        {id ?? '–'}
      </Text>
      {title ? (
        <Text size="xs" c="dimmed" lineClamp={2}>
          {title}
        </Text>
      ) : null}
    </Stack>
  );
}

export default function SapChangesPage() {
  const { query, search } = useFilters();
  const { meta } = useShell();
  const [area, setArea] = useSearchParam('area', '');
  const [landscape, setLandscape] = useSearchParam('landscape', '');
  const view = useApi('/api/sap/changes', {
    query: { ...query, area: area || undefined, landscape: landscape || undefined },
  });
  const data = view.data;
  const definitions = meta.data?.definitions ?? {};
  const landscapeLabel = (code: string) =>
    data?.landscapes.find((o) => o.value === code)?.label ?? LANDSCAPE_LABELS[code] ?? code;

  const stageColumns: Column<StageRow>[] = [
    { key: 'label', header: 'Stage', value: (r) => r.label },
    { key: 'normal', header: 'Normal', value: (r) => r.normal, render: (r) => formatInt(r.normal), align: 'right' },
    { key: 'urgent', header: 'Urgent', value: (r) => r.urgent, render: (r) => formatInt(r.urgent), align: 'right' },
    { key: 'standard', header: 'Standard', value: (r) => r.standard, render: (r) => formatInt(r.standard), align: 'right' },
    {
      key: 'defect',
      header: 'Defect corr.',
      value: (r) => r.defect_correction,
      render: (r) => formatInt(r.defect_correction),
      align: 'right',
    },
    {
      key: 'other',
      header: 'Other',
      value: (r) => r.general + r.other,
      render: (r) => formatInt(r.general + r.other),
      align: 'right',
    },
    { key: 'total', header: 'Open', value: (r) => r.total, render: (r) => <b>{formatInt(r.total)}</b>, align: 'right' },
  ];
  const urgentColumns: Column<UrgentRow>[] = [
    { key: 'label', header: 'Area', value: (r) => r.label },
    { key: 'created', header: 'Created', value: (r) => r.created, render: (r) => formatInt(r.created), align: 'right' },
    { key: 'urgent', header: 'Urgent', value: (r) => r.urgent, render: (r) => formatInt(r.urgent), align: 'right' },
    { key: 'ratio', header: 'Urgent %', value: (r) => r.ratio_pct, render: (r) => formatPct(r.ratio_pct), align: 'right' },
    {
      key: 'previous',
      header: '8 weeks before',
      value: (r) => r.previous_ratio_pct,
      render: (r) => formatPct(r.previous_ratio_pct),
      align: 'right',
    },
    {
      key: 'delta',
      header: 'Change',
      value: (r) => r.delta_pp,
      render: (r) =>
        r.delta_pp === null ? (
          '–'
        ) : (
          <Text span size="sm" c={r.delta_pp >= 15 ? 'red' : undefined} fw={r.delta_pp >= 15 ? 600 : undefined}>
            {r.delta_pp > 0 ? '+' : ''}
            {formatNumber(r.delta_pp, 1)} pp
          </Text>
        ),
      align: 'right',
    },
  ];
  const stuckColumns: Column<StuckRow>[] = [
    { key: 'change', header: 'Change', value: (r) => r.change_id, render: (r) => <Title id={r.change_id} title={r.title} /> },
    { key: 'type', header: 'Type', value: (r) => r.type_label },
    { key: 'area', header: 'Area', value: (r) => r.area_label },
    { key: 'landscape', header: 'Landscape', value: (r) => r.landscape, render: (r) => landscapeLabel(r.landscape) },
    { key: 'status', header: 'Status', value: (r) => r.status },
    {
      key: 'days',
      header: 'Days in status',
      value: (r) => r.days_in_status,
      render: (r) => `${formatNumber(r.days_in_status, 0)} (limit ${r.threshold_days})`,
      align: 'right',
    },
    { key: 'jira', header: 'Jira', value: (r) => r.jira_keys.join(', '), render: (r) => r.jira_keys.join(', ') || '–' },
  ];
  const waitingColumns: Column<WaitingRow>[] = [
    { key: 'transport', header: 'Transport', value: (r) => r.transport, nowrap: true },
    { key: 'change', header: 'Change', value: (r) => r.change_id, render: (r) => <Title id={r.change_id} title={r.title} /> },
    { key: 'landscape', header: 'Landscape', value: (r) => r.landscape, render: (r) => landscapeLabel(r.landscape) },
    { key: 'qa', header: 'QA import', value: (r) => r.qa_imported_at, render: (r) => `${r.qa_system} · ${formatDate(r.qa_imported_at)}` },
    { key: 'days', header: 'Days waiting', value: (r) => r.days_waiting, render: (r) => formatNumber(r.days_waiting, 0), align: 'right' },
  ];
  const failedColumns: Column<FailedRow>[] = [
    { key: 'transport', header: 'Transport', value: (r) => r.transport, nowrap: true },
    {
      key: 'system',
      header: 'System',
      value: (r) => r.system_id,
      render: (r) => (
        <Group gap={4} wrap="nowrap">
          <Text size="sm">{r.system_id}</Text>
          {r.role === 'prod' ? (
            <Badge size="xs" color="red" variant="light">
              production
            </Badge>
          ) : null}
        </Group>
      ),
    },
    { key: 'rc', header: 'Return code', value: (r) => r.return_code, render: (r) => formatInt(r.return_code), align: 'right' },
    { key: 'imported', header: 'Imported', value: (r) => r.imported_at, render: (r) => formatDateTime(r.imported_at) },
    { key: 'change', header: 'Change', value: (r) => r.change_id, render: (r) => <Title id={r.change_id} title={r.title} /> },
  ];
  const afterColumns: Column<AfterRow>[] = [
    { key: 'change', header: 'Change', value: (r) => r.change_id, render: (r) => <Title id={r.change_id} title={r.title} /> },
    { key: 'area', header: 'Area', value: (r) => r.area_label },
    { key: 'system', header: 'Production', value: (r) => r.imported_at, render: (r) => `${r.system_id} · ${formatDateTime(r.imported_at)}` },
    { key: 'rc', header: 'RC', value: (r) => r.return_code, render: (r) => formatInt(r.return_code), align: 'right' },
    { key: 'incidents', header: 'After', value: (r) => r.incidents, render: (r) => formatInt(r.incidents), align: 'right' },
    {
      key: 'before',
      header: 'Before',
      value: (r) => r.incidents_before,
      render: (r) => formatInt(r.incidents_before),
      align: 'right',
    },
    {
      key: 'lift',
      header: 'Lift',
      value: (r) => r.lift,
      render: (r) => (
        <Text span size="sm" fw={600} c={r.lift >= 5 ? 'red' : undefined}>
          +{formatInt(r.lift)}
        </Text>
      ),
      align: 'right',
    },
    {
      key: 'numbers',
      header: 'First incidents',
      value: (r) => r.numbers.join(' '),
      render: (r) => (
        <Group gap={6}>
          {r.numbers.map((n) => (
            <Fragment key={n}>
              <Anchor component={Link} to={ticketHref(`incident:${n}`, search)} size="xs">
                {n}
              </Anchor>
            </Fragment>
          ))}
        </Group>
      ),
    },
  ];
  const withoutColumns: Column<ChangeRow>[] = [
    { key: 'change', header: 'Change', value: (r) => r.change_id, render: (r) => <Title id={r.change_id} title={r.title} /> },
    { key: 'type', header: 'Type', value: (r) => r.type_label },
    { key: 'area', header: 'Area', value: (r) => r.area_label },
    { key: 'stage', header: 'Stage', value: (r) => r.stage_label },
    { key: 'created', header: 'Created', value: (r) => r.created_at, render: (r) => formatDate(r.created_at) },
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
        title="SAP changes"
        description={
          data
            ? `ChaRM changes and transports · status at ${formatDate(data.at)} · weekly figures to ${data.period}`
            : 'ChaRM changes and transports by area and landscape'
        }
        actions={selectors}
      />
      {view.error ? <ErrorState error={view.error} onRetry={view.reload} /> : null}

      <SimpleGrid cols={{ base: 1, xs: 2, md: 4 }} spacing="sm">
        {data
          ? data.kpis.map((kpi) => <KpiTile key={kpi.key} kpi={kpi} definition={definitions[kpi.key]?.text} />)
          : view.loading
            ? Array.from({ length: 7 }, (_, i) => <KpiTileSkeleton key={i} />)
            : null}
      </SimpleGrid>

      <Grid gap="md">
        <Grid.Col span={{ base: 12, lg: 6 }}>
          <SectionCard title="Open changes by stage" description="Requests for change are not counted">
            <DataTable rows={data?.stages} columns={stageColumns} rowKey={(r) => r.stage} loading={view.loading} emptyText="No open changes" minWidth={420} serverOrdered />
          </SectionCard>
        </Grid.Col>
        <Grid.Col span={{ base: 12, lg: 6 }}>
          <ChartCard
            title="Production imports per week"
            description="Imports into production systems; failed means a return code at or above the failure code"
            loading={view.loading}
            error={view.error}
            onRetry={view.reload}
            empty={!data?.production_imports.length}
          >
            <BarChart data={data?.production_imports ?? []} margin={{ top: 8, right: 8, left: -12, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="period" tickMargin={6} />
              <YAxis allowDecimals={false} />
              <Tooltip />
              <Legend />
              <Bar dataKey="imports" name="Imports" fill={CHART_COLORS[0]} radius={[3, 3, 0, 0]} />
              <Bar dataKey="failed" name="Failed" fill={CHART_COLORS[1]} radius={[3, 3, 0, 0]} />
            </BarChart>
          </ChartCard>
        </Grid.Col>
      </Grid>

      <SectionCard
        title="Urgent share of new changes by area"
        description="Changes created in the last 8 full weeks against the 8 weeks before (all areas)"
      >
        <DataTable rows={data?.urgent_by_area} columns={urgentColumns} rowKey={(r) => r.area} loading={view.loading} emptyText="No changes created" minWidth={520} serverOrdered />
      </SectionCard>

      <SectionCard title="Failed transport imports" description="Latest import per transport and system, in the weeks shown" count={data?.failed.length ?? null}>
        <DataTable rows={data?.failed} columns={failedColumns} rowKey={(r) => `${r.transport}:${r.system_id}`} loading={view.loading} emptyText="No failed imports" minWidth={560} serverOrdered />
      </SectionCard>

      <SectionCard
        title="SAP incidents after production imports"
        description="Imports followed by more incidents of the same landscape and area than in the same window before them (a correlation signal, not causation)"
        count={data?.incidents_after_imports.length ?? null}
      >
        <DataTable rows={data?.incidents_after_imports} columns={afterColumns} rowKey={(r) => `${r.change_id}:${r.system_id}:${r.imported_at}`} loading={view.loading} emptyText="No incidents after production imports" minWidth={640} serverOrdered />
      </SectionCard>

      <Grid gap="md">
        <Grid.Col span={{ base: 12, lg: 6 }}>
          <SectionCard title="Stuck changes" description="Open changes whose status has not moved for longer than their stage allows" count={data?.stuck.length ?? null}>
            <DataTable rows={data?.stuck} columns={stuckColumns} rowKey={(r) => r.change_id} loading={view.loading} emptyText="No stuck changes" minWidth={620} serverOrdered />
          </SectionCard>
        </Grid.Col>
        <Grid.Col span={{ base: 12, lg: 6 }}>
          <SectionCard title="Transports waiting for production" description="Tested changes imported into QA and not into production" count={data?.waiting.length ?? null}>
            <DataTable rows={data?.waiting} columns={waitingColumns} rowKey={(r) => r.transport} loading={view.loading} emptyText="Nothing waiting" minWidth={520} serverOrdered />
          </SectionCard>
        </Grid.Col>
      </Grid>

      <SectionCard
        title="Changes without a Jira story"
        description="Open normal, urgent and defect-correction changes with no Jira link either way"
        count={data?.without_jira_count ?? null}
      >
        <DataTable rows={data?.without_jira} columns={withoutColumns} rowKey={(r) => r.change_id} loading={view.loading} emptyText="Every change traces to a Jira story" minWidth={520} pageSize={25} serverOrdered />
      </SectionCard>
    </Stack>
  );
}
