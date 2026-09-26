/**
 * The audit trail (`#/audit`): who did what and when, newest first (docs/audit.md). Filter by person, action, outcome,
 * days or words; open an entry for its details and, for a change, each value before and after. The chain check sits
 * at the top. The workbook download takes the same filters and is itself put on the trail.
 */
import { Alert, Badge, Button, Code, Group, Modal, Pagination, SegmentedControl, Select, Stack, Table, Text, TextInput, Tooltip } from '@mantine/core';
import { IconFileSpreadsheet, IconShieldCheck, IconShieldX, IconX } from '@tabler/icons-react';
import { type ReactNode, useState } from 'react';
import { useSearchParams } from 'react-router';

import type { Schema } from '../../api/types';
import { useApi } from '../../api/useApi';
import { type Column, DataTable } from '../../components/DataTable';
import { formatDateTime } from '../../components/format';
import { PageHeader } from '../../components/PageHeader';
import { SectionCard } from '../../components/SectionCard';
import { usePatchSearchParams } from '../../hooks/useFilters';

type Entry = Schema<'AuditEntryOut'>;
type Integrity = Schema<'AuditIntegrityOut'>;

const PAGE_SIZE = 100;
const OUTCOMES = [
  { value: 'all', label: 'All' },
  { value: 'done', label: 'Done' },
  { value: 'failed', label: 'Failed' },
  { value: 'refused', label: 'Refused' },
  { value: 'started', label: 'Started' },
];
const OUTCOME_WORDS: Record<string, string> = { done: 'Done', failed: 'Failed', refused: 'Refused', started: 'Started' };
const OUTCOME_COLOR: Record<string, string> = { done: 'teal', failed: 'red', refused: 'orange', started: 'gray' };
const METHOD_WORDS: Record<string, string> = {
  microsoft: 'company SSO (Microsoft)',
  google: 'Google',
  github: 'GitHub',
  developer_mode: 'developer mode, not proven',
  windows: 'Windows account, not proven',
};
const WHERE_WORDS: Record<string, string> = { dashboard: 'Dashboard', command_line: 'Command line' };

function OutcomeBadge({ entry }: { entry: Entry }) {
  if (entry.open) {
    return (
      <Tooltip label="SED recorded the start but not how it ended: it stopped half-way, or it is still running" withinPortal>
        <Badge color="yellow" variant="light" tt="none">
          No outcome recorded
        </Badge>
      </Tooltip>
    );
  }
  return (
    <Badge color={OUTCOME_COLOR[entry.outcome] ?? 'gray'} variant="light" tt="none">
      {OUTCOME_WORDS[entry.outcome] ?? entry.outcome}
    </Badge>
  );
}

function Who({ entry }: { entry: Entry }) {
  return (
    <Stack gap={0}>
      <Text size="sm" fw={600}>
        {entry.actor_name}
      </Text>
      <Text size="xs" c="dimmed">
        {entry.verified ? `${entry.actor} · ${METHOD_WORDS[entry.method] ?? entry.method}` : METHOD_WORDS[entry.method] ?? entry.method}
      </Text>
    </Stack>
  );
}

const COLUMNS: Column<Entry>[] = [
  { key: 'at', header: 'When', value: (e) => e.at, render: (e) => formatDateTime(e.at), nowrap: true },
  { key: 'who', header: 'Who', value: (e) => e.actor_name, render: (e) => <Who entry={e} /> },
  { key: 'channel', header: 'Where', value: (e) => WHERE_WORDS[e.channel] ?? e.channel, nowrap: true },
  { key: 'action', header: 'Action', value: (e) => e.action_label, nowrap: true },
  { key: 'outcome', header: 'Outcome', value: (e) => e.outcome, render: (e) => <OutcomeBadge entry={e} /> },
  { key: 'summary', header: 'What happened', value: (e) => e.summary },
];

