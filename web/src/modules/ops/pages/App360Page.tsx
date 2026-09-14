import { Anchor, Badge, Grid, Group, SimpleGrid, Stack, Tabs, Text } from '@mantine/core';
import { IconArrowLeft } from '@tabler/icons-react';
import { useMemo } from 'react';
import { Link, useParams } from 'react-router';
import { Bar, BarChart, CartesianGrid, ComposedChart, Legend, Line, Tooltip, XAxis, YAxis } from 'recharts';

import type { Schema } from '../../../api/types';
import { useApi } from '../../../api/useApi';
import { useShell } from '../../../app/ShellContext';
import { CHART_COLORS, ChartCard } from '../../../components/ChartCard';
import { type Column, DataTable } from '../../../components/DataTable';
import { ErrorState } from '../../../components/ErrorState';
import { FindingList } from '../../../components/FindingList';
import { formatDate, formatDateTime, formatMoney, formatPct } from '../../../components/format';
import { KpiTile, KpiTileSkeleton } from '../../../components/KpiTile';
import { PageHeader } from '../../../components/PageHeader';
import { SectionCard } from '../../../components/SectionCard';
import { useFilters, useSearchParam } from '../../../hooks/useFilters';
import { licenseColumns, renewalColumns } from '../components/contractColumns';
import { TicketDrawer, useTicketParam } from '../components/TicketDrawer';
import { ticketColumns } from '../components/ticketColumns';
import { findingSubjectHref } from '../links';

const CHANGE_COLUMNS: Column<Schema<'ChangeRow'>>[] = [
  { key: 'number', header: 'Change', value: (c) => c.number, render: (c) => <Text size="sm" ff="monospace">{c.number}</Text> },
  { key: 'change_type', header: 'Type', value: (c) => c.change_type },
  {
    key: 'close_code',
    header: 'Outcome',
    value: (c) => c.close_code,
    render: (c) =>
      c.close_code ? (
        <Badge size="sm" variant="light" color={c.close_code === 'successful' ? 'teal' : 'red'}>
          {c.close_code}
        </Badge>
      ) : (
        '–'
      ),
  },
  { key: 'short_description', header: 'Short description', value: (c) => c.short_description },
  { key: 'start_date', header: 'Start', value: (c) => c.start_date, render: (c) => formatDateTime(c.start_date), nowrap: true },
  { key: 'closed_at', header: 'Closed', value: (c) => c.closed_at, render: (c) => formatDateTime(c.closed_at), nowrap: true },
];

const JIRA_COLUMNS: Column<Schema<'JiraRow'>>[] = [
  { key: 'issue_key', header: 'Key', value: (j) => j.issue_key, render: (j) => <Text size="sm" ff="monospace">{j.issue_key}</Text> },
  { key: 'issue_type', header: 'Type', value: (j) => j.issue_type },
  { key: 'status', header: 'Status', value: (j) => j.status },
  { key: 'priority', header: 'Priority', value: (j) => j.priority },
  { key: 'summary', header: 'Summary', value: (j) => j.summary },
  { key: 'created', header: 'Created', value: (j) => j.created, render: (j) => formatDate(j.created), nowrap: true },
  { key: 'resolved', header: 'Resolved', value: (j) => j.resolved, render: (j) => formatDate(j.resolved), nowrap: true },
];

const DOC_COLUMNS: Column<Schema<'DocRow'>>[] = [
  { key: 'title', header: 'Page', value: (d) => d.title },
  { key: 'space_key', header: 'Space', value: (d) => d.space_key },
  { key: 'page_type', header: 'Type', value: (d) => d.page_type },
  { key: 'last_updated', header: 'Updated', value: (d) => d.last_updated, render: (d) => formatDate(d.last_updated), nowrap: true },
];

const COST_COLUMNS: Column<Schema<'CostRow'>>[] = [
  { key: 'label', header: 'Category', value: (c) => c.label },
  { key: 'actual', header: 'Actual', value: (c) => c.actual, render: (c) => formatMoney(c.actual), align: 'right' },
  { key: 'budget', header: 'Budget', value: (c) => c.budget, render: (c) => formatMoney(c.budget), align: 'right' },
  {
    key: 'variance_pct',
    header: 'Variance',
    value: (c) => c.variance_pct,
    render: (c) => <Text size="sm" c={(c.variance_pct ?? 0) > 0 ? 'red' : 'teal'}>{formatPct(c.variance_pct)}</Text>,
    align: 'right',
  },
];

