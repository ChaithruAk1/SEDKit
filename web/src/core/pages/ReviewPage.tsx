import {
  Alert,
  Anchor,
  Badge,
  Button,
  Card,
  Group,
  Kbd,
  Modal,
  SegmentedControl,
  SimpleGrid,
  Stack,
  Table,
  Text,
  TextInput,
  Textarea,
  Title,
} from '@mantine/core';
import { useHotkeys } from '@mantine/hooks';
import { IconAlertTriangle, IconCheck } from '@tabler/icons-react';
import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router';

import type { Schema } from '../../api/types';
import { useApi, useApiPost } from '../../api/useApi';
import { EmptyState } from '../../components/EmptyState';
import { ErrorState } from '../../components/ErrorState';
import { formatDate, formatDateTime, formatInt, formatRatio, humanize } from '../../components/format';
import { Markdown } from '../../components/Markdown';
import { PageHeader } from '../../components/PageHeader';
import { ProvenanceBadge } from '../../components/ProvenanceBadge';
import { SectionCard } from '../../components/SectionCard';
import { SystemDetectedBadge } from '../../components/SystemDetectedBadge';
import { useSearchParam } from '../../hooks/useFilters';

type ReviewItem = Schema<'ReviewItem'>;
type Action = Schema<'FindingReviewIn'>['action'];
type DialogMode = 'reject' | 'edit' | 'acknowledge' | 'suppress_until';

const SEVERITY_COLOR: Record<string, string> = { critical: 'red', high: 'red', medium: 'orange', low: 'yellow', info: 'gray' };
const STATUS_LABEL: Record<string, string> = {
  draft: 'Draft',
  update_pending: 'Wording update',
  stale_input: 'Stale input',
  active: 'Active',
};
const FILTERS = [
  { value: 'all', label: 'All' },
  { value: 'draft', label: 'Drafts' },
  { value: 'update_pending', label: 'Wording updates' },
  { value: 'stale_input', label: 'Stale input' },
  { value: 'rule', label: 'System-detected' },
] as const;

function matches(item: ReviewItem, filter: string): boolean {
  if (filter === 'all') return true;
  if (filter === 'rule') return item.origin === 'rule';
  return item.origin === 'ai' && item.status === filter;
}

function factsOf(item: ReviewItem): Record<string, unknown> {
  return Object.fromEntries(item.evidence.map((e) => [e.fact_key, e.value]));
}

function valueText(value: unknown): string {
  if (typeof value === 'number') return value.toLocaleString('en-GB');
  if (value === null || value === undefined) return '–';
  return typeof value === 'string' ? value : JSON.stringify(value);
}

/** Actions a finding takes in its current state (the server enforces the same rules). */
function actionsFor(item: ReviewItem): { primary: Action | null; dialogs: DialogMode[] } {
  if (item.origin === 'rule') return { primary: null, dialogs: ['acknowledge', 'suppress_until'] };
  if (item.status === 'update_pending') return { primary: 'approve_update', dialogs: ['edit', 'reject'] };
  return { primary: 'approve', dialogs: ['edit', 'reject'] };
}

const DIALOG_LABEL: Record<DialogMode, string> = {
  reject: 'Reject',
  edit: 'Edit and approve',
  acknowledge: 'Acknowledge',
  suppress_until: 'Suppress until…',
};