function IntegrityNote({ integrity }: { integrity: Integrity | undefined }) {
  if (!integrity) return null;
  if (!integrity.intact) {
    return (
      <Alert color="red" variant="light" icon={<IconShieldX size={18} />} title="The trail was changed outside SED">
        <Text size="sm">
          Entry {integrity.first_break} is missing or was changed after SED wrote it. Entries from there on can no
          longer be trusted as recorded. Keep a copy of the download below and compare it with your backups.
        </Text>
      </Alert>
    );
  }
  const since = integrity.first_at ? ` since ${formatDateTime(integrity.first_at)}` : '';
  return (
    <Alert color="teal" variant="light" icon={<IconShieldCheck size={18} />} title="The trail is intact">
      <Stack gap={4}>
        <Text size="sm">
          {integrity.entries.toLocaleString()} {integrity.entries === 1 ? 'entry' : 'entries'}
          {since}; every entry fits the one before it. Nothing here can be changed or deleted from SED.
        </Text>
        <Text size="xs" c="dimmed">
          The check catches entries changed or removed by hand. It is not a lock: someone with your Windows login could
          still rewrite or delete the whole file.
        </Text>
      </Stack>
    </Alert>
  );
}

function pretty(value: unknown): string {
  if (value === null || value === undefined) return '–';
  return typeof value === 'string' ? value : JSON.stringify(value, null, 2);
}