export default function App360Page() {
  const { appId = '' } = useParams();
  const { filters, query, search } = useFilters();
  const { meta } = useShell();
  const app360 = useApi(appId ? '/api/ops/apps/{app_id}' : null, { params: { app_id: appId }, query });
  const [tab, setTab] = useSearchParam('section', 'tickets');
  const [, setTicket] = useTicketParam();
  const data = app360.data;
  const app = data?.app;
  const definitions = meta.data?.definitions ?? {};
  const openTicketColumns = useMemo(() => ticketColumns({ showApp: false }), []);
  const findings = useMemo(
    () => (data?.findings ?? []).filter((f) => filters.include_drafts || f.origin === 'rule' || f.status !== 'draft'),
    [data, filters.include_drafts],
  );

  const back = (
    <Anchor component={Link} to={`/ops/apps${search}`} size="sm">
      <Group gap={4}>
        <IconArrowLeft size={14} /> All applications
      </Group>
    </Anchor>
  );

  if (app360.error && !data) {
    return (
      <Stack gap="md">
        {back}
        <ErrorState error={app360.error} onRetry={app360.reload} title={app360.error.kind === 'not_found' ? 'Unknown application' : undefined} />
      </Stack>
    );
  }

  return (
    <Stack gap="md">
      {back}
      <PageHeader
        title={app?.name ?? appId}
        description={
          app
            ? [app.app_id, app.family, app.primary_vendor ? `vendor ${app.primary_vendor}` : null].filter(Boolean).join(' · ')
            : 'Loading application…'
        }
        badges={
          app ? (
            <Group gap={6}>
              {app.criticality ? <Badge variant="light" color={app.criticality === 'high' ? 'red' : 'gray'}>{app.criticality} criticality</Badge> : null}
              {app.lifecycle ? <Badge variant="dot">{app.lifecycle}</Badge> : null}
              {app.open_risks ? <Badge color="red" variant="light">{app.open_risks} open risk{app.open_risks === 1 ? '' : 's'}</Badge> : null}
            </Group>
          ) : null
        }
      />

      <SimpleGrid cols={{ base: 1, xs: 2, md: 3, xl: 5 }} spacing="sm">
        {data
          ? data.kpis.map((kpi) => <KpiTile key={kpi.key} kpi={kpi} definition={definitions[kpi.key]?.text} />)
          : Array.from({ length: 5 }, (_, i) => <KpiTileSkeleton key={i} />)}
      </SimpleGrid>

      <Grid gap="md">
        <Grid.Col span={{ base: 12, lg: 7 }}>
          <ChartCard
            title="Incident volumes (12 months)"
            loading={app360.loading}
            empty={!data?.volumes.length}
            height={230}
          >
            <ComposedChart data={data?.volumes ?? []} margin={{ top: 8, right: 8, left: -12, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="period" />
              <YAxis allowDecimals={false} />
              <Tooltip />
              <Legend />
              <Bar dataKey="opened" name="Opened" fill={CHART_COLORS[0]} radius={[3, 3, 0, 0]} />
              <Bar dataKey="resolved" name="Resolved" fill={CHART_COLORS[2]} radius={[3, 3, 0, 0]} />
              <Line dataKey="net" name="Net" stroke={CHART_COLORS[1]} dot={false} strokeWidth={2} />
            </ComposedChart>
          </ChartCard>
        </Grid.Col>
        <Grid.Col span={{ base: 12, lg: 5 }}>
          <ChartCard title="Cost vs budget" description="By cost category" loading={app360.loading} empty={!data?.cost.length} height={230}>
            <BarChart data={data?.cost ?? []} margin={{ top: 8, right: 8, left: 8, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="label" />
              <YAxis tickFormatter={(v: number) => formatMoney(v, { compact: true })} width={64} />
              <Tooltip formatter={(value) => formatMoney(Number(value))} />
              <Legend />
              <Bar dataKey="actual" name="Actual" fill={CHART_COLORS[0]} radius={[3, 3, 0, 0]} />
              <Bar dataKey="budget" name="Budget" fill={CHART_COLORS[6]} radius={[3, 3, 0, 0]} />
            </BarChart>
          </ChartCard>
        </Grid.Col>
      </Grid>

      <Tabs value={tab} onChange={(value) => setTab(value)} keepMounted={false}>
        <Tabs.List mb="sm">
          <Tabs.Tab value="tickets">Open tickets ({data?.open_tickets.length ?? 0})</Tabs.Tab>
          <Tabs.Tab value="changes">Changes ({data?.changes.length ?? 0})</Tabs.Tab>
          <Tabs.Tab value="cost">Cost ({data?.cost.length ?? 0})</Tabs.Tab>
          <Tabs.Tab value="contracts">Contracts ({data?.contracts.length ?? 0})</Tabs.Tab>
          <Tabs.Tab value="licenses">Licences ({data?.licenses.length ?? 0})</Tabs.Tab>
          <Tabs.Tab value="jira">Jira ({data?.jira.length ?? 0})</Tabs.Tab>
          <Tabs.Tab value="docs">Docs ({data?.docs.length ?? 0})</Tabs.Tab>
          <Tabs.Tab value="findings">Findings ({findings.length})</Tabs.Tab>
        </Tabs.List>
        <Tabs.Panel value="tickets">
          <SectionCard title="Open tickets" description="Up to 20, newest first">
            <DataTable
              rows={data?.open_tickets}
              columns={openTicketColumns}
              rowKey={(t) => t.ticket_id}
              loading={app360.loading}
              onRowClick={(t) => setTicket(t.ticket_id)}
              rowLabel={(t) => `Open ticket ${t.number}`}
              serverOrdered
              emptyText="No open tickets"
              minWidth={960}
            />
          </SectionCard>
        </Tabs.Panel>
        <Tabs.Panel value="changes">
          <SectionCard title="Recent changes">
            <DataTable rows={data?.changes} columns={CHANGE_COLUMNS} rowKey={(c) => c.number} loading={app360.loading} emptyText="No changes" />
          </SectionCard>
        </Tabs.Panel>
        <Tabs.Panel value="cost">
          <SectionCard title="Cost vs budget">
            <DataTable rows={data?.cost} columns={COST_COLUMNS} rowKey={(c) => c.key} loading={app360.loading} emptyText="No cost lines" minWidth={480} />
          </SectionCard>
        </Tabs.Panel>
        <Tabs.Panel value="contracts">
          <SectionCard title="Contracts">
            <DataTable rows={data?.contracts} columns={renewalColumns(false)} rowKey={(c) => c.contract_id} loading={app360.loading} emptyText="No contracts" minWidth={900} />
          </SectionCard>
        </Tabs.Panel>
        <Tabs.Panel value="licenses">
          <SectionCard title="Licences">
            <DataTable rows={data?.licenses} columns={licenseColumns(false)} rowKey={(l) => l.license_id} loading={app360.loading} emptyText="No licences" minWidth={900} />
          </SectionCard>
        </Tabs.Panel>
        <Tabs.Panel value="jira">
          <SectionCard title="Jira issues">
            <DataTable rows={data?.jira} columns={JIRA_COLUMNS} rowKey={(j) => j.issue_key} loading={app360.loading} emptyText="No Jira issues" minWidth={800} />
          </SectionCard>
        </Tabs.Panel>
        <Tabs.Panel value="docs">
          <SectionCard title="Documentation pages">
            <DataTable rows={data?.docs} columns={DOC_COLUMNS} rowKey={(d) => d.page_id} loading={app360.loading} emptyText="No pages" minWidth={560} />
          </SectionCard>
        </Tabs.Panel>
        <Tabs.Panel value="findings">
          <FindingList
            findings={data ? findings : undefined}
            emptyText="No findings for this application"
            subjectHref={(f) => (f.subject_id === appId ? null : findingSubjectHref(f, search))}
          />
        </Tabs.Panel>
      </Tabs>
      <TicketDrawer />
    </Stack>
  );
}
