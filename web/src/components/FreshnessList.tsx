import { Badge, Group, Stack, Text, Tooltip } from '@mantine/core';

import type { FreshnessRow } from '../api/types';
import { EmptyState } from './EmptyState';
import { formatDate, formatDateTime, humanize } from './format';

export interface FreshnessListProps {
  rows: readonly FreshnessRow[] | undefined;
  /** Reference date (YYYY-MM-DD) for the age of each source; defaults to today. */
  asOf?: string | null;
}

function ageDays(latest: string | null, reference: string | null | undefined): number | null {
  if (!latest) return null;
  const end = reference ? Date.parse(`${reference}T00:00:00Z`) : Date.now();
  const start = Date.parse(latest.length === 10 ? `${latest}T00:00:00Z` : latest);
  if (Number.isNaN(start) || Number.isNaN(end)) return null;
  return Math.max(0, Math.floor((end - start) / 86_400_000));
}

/** Last import per source mapping, coloured by how old the newest data is. */
export function FreshnessList({ rows, asOf }: FreshnessListProps) {
  if (!rows || rows.length === 0) return <EmptyState title="No imports yet" compact />;
  return (
    <Stack gap={6}>
      {rows.map((row) => {
        const age = ageDays(row.latest_as_of, asOf);
        const color = age === null ? 'gray' : age <= 7 ? 'teal' : age <= 31 ? 'yellow' : 'red';
        return (
          <Group key={row.mapping_name} justify="space-between" wrap="nowrap" gap="xs">
            <Tooltip label={row.mapping_name} withArrow>
              <Text size="sm" truncate>
                {humanize(row.mapping_name)}
              </Text>
            </Tooltip>
            <Group gap={6} wrap="nowrap">
              <Text size="xs" c="dimmed" visibleFrom="sm">
                {row.last_import ? `imported ${formatDateTime(row.last_import)}` : 'never imported'}
                {row.files !== null ? ` · ${row.files} files` : ''}
              </Text>
              <Badge size="sm" variant="light" color={color}>
                {row.latest_as_of ? `data ${formatDate(row.latest_as_of)}` : 'no data'}
              </Badge>
            </Group>
          </Group>
        );
      })}
    </Stack>
  );
}
