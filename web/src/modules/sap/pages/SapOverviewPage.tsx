import { Alert, Anchor, Grid, SimpleGrid, Stack, Text } from '@mantine/core';
import { IconInfoCircle } from '@tabler/icons-react';
import { Link } from 'react-router';

import type { Schema } from '../../../api/types';
import { useApi } from '../../../api/useApi';
import { useShell } from '../../../app/ShellContext';
import { type Column, DataTable } from '../../../components/DataTable';
import { ErrorState } from '../../../components/ErrorState';
import { FindingList } from '../../../components/FindingList';
import { formatDate, formatInt, formatPct } from '../../../components/format';
import { KpiTile, KpiTileSkeleton } from '../../../components/KpiTile';
import { PageHeader } from '../../../components/PageHeader';
import { SectionCard } from '../../../components/SectionCard';
import { useFilters } from '../../../hooks/useFilters';
import { areaHref, sapFindingHref, withParams } from '../links';

type AreaRow = Schema<'SapAreaRow'>;
type LandscapeRow = Schema<'SapLandscapeRow'>;

export default function SapOverviewPage() {
  const { query, search } = useFilters();
  const { meta } = useShell();
  const overview = useApi('/api/sap/overview', { query });
  const data = overview.data;
  const definitions = meta.data?.definitions ?? {};
  const week = data?.period ?? 'last week';

  const areaColumns: Column<AreaRow>[] = [
    {
      key: 'label',
      header: 'Area',
      value: (r) => r.label,
      render: (r) => (
        <Anchor component={Link} to={areaHref(r.area, search)} size="sm">
          {r.label}
        </Anchor>
      ),
    },
    { key: 'open', header: 'Open', value: (r) => r.open, render: (r) => formatInt(r.open), align: 'right' },
    { key: 'aged', header: 'Open > 30 days', value: (r) => r.aged_30d, render: (r) => formatInt(r.aged_30d), align: 'right' },
    { key: 'opened', header: `Opened (${week})`, value: (r) => r.opened, render: (r) => formatInt(r.opened), align: 'right' },
    { key: 'resolved', header: `Resolved (${week})`, value: (r) => r.resolved, render: (r) => formatInt(r.resolved), align: 'right' },
    { key: 'sla', header: 'SLA met', value: (r) => r.sla_pct, render: (r) => formatPct(r.sla_pct), align: 'right' },
  ];
  const landscapeColumns: Column<LandscapeRow>[] = [
    {
      key: 'label',
      header: 'Landscape',
      value: (r) => r.label,
      render: (r) => (
        <Anchor component={Link} to={`/sap/tickets${withParams(search, { landscape: r.landscape })}`} size="sm">
          {r.label}
        </Anchor>
      ),
    },
    { key: 'open', header: 'Open', value: (r) => r.open, render: (r) => formatInt(r.open), align: 'right' },
  ];

  return (
    <Stack gap="md">
      <PageHeader
        title="SAP overview"
        description={
          data
            ? `SAP L3 support · week ${data.period} · as of ${formatDate(data.as_of)} · last import data ${formatDate(data.data_as_of_last_import)}`
            : 'SAP L3 support by area and landscape'
        }
      />
      {overview.error ? <ErrorState error={overview.error} onRetry={overview.reload} /> : null}
      {data && !data.configured ? (
        <Alert icon={<IconInfoCircle size={18} />} title="No SAP scope configured" color="yellow">
          List your SAP L3 assignment groups, SAP categories or custom fields in DATA_DIR\config\sap\scope.yaml.
        </Alert>
      ) : null}

      <SimpleGrid cols={{ base: 1, xs: 2, md: 4 }} spacing="sm">
        {data
          ? data.kpis.map((kpi) => <KpiTile key={kpi.key} kpi={kpi} definition={definitions[kpi.key]?.text} />)
          : overview.loading
            ? Array.from({ length: 8 }, (_, i) => <KpiTileSkeleton key={i} />)
            : null}
      </SimpleGrid>

      <Grid gap="md">
        <Grid.Col span={{ base: 12, lg: 8 }}>
          <SectionCard title="SAP areas" description="Open backlog now; volumes and SLA of the last full week" count={data?.areas.length ?? null}>
            <DataTable
              rows={data?.areas}
              columns={areaColumns}
              rowKey={(r) => r.area}
              loading={overview.loading}
              emptyText="No SAP areas"
              serverOrdered
            />
          </SectionCard>
        </Grid.Col>
        <Grid.Col span={{ base: 12, lg: 4 }}>
          <SectionCard title="Landscapes" description="Open SAP incidents by landscape">
            <DataTable
              rows={data?.landscapes}
              columns={landscapeColumns}
              rowKey={(r) => r.landscape}
              loading={overview.loading}
              emptyText="No landscapes"
              serverOrdered
              minWidth={240}
            />
            <Text size="xs" c="dimmed" mt="xs">
              Landscapes come from the ticket&apos;s application (config/sap/scope.yaml).
            </Text>
          </SectionCard>
        </Grid.Col>
      </Grid>

      <SectionCard title="System-detected SAP risks" description="Rule findings of the SAP module" count={data?.findings.length ?? null}>
        <FindingList
          findings={data?.findings}
          emptyText={overview.loading ? 'Loading…' : 'No SAP risks detected'}
          subjectHref={(f) => sapFindingHref(f, search)}
        />
      </SectionCard>
    </Stack>
  );
}