function ItemCard({
  item,
  selected,
  busy,
  onSelect,
  onPrimary,
  onDialog,
}: {
  item: ReviewItem;
  selected: boolean;
  busy: boolean;
  onSelect: () => void;
  onPrimary: (action: Action) => void;
  onDialog: (mode: DialogMode) => void;
}) {
  const facts = factsOf(item);
  const { primary, dialogs } = actionsFor(item);
  return (
    <Card
      withBorder
      radius="md"
      padding="md"
      onClick={onSelect}
      data-finding={item.finding_id}
      aria-current={selected ? 'true' : undefined}
      style={{
        borderColor: selected ? 'var(--mantine-color-blue-filled)' : undefined,
        boxShadow: selected ? '0 0 0 1px var(--mantine-color-blue-filled)' : undefined,
      }}
    >
      <Stack gap="xs">
        <Group justify="space-between" align="flex-start" gap="xs">
          <Stack gap={4} style={{ minWidth: 0, flex: '1 1 320px' }}>
            <Group gap={6}>
              <Badge size="xs" variant="filled" color={SEVERITY_COLOR[item.severity ?? ''] ?? 'gray'}>
                {item.severity ?? 'n/a'}
              </Badge>
              <Badge size="xs" variant="default">
                {humanize(item.kind)}
              </Badge>
              {item.origin === 'rule' ? (
                <SystemDetectedBadge />
              ) : (
                <ProvenanceBadge runId={item.run_id} status={item.status} confidence={item.confidence} />
              )}
              <Badge size="xs" variant="outline" color={item.status === 'stale_input' ? 'yellow' : 'gray'}>
                {STATUS_LABEL[item.status] ?? item.status}
              </Badge>
            </Group>
            <Text fw={600} size="sm" style={{ overflowWrap: 'anywhere' }}>
              {item.title}
            </Text>
            {item.subject_id ? (
              <Text size="xs" c="dimmed">
                {`${humanize(item.subject_type)} ${item.subject_id}`}
              </Text>
            ) : null}
          </Stack>
          <Group gap={6} wrap="nowrap" onClick={(event) => event.stopPropagation()}>
            {primary ? (
              <Button size="compact-sm" leftSection={<IconCheck size={14} />} loading={busy} onClick={() => onPrimary(primary)}>
                {primary === 'approve_update' ? 'Approve update' : 'Approve'}
              </Button>
            ) : null}
            {dialogs.map((mode) => (
              <Button key={mode} size="compact-sm" variant="default" disabled={busy} onClick={() => onDialog(mode)}>
                {DIALOG_LABEL[mode]}
              </Button>
            ))}
          </Group>
        </Group>

        {item.status === 'stale_input' ? (
          <Alert color="yellow" variant="light" icon={<IconAlertTriangle size={16} />} p="xs">
            <Text size="xs">An input run of this finding was rejected. Re-run the analysis, or approve only if the text still holds.</Text>
          </Alert>
        ) : null}

        {item.status === 'update_pending' ? (
          <SimpleGrid cols={{ base: 1, md: 2 }} spacing="sm">
            <Stack gap={2}>
              <Text size="xs" c="dimmed" fw={600}>
                Published text
              </Text>
              <Markdown facts={facts}>{item.body_md}</Markdown>
            </Stack>
            <Stack gap={2}>
              <Text size="xs" c="dimmed" fw={600}>
                New AI wording
              </Text>
              <Markdown facts={facts}>{item.pending_body_md}</Markdown>
            </Stack>
          </SimpleGrid>
        ) : (
          <Markdown facts={facts}>{item.body_md}</Markdown>
        )}

        {item.material_change.length ? (
          <Group gap={4}>
            <Text size="xs" c="dimmed">
              Changed since the approved version:
            </Text>
            {item.material_change.map((reason) => (
              <Badge key={reason} size="xs" variant="light" color="orange">
                {reason}
              </Badge>
            ))}
          </Group>
        ) : null}

        <Group gap="md">
          {item.ticket_count !== null ? <Meta label="Tickets" value={formatInt(item.ticket_count)} /> : null}
          {item.periodicity ? <Meta label="Pattern" value={humanize(item.periodicity)} /> : null}
          {item.suspected_change ? <Meta label="Suspected change" value={item.suspected_change} /> : null}
          {item.recommendation ? <Meta label="Recommendation" value={humanize(item.recommendation)} /> : null}
          {item.decision_due ? <Meta label="Decision due" value={formatDate(item.decision_due)} /> : null}
          {item.confidence !== null && item.origin === 'ai' ? <Meta label="Confidence" value={formatRatio(item.confidence)} /> : null}
        </Group>

        {item.evidence.length ? (
          <Table withRowBorders={false} verticalSpacing={2} horizontalSpacing="xs" fz="xs" maw={520}>
            <Table.Tbody>
              {item.evidence.map((e) => (
                <Table.Tr key={e.fact_key}>
                  <Table.Td c="dimmed" ff="monospace">
                    {e.fact_key}
                  </Table.Td>
                  <Table.Td ta="right">{valueText(e.value)}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        ) : null}
      </Stack>
    </Card>
  );
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <Text size="xs">
      <Text span c="dimmed" inherit>
        {label}:{' '}
      </Text>
      {value}
    </Text>
  );
}

function DecisionDialog({
  item,
  mode,
  pending,
  error,
  onClose,
  onSubmit,
}: {
  item: ReviewItem | null;
  mode: DialogMode | null;
  pending: boolean;
  error: Parameters<typeof ErrorState>[0]['error'];
  onClose: () => void;
  onSubmit: (body: { note: string | null; body_md?: string; until?: string }) => void;
}) {
  const [note, setNote] = useState('');
  const [body, setBody] = useState('');
  const [until, setUntil] = useState('');
  useEffect(() => {
    setNote('');
    setUntil('');
    setBody(item?.pending_body_md ?? item?.body_md ?? '');
  }, [item, mode]);
  if (!item || !mode) return null;
  const needsNote = mode !== 'edit';
  const ready = (!needsNote || note.trim() !== '') && (mode !== 'edit' || body.trim() !== '') && (mode !== 'suppress_until' || until !== '');
  const submit = () => {
    if (!ready) return;
    onSubmit({
      note: note.trim() || null,
      ...(mode === 'edit' ? { body_md: body } : {}),
      ...(mode === 'suppress_until' ? { until } : {}),
    });
  };
  return (
    <Modal opened onClose={onClose} title={`${DIALOG_LABEL[mode]}: ${item.title}`} size={mode === 'edit' ? 'xl' : 'md'} centered>
      <Stack gap="sm">
        {mode === 'edit' ? (
          <>
            <Textarea
              label="Text to publish"
              description="Numbers stay as {{f:<fact_key>}} tokens from the evidence; the edit is approved as you save it."
              autosize
              minRows={6}
              maxRows={16}
              value={body}
              onChange={(event) => setBody(event.currentTarget.value)}
              data-autofocus
            />
            <Card withBorder padding="xs">
              <Text size="xs" c="dimmed" mb={4}>
                Preview
              </Text>
              <Markdown facts={factsOf(item)}>{body}</Markdown>
            </Card>
          </>
        ) : null}
        {mode === 'suppress_until' ? (
          <TextInput type="date" label="Hide until" description="The finding returns after this date or when its evidence changes" value={until} onChange={(event) => setUntil(event.currentTarget.value)} required />
        ) : null}
        <Textarea
          label="Note"
          description={needsNote ? 'Required: why this decision' : 'Optional'}
          autosize
          minRows={2}
          value={note}
          onChange={(event) => setNote(event.currentTarget.value)}
          required={needsNote}
          data-autofocus={mode !== 'edit' ? true : undefined}
        />
        <ErrorState error={error} compact />
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} loading={pending} disabled={!ready} color={mode === 'reject' ? 'red' : undefined}>
            {DIALOG_LABEL[mode].replace('…', '')}
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}

export default function ReviewPage() {
  const [filter, setFilter] = useSearchParam('show', 'all');
  const queue = useApi('/api/review/queue', { query: { limit: 500 } });
  const waitingRuns = useApi('/api/runs', { query: { status: 'completed', limit: 50 } });
  const decide = useApiPost('/api/findings/{finding_id}/review');
  const bulk = useApiPost('/api/findings/bulk-review');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [dialog, setDialog] = useState<{ item: ReviewItem; mode: DialogMode } | null>(null);
  const [done, setDone] = useState<string | null>(null);

  const all = queue.data?.items;
  // Grouped by kind in first-seen order; keyboard navigation follows the same order as the screen.
  const groups = useMemo(() => {
    const byKind = new Map<string, ReviewItem[]>();
    for (const item of (all ?? []).filter((i) => matches(i, filter))) byKind.set(item.kind, [...(byKind.get(item.kind) ?? []), item]);
    return [...byKind.entries()];
  }, [all, filter]);
  const items = useMemo(() => groups.flatMap(([, group]) => group), [groups]);
  const updates = useMemo(() => (all ?? []).filter((i) => i.origin === 'ai' && i.status === 'update_pending'), [all]);
  const selectedIndex = Math.max(0, items.findIndex((i) => i.finding_id === selectedId));
  const selected = items[selectedIndex] ?? null;

  const counts = useMemo(() => {
    const out: Record<string, number> = { all: all?.length ?? 0 };
    for (const f of FILTERS) if (f.value !== 'all') out[f.value] = (all ?? []).filter((i) => matches(i, f.value)).length;
    return out;
  }, [all]);

  const afterDecision = (message: string) => {
    setDone(message);
    setDialog(null);
    const next = items[selectedIndex + 1] ?? items[selectedIndex - 1] ?? null;
    setSelectedId(next?.finding_id ?? null);
    queue.reload();
  };

  const run = async (item: ReviewItem, action: Action, extra: { note?: string | null; body_md?: string; until?: string } = {}) => {
    try {
      const result = await decide.run({ action, ...extra }, { params: { finding_id: item.finding_id } });
      const status = result.results[0]?.status ?? action;
      afterDecision(`${humanize(status)}: ${item.title}`);
    } catch {
      // Shown from decide.error.
    }
  };

  const approveAllUpdates = async () => {
    try {
      const result = await bulk.run({ finding_ids: updates.map((i) => i.finding_id), action: 'approve_update' });
      afterDecision(`Approved ${result.results.length} wording updates`);
    } catch {
      // Shown from bulk.error.
    }
  };

  const move = (step: number) => {
    if (!items.length) return;
    const index = Math.min(items.length - 1, Math.max(0, selectedIndex + step));
    setSelectedId(items[index]!.finding_id);
    document.querySelector(`[data-finding="${CSS.escape(items[index]!.finding_id)}"]`)?.scrollIntoView({ block: 'nearest' });
  };

  useHotkeys(
    dialog
      ? []
      : [
          ['j', () => move(1)],
          ['k', () => move(-1)],
          [
            'a',
            () => {
              const primary = selected ? actionsFor(selected).primary : null;
              if (selected && primary && !decide.pending) void run(selected, primary);
            },
          ],
          ['r', () => selected?.origin === 'ai' && setDialog({ item: selected, mode: 'reject' })],
          ['e', () => selected?.origin === 'ai' && setDialog({ item: selected, mode: 'edit' })],
        ],
  );

  return (
    <Stack gap="md">
      <PageHeader
        title="Review"
        description={
          <>
            AI drafts, wording updates and findings with stale inputs wait here; system-detected findings can be acknowledged or
            suppressed. Keys: <Kbd size="xs">j</Kbd> <Kbd size="xs">k</Kbd> move, <Kbd size="xs">a</Kbd> approve,{' '}
            <Kbd size="xs">r</Kbd> reject, <Kbd size="xs">e</Kbd> edit.
          </>
        }
      />

      {waitingRuns.data?.items.length ? (
        <SectionCard
          title="Runs waiting for approval"
          description="Label runs are approved from their review sample; finding runs through the findings below"
          count={waitingRuns.data.items.length}
        >
          <Stack gap={4}>
            {waitingRuns.data.items.map((r) => (
              <Group key={r.run_id} gap="xs">
                <Anchor component={Link} to={`/runs/${encodeURIComponent(r.run_id)}`} size="sm" ff="monospace">
                  {r.run_id}
                </Anchor>
                <Badge size="xs" variant="default">
                  {r.skill}
                </Badge>
                <Text size="xs" c="dimmed">
                  {`finished ${formatDateTime(r.finished_at)}`}
                </Text>
              </Group>
            ))}
          </Stack>
        </SectionCard>
      ) : null}

      <Group justify="space-between" gap="sm">
        <SegmentedControl
          size="xs"
          value={filter}
          onChange={setFilter}
          data={FILTERS.map((f) => ({ value: f.value, label: `${f.label} (${counts[f.value] ?? 0})` }))}
        />
        {updates.length > 1 ? (
          <Button size="xs" variant="light" onClick={approveAllUpdates} loading={bulk.pending}>
            {`Approve all ${updates.length} wording updates`}
          </Button>
        ) : null}
      </Group>

      {done ? (
        <Alert color="teal" icon={<IconCheck size={16} />} withCloseButton onClose={() => setDone(null)} p="xs">
          <Text size="sm">{done}</Text>
        </Alert>
      ) : null}
      {!dialog ? <ErrorState error={decide.error ?? bulk.error} compact /> : null}
      {queue.error ? <ErrorState error={queue.error} onRetry={queue.reload} /> : null}

      {queue.loading && !all ? (
        <Text size="sm" c="dimmed">
          Loading the review queue…
        </Text>
      ) : items.length === 0 && !queue.error ? (
        <EmptyState
          title={filter === 'all' ? 'Nothing waiting for review' : 'Nothing in this view'}
          description="New AI findings appear here after an analysis run finishes."
        />
      ) : (
        groups.map(([kind, group]) => (
          <Stack key={kind} gap="xs">
            <Group gap={6}>
              <Title order={4} fz="sm">
                {humanize(kind)}
              </Title>
              <Badge size="sm" variant="light" color="gray">
                {group.length}
              </Badge>
            </Group>
            {group.map((item) => (
              <ItemCard
                key={item.finding_id}
                item={item}
                selected={selected?.finding_id === item.finding_id}
                busy={decide.pending && selected?.finding_id === item.finding_id}
                onSelect={() => setSelectedId(item.finding_id)}
                onPrimary={(action) => {
                  setSelectedId(item.finding_id);
                  void run(item, action);
                }}
                onDialog={(mode) => {
                  setSelectedId(item.finding_id);
                  decide.reset();
                  setDialog({ item, mode });
                }}
              />
            ))}
          </Stack>
        ))
      )}

      <DecisionDialog
        item={dialog?.item ?? null}
        mode={dialog?.mode ?? null}
        pending={decide.pending}
        error={decide.error}
        onClose={() => setDialog(null)}
        onSubmit={(body) => dialog && void run(dialog.item, dialog.mode, body)}
      />
    </Stack>
  );
}
