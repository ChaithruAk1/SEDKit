import { Badge, Button, Code, Grid, Group, HoverCard, SegmentedControl, Stack, Text } from '@mantine/core';
import { IconLink } from '@tabler/icons-react';
import { useMemo, useState } from 'react';
import { useLocation } from 'react-router';

import type { Schema } from '../../api/types';
import { useApi } from '../../api/useApi';
import { useShell } from '../../app/ShellContext';
import { AssignAliasModal, STRONG_MATCH } from '../../components/AssignAliasModal';
import { type Column, DataTable } from '../../components/DataTable';
import { ErrorState } from '../../components/ErrorState';
import { FreshnessList } from '../../components/FreshnessList';
import { formatDate, formatDateTime, formatInt, humanize } from '../../components/format';
import { PageHeader } from '../../components/PageHeader';
import { SectionCard } from '../../components/SectionCard';
import { useSearchParam } from '../../hooks/useFilters';
import { ClearDataCard } from './ClearDataCard';
import { SourcesCard, UploadCard } from './DataSources';

type ImportRow = Schema<'ImportRow'>;
type UnmappedRow = Schema<'UnmappedRow'>;

const STATUS_COLOR: Record<string, string> = { completed: 'teal', failed: 'red', running: 'blue', rolled_back: 'orange' };
const DQ_COLOR: Record<string, string> = { error: 'red', warning: 'yellow', info: 'blue' };

function DqCell({ row }: { row: ImportRow }) {
  const entries = Object.keys(row.dq ?? {});
  if (!row.dq_severity && entries.length === 0) return <Text size="xs" c="dimmed">clean</Text>;
  return (
    <HoverCard width={320} shadow="md" withArrow>
      <HoverCard.Target>
        <Badge size="sm" variant="light" color={DQ_COLOR[row.dq_severity ?? ''] ?? 'gray'} style={{ cursor: 'help' }}>
          {row.dq_severity ?? 'details'}
          {entries.length ? ` · ${entries.length}` : ''}
        </Badge>
      </HoverCard.Target>
      <HoverCard.Dropdown>
        <Code block fz="xs">
          {JSON.stringify(row.dq, null, 2)}
        </Code>
      </HoverCard.Dropdown>
    </HoverCard>
  );
}

const IMPORT_COLUMNS: Column<ImportRow>[] = [
  { key: 'batch_id', header: 'Batch', value: (r) => r.batch_id, align: 'right', width: 70 },
  {
    key: 'file_name',
    header: 'File',
    value: (r) => r.file_name,
    render: (r) => (
      <Stack gap={0}>
        <Text size="sm" style={{ overflowWrap: 'anywhere' }}>
          {r.file_name}
        </Text>
        <Text size="xs" c="dimmed">
          {`${r.mapping_name} · ${r.load_mode}`}
        </Text>
      </Stack>
    ),
  },
  {
    key: 'status',
    header: 'Status',
    value: (r) => r.status,
    render: (r) => (
      <Badge size="sm" variant="light" color={STATUS_COLOR[r.status] ?? 'gray'}>
        {r.status}
      </Badge>
    ),
  },
  { key: 'as_of', header: 'Data as of', value: (r) => r.as_of, render: (r) => formatDate(r.as_of), nowrap: true },
  { key: 'rows_read', header: 'Read', value: (r) => r.rows_read, render: (r) => formatInt(r.rows_read), align: 'right' },
  { key: 'rows_inserted', header: 'New', value: (r) => r.rows_inserted, render: (r) => formatInt(r.rows_inserted), align: 'right' },
  { key: 'rows_updated', header: 'Updated', value: (r) => r.rows_updated, render: (r) => formatInt(r.rows_updated), align: 'right' },
  { key: 'rows_unchanged', header: 'Unchanged', value: (r) => r.rows_unchanged, render: (r) => formatInt(r.rows_unchanged), align: 'right' },
  {
    key: 'rows_rejected',
    header: 'Rejected',
    value: (r) => r.rows_rejected,
    render: (r) => <Text size="sm" c={r.rows_rejected ? 'red' : undefined}>{formatInt(r.rows_rejected)}</Text>,
    align: 'right',
  },
  {
    key: 'rows_soft_deleted',
    header: 'Soft-deleted',
    value: (r) => r.rows_soft_deleted,
    render: (r) => <Text size="sm" c={r.rows_soft_deleted ? 'orange' : undefined}>{formatInt(r.rows_soft_deleted)}</Text>,
    align: 'right',
  },
  { key: 'dq', header: 'DQ', value: (r) => r.dq_severity, render: (r) => <DqCell row={r} /> },
  { key: 'imported_at', header: 'Imported', value: (r) => r.imported_at, render: (r) => formatDateTime(r.imported_at), nowrap: true },
];