function EntryDetail({ entry, onClose, onShowAction }: { entry: Entry | null; onClose: () => void; onShowAction: (id: string) => void }) {
  if (!entry) return null;
  const rows: [string, ReactNode][] = [
    ['When', `${formatDateTime(entry.at)} (${entry.at} UTC)`],
    ['Who', entry.verified ? entry.actor : `${entry.actor} (not proven)`],
    ['Identified by', METHOD_WORDS[entry.method] ?? entry.method],
    ['Where', WHERE_WORDS[entry.channel] ?? entry.channel],
    ['Action', entry.action_label],
    ['Outcome', <OutcomeBadge key="outcome" entry={entry} />],
    ['What happened', entry.summary],
  ];
  if (entry.target_id) rows.push(['About', entry.target_id]);
  return (
    <Modal opened onClose={onClose} title={`Entry ${entry.seq}`} size="lg">
      <Stack gap="sm">
        <Table>
          <Table.Tbody>
            {rows.map(([label, value]) => (
              <Table.Tr key={label}>
                <Table.Th>{label}</Table.Th>
                <Table.Td>{value}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
        {entry.changes && entry.changes.length > 0 ? (
          <Stack gap={4}>
            <Text size="sm" fw={600}>
              What changed
            </Text>
            <Table>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Setting</Table.Th>
                  <Table.Th>Before</Table.Th>
                  <Table.Th>After</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {entry.changes.map((change, index) => (
                  <Table.Tr key={`${String(change.field)}-${index}`}>
                    <Table.Td>{String(change.field)}</Table.Td>
                    <Table.Td>
                      <Code block>{pretty(change.before)}</Code>
                    </Table.Td>
                    <Table.Td>
                      <Code block>{pretty(change.after)}</Code>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Stack>
        ) : null}
        {Object.keys(entry.detail).length > 0 ? (
          <Stack gap={4}>
            <Text size="sm" fw={600}>
              Details
            </Text>
            <Code block>{JSON.stringify(entry.detail, null, 2)}</Code>
          </Stack>
        ) : null}
        <Text size="xs" c="dimmed">
          Fingerprint {entry.entry_hash}
        </Text>
        {entry.correlation_id ? (
          <Group>
            <Button size="xs" variant="default" onClick={() => onShowAction(entry.correlation_id ?? '')}>
              Show how this action started and ended
            </Button>
          </Group>
        ) : null}
      </Stack>
    </Modal>
  );
}

export default function AuditPage() {
  const [params] = useSearchParams();
  const patch = usePatchSearchParams();
  const [open, setOpen] = useState<Entry | null>(null);
  const person = params.get('person') ?? '';
  const action = params.get('action') ?? '';
  const outcome = params.get('outcome') ?? 'all';
  const since = params.get('since') ?? '';
  const until = params.get('until') ?? '';
  const search = params.get('q') ?? '';
  const correlation = params.get('correlation') ?? '';
  const page = Math.max(1, Number.parseInt(params.get('page') ?? '1', 10) || 1);

  const filters = {
    person: person || null,
    action: action || null,
    outcome: outcome === 'all' ? null : (outcome as 'started' | 'done' | 'failed' | 'refused'),
    since: since || null,
    until: until || null,
    q: search || null,
    correlation: correlation || null,
  };
  const trail = useApi('/api/audit', { query: { ...filters, page, page_size: PAGE_SIZE } });
  const data = trail.data;
  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;

  const download = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) if (value) download.set(key, value);
  const downloadHref = `/api/audit-export.xlsx${download.toString() ? `?${download.toString()}` : ''}`;

  /** One history update per change: the filter and a return to the first page together. */
  const changeFilter = (key: string) => (value: string | null) => patch({ [key]: value, page: null });

  return (
    <Stack gap="md">
      <PageHeader
        title="Audit trail"
        description="Who did what, and when: sign-ins, downloads, pulls, imports, cleared data, restores, AI runs and sign-in changes. Kept forever."
        actions={
          <Tooltip label="The same filters as the table; the download is itself recorded" withinPortal>
            <Button size="xs" variant="default" component="a" href={downloadHref} leftSection={<IconFileSpreadsheet size={14} />}>
              Download as a workbook
            </Button>
          </Tooltip>
        }
      />
      <IntegrityNote integrity={data?.integrity} />
      <SectionCard
        title="Entries"
        description="Newest first. Open an entry for its details."
        count={data?.total ?? null}
        actions={
          <SegmentedControl size="xs" value={outcome} onChange={(value) => changeFilter('outcome')(value === 'all' ? null : value)} data={OUTCOMES} />
        }
      >
        <Stack gap="sm">
          <Group gap="sm" align="flex-end" wrap="wrap">
            <Select
              size="xs"
              label="Person"
              placeholder="Everyone"
              clearable
              searchable
              value={person || null}
              onChange={changeFilter('person')}
              data={(data?.people ?? []).map((p) => ({ value: p.id, label: p.verified ? `${p.name} (${p.id})` : `${p.name} (not proven)` }))}
            />
            <Select
              size="xs"
              label="Action"
              placeholder="All actions"
              clearable
              value={action || null}
              onChange={changeFilter('action')}
              data={(data?.actions ?? []).map((a) => ({ value: a.key, label: a.label }))}
            />
            <TextInput size="xs" type="date" label="From" value={since} onChange={(event) => changeFilter('since')(event.currentTarget.value)} />
            <TextInput size="xs" type="date" label="To" value={until} onChange={(event) => changeFilter('until')(event.currentTarget.value)} />
            <TextInput size="xs" label="Words" placeholder="In what happened, who or the file" value={search} onChange={(event) => changeFilter('q')(event.currentTarget.value)} />
            {correlation ? (
              <Button size="xs" variant="default" rightSection={<IconX size={12} />} onClick={() => changeFilter('correlation')(null)} aria-label="One action only: show every action again">
                One action only
              </Button>
            ) : null}
          </Group>
          <DataTable
            rows={data?.items}
            columns={COLUMNS}
            rowKey={(e) => String(e.seq)}
            loading={trail.loading}
            error={trail.error}
            onRetry={trail.reload}
            onRowClick={setOpen}
            rowLabel={(e) => `Open entry ${e.seq}`}
            emptyText="Nothing on the trail matches"
            emptyDescription="Sign-ins, downloads, pulls, imports and the other actions appear here as they happen."
            serverOrdered
            minWidth={900}
          />
          {pages > 1 ? (
            <Group justify="flex-end">
              <Pagination size="sm" total={pages} value={Math.min(page, pages)} onChange={(p) => patch({ page: p === 1 ? null : String(p) })} />
            </Group>
          ) : null}
        </Stack>
      </SectionCard>
      <EntryDetail
        entry={open}
        onClose={() => setOpen(null)}
        onShowAction={(id) => {
          setOpen(null);
          patch({ person: null, action: null, outcome: null, since: null, until: null, q: null, correlation: id, page: null });
        }}
      />
    </Stack>
  );
}
