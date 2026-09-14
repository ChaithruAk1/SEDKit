import { Anchor, Badge, Card, Grid, Group, SimpleGrid, Stack, Tabs, Text } from '@mantine/core';
import { IconAlertTriangle, IconArchive, IconChecklist } from '@tabler/icons-react';
import type { ReactNode } from 'react';
import { Link } from 'react-router';

import { useApi } from '../../../api/useApi';
import { useShell } from '../../../app/ShellContext';
import { ErrorState } from '../../../components/ErrorState';
import { FindingList } from '../../../components/FindingList';
import { FreshnessList } from '../../../components/FreshnessList';
import { formatDate, formatInt, humanize } from '../../../components/format';
import { KpiTile, KpiTileSkeleton } from '../../../components/KpiTile';
import { PageHeader } from '../../../components/PageHeader';
import { SectionCard } from '../../../components/SectionCard';
import { useFilters, useSearchParam } from '../../../hooks/useFilters';
import { useFindings } from '../components/useFindings';
import { findingSubjectHref } from '../links';

function CountCard({ title, value, icon, href, note }: { title: string; value: number | undefined; icon: ReactNode; href?: string; note?: ReactNode }) {
  return (
    <Card withBorder radius="md" padding="md">
      <Group justify="space-between" wrap="nowrap">
        <Stack gap={0}>
          <Text size="xs" c="dimmed" fw={600} tt="uppercase">
            {title}
          </Text>
          <Text fz={26} fw={700}>
            {value === undefined ? '–' : formatInt(value)}
          </Text>
        </Stack>
        {icon}
      </Group>
      {note ? (
        <Text size="xs" c="dimmed">
          {note}
        </Text>
      ) : null}
      {href ? (
        <Anchor component={Link} to={href} size="xs">
          Open list
        </Anchor>
      ) : null}
    </Card>
  );
}

export default function OverviewPage() {
  const { filters, query, search } = useFilters();
  const { meta } = useShell();
  const overview = useApi('/api/ops/overview', { query });
  const findings = useFindings({ as_of: filters.as_of, limit: 200 }, filters.include_drafts);
  const [kindTab, setKindTab] = useSearchParam('findings', 'all');
  const data = overview.data;
  const definitions = meta.data?.definitions ?? {};

  const kinds = [...new Set((findings.items ?? []).map((f) => f.kind))].sort();
  const shownFindings = (findings.items ?? []).filter((f) => kindTab === 'all' || f.kind === kindTab);

  return (
    <Stack gap="md">
      <PageHeader
        title="Overview"
        description={
          data
            ? `Period ${data.period} · as of ${formatDate(data.as_of)} · last import data ${formatDate(data.data_as_of_last_import)}`
            : 'Key figures for the selected scope'
        }
      />
      {overview.error ? <ErrorState error={overview.error} onRetry={overview.reload} /> : null}

      <SimpleGrid cols={{ base: 1, xs: 2, md: 3, xl: 4 }} spacing="sm">
        {data
          ? data.kpis.map((kpi) => <KpiTile key={kpi.key} kpi={kpi} definition={definitions[kpi.key]?.text} />)
          : overview.loading
            ? Array.from({ length: 8 }, (_, i) => <KpiTileSkeleton key={i} />)
            : null}
      </SimpleGrid>

      {overview.error && !data ? null : (
        <Grid gap="md">
          <Grid.Col span={{ base: 12, lg: 8 }}>
            <SectionCard title="Top risks" description="Highest-severity published findings" count={data?.top_risks.length ?? null}>
              <FindingList
                findings={data?.top_risks}
                emptyText={overview.loading ? 'Loading…' : 'No published risks'}
                subjectHref={(f) => findingSubjectHref(f, search)}
              />
            </SectionCard>
          </Grid.Col>
          <Grid.Col span={{ base: 12, lg: 4 }}>
            <Stack gap="md">
              <SimpleGrid cols={{ base: 1, xs: 3, lg: 1 }} spacing="sm">
                <CountCard
                  title="Needs attention"
                  value={data?.attention_count}
                  icon={<IconAlertTriangle size={28} color="var(--mantine-color-orange-6)" />}
                  href={`/ops/attention${search}`}
                  note="As of the last export"
                />
                <CountCard
                  title="Stale open"
                  value={data?.stale_open}
                  icon={<IconArchive size={28} color="var(--mantine-color-gray-6)" />}
                  note="Open in the store, missing from the latest active export"
                />
                <CountCard
                  title="Review queue"
                  value={data?.review_queue_count}
                  icon={<IconChecklist size={28} color="var(--mantine-color-violet-6)" />}
                  note="AI drafts and runs waiting for review (sed review)"
                />
              </SimpleGrid>
              <SectionCard title="Freshness" description="Newest data per source">
                <FreshnessList rows={data?.freshness} asOf={data?.as_of} />
              </SectionCard>
            </Stack>
          </Grid.Col>
        </Grid>
      )}

      <SectionCard
        title="Findings"
        description={filters.include_drafts ? 'Published findings and AI drafts' : 'Published findings'}
        count={findings.items?.length ?? null}
      >
        {findings.error ? <ErrorState error={findings.error} onRetry={findings.reload} compact /> : null}
        {kinds.length > 1 ? (
          <Tabs value={kindTab} onChange={(value) => setKindTab(value)} mb="sm">
            <Tabs.List>
              <Tabs.Tab value="all">
                All <Badge size="xs" variant="light" ml={4}>{findings.items?.length ?? 0}</Badge>
              </Tabs.Tab>
              {kinds.map((kind) => (
                <Tabs.Tab key={kind} value={kind}>
                  {humanize(kind)}{' '}
                  <Badge size="xs" variant="light" ml={4}>
                    {(findings.items ?? []).filter((f) => f.kind === kind).length}
                  </Badge>
                </Tabs.Tab>
              ))}
            </Tabs.List>
          </Tabs>
        ) : null}
        <FindingList
          findings={findings.items ? shownFindings : undefined}
          emptyText={findings.loading ? 'Loading…' : 'No findings'}
          subjectHref={(f) => findingSubjectHref(f, search)}
        />
      </SectionCard>
    </Stack>
  );
}
