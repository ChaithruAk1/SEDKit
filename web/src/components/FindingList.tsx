import { Anchor, Badge, Card, Group, Spoiler, Stack, Text } from '@mantine/core';
import { Link } from 'react-router';

import type { FindingOut } from '../api/types';
import { EmptyState } from './EmptyState';
import { humanize } from './format';
import { Markdown } from './Markdown';
import { ProvenanceBadge } from './ProvenanceBadge';
import { SystemDetectedBadge } from './SystemDetectedBadge';

const SEVERITY_COLOR: Record<string, string> = { critical: 'red', high: 'red', medium: 'orange', low: 'yellow', info: 'gray' };

export interface FindingListProps {
  findings: readonly FindingOut[] | undefined;
  emptyText?: string;
  /** In-app link for a finding's subject (modules know their routes), or null. */
  subjectHref?: (finding: FindingOut) => string | null;
  compact?: boolean;
}

function evidenceText(value: unknown): string {
  if (typeof value === 'number') return value.toLocaleString('en-GB');
  if (value === null || value === undefined) return '–';
  return typeof value === 'string' ? value : JSON.stringify(value);
}

/** Published findings: rule findings carry the System-detected badge, AI findings a ProvenanceBadge. */
export function FindingList({ findings, emptyText = 'No published findings', subjectHref, compact = false }: FindingListProps) {
  if (!findings || findings.length === 0) return <EmptyState title={emptyText} compact />;
  return (
    <Stack gap="sm">
      {findings.map((finding) => {
        const facts = Object.fromEntries(finding.evidence.map((e) => [e.fact_key, e.value]));
        const href = subjectHref?.(finding) ?? null;
        const draft = finding.origin === 'ai' && !['approved', 'update_pending'].includes(finding.status);
        return (
          <Card key={finding.finding_id} withBorder radius="md" padding={compact ? 'sm' : 'md'} data-finding={finding.finding_id}>
            <Group justify="space-between" align="flex-start" gap="xs" wrap="nowrap">
              <Stack gap={4} style={{ minWidth: 0 }}>
                <Group gap={6}>
                  <Badge size="xs" color={SEVERITY_COLOR[finding.severity ?? ''] ?? 'gray'} variant="filled">
                    {finding.severity ?? 'n/a'}
                  </Badge>
                  <Badge size="xs" variant="default">
                    {humanize(finding.kind)}
                  </Badge>
                  {finding.system_detected || finding.origin === 'rule' ? (
                    <SystemDetectedBadge />
                  ) : (
                    <ProvenanceBadge
                      runId={finding.run_id}
                      status={finding.status}
                      reviewedBy={finding.reviewed_by}
                      reviewedAt={finding.reviewed_at}
                    />
                  )}
                  {draft ? (
                    <Badge size="xs" color="orange" variant="outline">
                      {finding.status}
                    </Badge>
                  ) : null}
                </Group>
                <Text fw={600} size="sm">
                  {href ? (
                    <Anchor component={Link} to={href} inherit>
                      {finding.title}
                    </Anchor>
                  ) : (
                    finding.title
                  )}
                </Text>
              </Stack>
            </Group>
            {finding.body_md && !compact ? (
              <Spoiler maxHeight={96} showLabel="Show more" hideLabel="Show less" mt={6}>
                <Markdown facts={facts}>{finding.body_md}</Markdown>
              </Spoiler>
            ) : null}
            {finding.evidence.length > 0 ? (
              <Group gap={6} mt={6}>
                {finding.evidence.map((e) => (
                  <Badge key={e.fact_key} size="xs" variant="light" color="gray" tt="none">
                    {e.fact_key}: {evidenceText(e.value)}
                  </Badge>
                ))}
              </Group>
            ) : null}
          </Card>
        );
      })}
    </Stack>
  );
}
