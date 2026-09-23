import { Anchor, Badge, Card, Divider, Drawer, Group, Loader, SimpleGrid, Stack, Text, Title } from '@mantine/core';
import type { ReactNode } from 'react';
import { Link } from 'react-router';

import type { TicketDetail } from '../../../api/types';
import { useApi } from '../../../api/useApi';
import { ErrorState } from '../../../components/ErrorState';
import { formatBool, formatDateTime, formatInt, formatRatio, humanize, priorityLabel } from '../../../components/format';
import { ProvenanceBadge } from '../../../components/ProvenanceBadge';
import { useFilters, useSearchParam } from '../../../hooks/useFilters';
import { appHref } from '../links';

/** The ticket drawer is driven by `?ticket=<ticket_id>` so an open ticket is linkable. */
export function useTicketParam(): [string, (ticketId: string | null) => void] {
  return useSearchParam('ticket', '');
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <Text size="xs" c="dimmed">
        {label}
      </Text>
      <Text size="sm" component="div" style={{ overflowWrap: 'anywhere' }}>
        {children}
      </Text>
    </div>
  );
}

/** Ticket text is untrusted data: shown as plain text (never markdown or HTML). */
function PlainText({ children }: { children: string | null }) {
  if (!children) return <Text size="sm" c="dimmed">No text</Text>;
  return (
    <Text size="sm" style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>
      {children}
    </Text>
  );
}

function Body({ ticket }: { ticket: TicketDetail }) {
  const { search } = useFilters();
  return (
    <Stack gap="md">
      <Group gap={6}>
        <Badge variant="default">{humanize(ticket.kind)}</Badge>
        <Badge color={(ticket.priority ?? 9) <= 2 ? 'red' : 'gray'} variant="light">
          {priorityLabel(ticket.priority)}
        </Badge>
        <Badge color={ticket.is_open ? 'blue' : 'teal'} variant="light">
          {ticket.state ?? (ticket.is_open ? 'open' : 'closed')}
        </Badge>
        {ticket.stale_open ? (
          <Badge color="orange" variant="outline">
            stale open
          </Badge>
        ) : null}
      </Group>
      <Title order={4}>{ticket.short_description ?? ticket.number}</Title>

      <SimpleGrid cols={2} spacing="sm">
        <Field label="Application">
          {ticket.app_id ? (
            <Anchor component={Link} to={appHref(ticket.app_id, search)}>
              {ticket.app_name ?? ticket.app_id}
            </Anchor>
          ) : (
            'Unmapped'
          )}
        </Field>
        <Field label="Vendor">{ticket.vendor_name ?? '–'}</Field>
        <Field label="Assignment group">{ticket.assignment_group ?? 'Unassigned'}</Field>
        <Field label="Assigned to (pseudonym)">{ticket.assigned_to_pid ?? 'Unassigned'}</Field>
        <Field label="Opened">{formatDateTime(ticket.opened_at)}</Field>
        <Field label="Resolved">{formatDateTime(ticket.resolved_at)}</Field>
        <Field label="Reassignments">{formatInt(ticket.reassignment_count)}</Field>
        <Field label="Reopened">{formatInt(ticket.reopen_count)}</Field>
        <Field label="Made SLA">{formatBool(ticket.made_sla)}</Field>
        <Field label="Updated">{formatDateTime(ticket.sys_updated_on)}</Field>
        <Field label="CI (raw)">{ticket.cmdb_ci_raw ?? '–'}</Field>
        <Field label="Problem / caused by">{[ticket.problem_id, ticket.caused_by].filter(Boolean).join(' / ') || '–'}</Field>
      </SimpleGrid>

      <Card withBorder padding="sm" radius="md">
        <Text size="xs" fw={700} mb={6}>
          Category: ServiceNow vs AI
        </Text>
        <SimpleGrid cols={2} spacing="sm">
          <Field label="ServiceNow category">{ticket.sn_category ?? '–'}</Field>
          <Field label="AI app-owner category">
            <Group gap={6}>
              <span>
                {ticket.am_category ? humanize(ticket.am_category) : 'Not triaged'}
                {ticket.am_subcategory ? ` / ${humanize(ticket.am_subcategory)}` : ''}
              </span>
              <ProvenanceBadge
                runId={ticket.label_run_id}
                status={ticket.label_run_status}
                confidence={ticket.label_confidence}
              />
            </Group>
          </Field>
        </SimpleGrid>
      </Card>

      <div>
        <Text size="xs" fw={700} mb={4}>
          Description (scrubbed)
        </Text>
        <PlainText>{ticket.description}</PlainText>
      </div>
      {ticket.close_notes || ticket.close_code ? (
        <div>
          <Text size="xs" fw={700} mb={4}>
            Resolution {ticket.close_code ? `· ${ticket.close_code}` : ''}
          </Text>
          <PlainText>{ticket.close_notes}</PlainText>
        </div>
      ) : null}

      {Object.keys(ticket.export_fields).length > 0 ? (
        <>
          <Divider label={`All fields from your export (${Object.keys(ticket.export_fields).length})`} labelPosition="left" />
          <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="xs" verticalSpacing="xs">
            {Object.entries(ticket.export_fields).map(([name, value]) => (
              // Values are whatever the export held: shown as plain text, never parsed or rendered as markup.
              <Field key={name} label={name}>
                {value}
              </Field>
            ))}
          </SimpleGrid>
        </>
      ) : null}

      {ticket.labels.length > 0 ? (
        <>
          <Divider label="AI labels" labelPosition="left" />
          <Stack gap="xs">
            {ticket.labels.map((label) => (
              <Card key={`${label.stage}:${label.run_id}`} withBorder padding="sm" radius="md">
                <Group justify="space-between" gap={6}>
                  <Text size="sm" fw={600}>
                    {humanize(label.stage)}: {humanize(label.am_category)}
                    {label.am_subcategory ? ` / ${humanize(label.am_subcategory)}` : ''}
                  </Text>
                  <ProvenanceBadge runId={label.run_id} status={label.run_status} confidence={label.confidence} />
                </Group>
                <Text size="xs" c="dimmed">
                  {`symptom ${label.symptom_key ?? '–'} · confidence ${formatRatio(label.confidence)}`}
                  {label.misfiled_as ? ` · misfiled as ${label.misfiled_as}` : ''}
                </Text>
                {label.rationale ? <PlainText>{label.rationale}</PlainText> : null}
              </Card>
            ))}
          </Stack>
        </>
      ) : null}
    </Stack>
  );
}

export function TicketDrawer() {
  const [ticketId, setTicketId] = useTicketParam();
  const { filters } = useFilters();
  // Unreviewed AI labels only with "Include AI drafts" (the API also leaves out rejected runs and outdated labels).
  const detail = useApi(ticketId ? '/api/ops/tickets/{ticket_id}' : null, {
    params: { ticket_id: ticketId },
    query: filters.include_drafts ? { include_drafts: true } : {},
  });
  const ticket = detail.data && detail.data.ticket_id === ticketId ? detail.data : undefined;
  return (
    <Drawer
      opened={Boolean(ticketId)}
      onClose={() => setTicketId(null)}
      position="right"
      size="lg"
      title={<Text fw={700}>{ticket?.number ?? ticketId}</Text>}
    >
      {detail.error ? <ErrorState error={detail.error} onRetry={detail.reload} /> : null}
      {!ticket && detail.loading ? <Loader size="sm" /> : null}
      {ticket ? <Body ticket={ticket} /> : null}
    </Drawer>
  );
}