export default function DataPage() {
  const { meta } = useShell();
  const location = useLocation();
  const [kind, setKind] = useSearchParam('kind', 'all');
  const [assigning, setAssigning] = useState<UnmappedRow | null>(null);

  const imports = useApi('/api/imports', { query: { limit: 100 } });
  const sources = useApi('/api/sources');
  const unmapped = useApi('/api/dq/unmapped', { query: { kind: kind === 'all' ? null : kind, limit: 500 } });
  const allUnmapped = useApi('/api/dq/unmapped', { query: { limit: 5000 } });

  const kinds = useMemo(() => {
    const counts = new Map<string, number>();
    for (const row of allUnmapped.data?.items ?? []) counts.set(row.kind, (counts.get(row.kind) ?? 0) + 1);
    return [...counts.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [allUnmapped.data]);

  const unmappedColumns: Column<UnmappedRow>[] = [
    { key: 'kind', header: 'Kind', value: (r) => r.kind, render: (r) => <Badge size="sm" variant="light">{humanize(r.kind)}</Badge> },
    {
      key: 'raw_value',
      header: 'Raw value',
      value: (r) => r.raw_value,
      render: (r) => (
        <Text size="sm" ff="monospace" style={{ overflowWrap: 'anywhere' }}>
          {r.raw_value}
        </Text>
      ),
    },
    { key: 'occurrences', header: 'Rows', value: (r) => r.occurrences, render: (r) => formatInt(r.occurrences), align: 'right' },
    {
      key: 'suggestion',
      header: 'Suggestion',
      value: (r) => r.suggestion,
      render: (r) =>
        r.suggestion ? (
          <Group gap={6} wrap="nowrap">
            <Text size="sm">{r.suggestion}</Text>
            {r.score !== null ? (
              <Badge size="xs" variant="outline" color={r.score >= STRONG_MATCH ? 'teal' : 'gray'}>
                {`${Math.round(r.score)}%`}
              </Badge>
            ) : null}
          </Group>
        ) : (
          <Text size="sm" c="dimmed">
            none
          </Text>
        ),
    },
    {
      key: 'batches',
      header: 'Batches',
      value: (r) => r.last_batch_id,
      render: (r) => (r.first_batch_id === r.last_batch_id ? `${r.last_batch_id ?? '–'}` : `${r.first_batch_id}–${r.last_batch_id}`),
      nowrap: true,
    },
    {
      key: 'action',
      header: '',
      sortable: false,
      align: 'right',
      render: (r) => (
        <Button size="compact-xs" variant="light" leftSection={<IconLink size={12} />} onClick={() => setAssigning(r)}>
          Assign alias
        </Button>
      ),
    },
  ];

  const refresh = () => {
    unmapped.reload();
    allUnmapped.reload();
    imports.reload();
  };
  const afterImport = () => {
    refresh();
    sources.reload();
    meta.reload();
  };
  // Stamp set by the sidebar's "Upload an export", fresh on every click so a repeat click scrolls the card again.
  const focusUpload = (location.state as { focusUpload?: number } | null)?.focusUpload;

  return (
    <Stack gap="md">
      <PageHeader
        title="Data"
        description="Sources, uploads, import batches, data quality and unmapped values. Assigning an alias re-links existing rows."
      />
      {/* Stacked (below lg) the upload card goes first: the sources table is long enough to push it off-screen. */}
      <Grid gap="md">
        <Grid.Col span={{ base: 12, lg: 8 }} order={{ base: 2, lg: 1 }}>
          <SourcesCard sources={sources.data} loading={sources.loading} error={sources.error} onRetry={sources.reload} onChanged={afterImport} />
        </Grid.Col>
        <Grid.Col span={{ base: 12, lg: 4 }} order={{ base: 1, lg: 2 }}>
          <UploadCard sources={sources.data} onImported={afterImport} focusKey={focusUpload ? String(focusUpload) : undefined} />
        </Grid.Col>
      </Grid>
      <Grid gap="md">
        <Grid.Col span={{ base: 12, lg: 4 }}>
          <SectionCard title="Freshness" description="Newest data per source">
            {meta.error ? <ErrorState error={meta.error} onRetry={meta.reload} compact /> : null}
            <FreshnessList rows={meta.data?.freshness} asOf={meta.data?.as_of_default} />
          </SectionCard>
        </Grid.Col>
        <Grid.Col span={{ base: 12, lg: 8 }}>
          <SectionCard
            title="Unmapped values"
            description="Raw values that did not resolve to a known application, vendor or group"
            count={unmapped.data?.items.length ?? null}
            actions={
              kinds.length > 1 ? (
                <SegmentedControl
                  size="xs"
                  value={kind}
                  onChange={setKind}
                  data={[
                    { value: 'all', label: `All (${allUnmapped.data?.items.length ?? 0})` },
                    ...kinds.map(([k, n]) => ({ value: k, label: `${humanize(k)} (${n})` })),
                  ]}
                />
              ) : undefined
            }
          >
            <DataTable
              rows={unmapped.data?.items}
              columns={unmappedColumns}
              rowKey={(r) => `${r.kind}:${r.raw_value}`}
              loading={unmapped.loading}
              error={unmapped.error}
              onRetry={unmapped.reload}
              emptyText="Every value is mapped"
              initialSort={{ key: 'occurrences', dir: 'desc' }}
              pageSize={25}
              minWidth={720}
            />
          </SectionCard>
        </Grid.Col>
      </Grid>
      <ClearDataCard onCleared={afterImport} />
      <SectionCard title="Import batches" description="Latest 100 imports, newest first" count={imports.data?.items.length ?? null}>
        <DataTable
          rows={imports.data?.items}
          columns={IMPORT_COLUMNS}
          rowKey={(r) => String(r.batch_id)}
          loading={imports.loading}
          error={imports.error}
          onRetry={imports.reload}
          emptyText="No imports yet"
          emptyDescription="Upload an export above, pull a connector, or drop exports in the inbox and run `sed import --inbox`."
          pageSize={25}
          minWidth={1100}
        />
      </SectionCard>
      <AssignAliasModal row={assigning} onClose={() => setAssigning(null)} onAssigned={refresh} />
    </Stack>
  );
}
