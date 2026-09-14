import { Badge, Group, Stack, Text } from '@mantine/core';

import type { TicketRow } from '../../../api/types';
import type { Column } from '../../../components/DataTable';
import { formatDateTime, humanize, priorityLabel } from '../../../components/format';
import { ProvenanceBadge } from '../../../components/ProvenanceBadge';

export function PriorityBadge({ priority }: { priority: number | null }) {
  const color = priority === 1 ? 'red' : priority === 2 ? 'orange' : 'gray';
  return (
    <Badge size="sm" variant={priority !== null && priority <= 2 ? 'filled' : 'light'} color={color}>
      {priorityLabel(priority)}
    </Badge>
  );
}

/** ServiceNow category next to the AI app-owner category (with provenance), as on the tickets search. */
export function CategoryPair({ ticket }: { ticket: TicketRow }) {
  return (
    <Stack gap={2}>
      <Text size="xs">
        <Text span c="dimmed" inherit>
          SN:{' '}
        </Text>
        {ticket.sn_category ?? '–'}
      </Text>
      <Group gap={4} wrap="nowrap">
        <Text size="xs">
          <Text span c="dimmed" inherit>
            AI:{' '}
          </Text>
          {ticket.am_category ? humanize(ticket.am_category) : '–'}
          {ticket.am_subcategory ? ` / ${humanize(ticket.am_subcategory)}` : ''}
        </Text>
        <ProvenanceBadge runId={ticket.label_run_id} confidence={ticket.label_confidence} />
      </Group>
    </Stack>
  );
}

export function ticketColumns({ showApp = true }: { showApp?: boolean } = {}): Column<TicketRow>[] {
  const columns: (Column<TicketRow> | null)[] = [
    {
      key: 'number',
      header: 'Number',
      value: (t) => t.number,
      render: (t) => (
        <Stack gap={0}>
          <Text size="sm" fw={600} ff="monospace">
            {t.number}
          </Text>
          <Text size="xs" c="dimmed">
            {humanize(t.kind)}
          </Text>
        </Stack>
      ),
      nowrap: true,
    },
    { key: 'priority', header: 'Prio', value: (t) => t.priority, render: (t) => <PriorityBadge priority={t.priority} /> },
    {
      key: 'state',
      header: 'State',
      value: (t) => t.state,
      render: (t) => (
        <Group gap={4} wrap="nowrap">
          <Text size="sm">{t.state ?? '–'}</Text>
          {t.stale_open ? (
            <Badge size="xs" color="orange" variant="outline">
              stale
            </Badge>
          ) : null}
        </Group>
      ),
    },
    {
      key: 'short_description',
      header: 'Short description',
      value: (t) => t.short_description,
      render: (t) => (
        <Text size="sm" lineClamp={2}>
          {t.short_description ?? '–'}
        </Text>
      ),
    },
    showApp ? { key: 'app', header: 'Application', value: (t) => t.app_name ?? t.app_id } : null,
    { key: 'group', header: 'Group', value: (t) => t.assignment_group },
    { key: 'category', header: 'Category (SN vs AI)', sortable: false, render: (t) => <CategoryPair ticket={t} /> },
    { key: 'opened_at', header: 'Opened', value: (t) => t.opened_at, render: (t) => formatDateTime(t.opened_at), nowrap: true },
  ];
  return columns.filter((c): c is Column<TicketRow> => c !== null);
}
