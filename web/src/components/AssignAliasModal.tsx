import { Alert, Badge, Button, Group, Modal, Select, Stack, Text } from '@mantine/core';
import { useDebouncedValue } from '@mantine/hooks';
import { IconCheck } from '@tabler/icons-react';
import { useEffect, useMemo, useState } from 'react';

import type { Schema } from '../api/types';
import { useApi, useApiPost } from '../api/useApi';
import { ErrorState } from './ErrorState';
import { formatInt, humanize } from './format';

type UnmappedRow = Schema<'UnmappedRow'>;
type AliasOut = Schema<'AliasOut'>;
type AliasTarget = Schema<'AliasTarget'>;

function targetLabel(target: AliasTarget): string {
  return target.id === target.name ? target.name : `${target.name} (${target.id})`;
}

export interface AssignAliasModalProps {
  /** The unmapped value to alias; null closes the modal. */
  row: UnmappedRow | null;
  onClose: () => void;
  /** Called after a successful POST /api/aliases (the caller refetches its lists). */
  onAssigned: (result: AliasOut) => void;
}

/**
 * Map an unmapped raw value to a known entity: pick a target from GET /api/alias-targets, then POST /api/aliases
 * (token-protected). The server records a manual alias and re-links existing rows.
 */
export function AssignAliasModal({ row, onClose, onAssigned }: AssignAliasModalProps) {
  const [search, setSearch] = useState('');
  const [debounced] = useDebouncedValue(search, 250);
  const [selected, setSelected] = useState<AliasTarget | null>(null);
  const post = useApiPost('/api/aliases');
  const { reset } = post;

  useEffect(() => {
    setSearch(row?.suggestion ?? '');
    setSelected(null);
    reset();
  }, [row, reset]);

  // While the input shows the selected option's label, list all targets instead of searching for that label.
  const typed = debounced.trim();
  const q = selected && typed === targetLabel(selected) ? null : typed || null;
  const targets = useApi(row ? '/api/alias-targets' : null, { query: { kind: row?.kind ?? '', q, limit: 50 } });

  useEffect(() => {
    if (selected || !row?.suggestion || !targets.data) return;
    const wanted = row.suggestion.toLowerCase();
    const match = targets.data.items.find((t) => t.name.toLowerCase() === wanted || t.id.toLowerCase() === wanted);
    if (match) setSelected(match);
  }, [targets.data, row, selected]);

  const options = useMemo(() => {
    const items = [...(targets.data?.items ?? [])];
    if (selected && !items.some((t) => t.id === selected.id)) items.unshift(selected);
    return items.map((t) => ({ value: t.id, label: targetLabel(t) }));
  }, [targets.data, selected]);

  const onSelect = (value: string | null) => {
    const found = value === null ? null : (targets.data?.items.find((t) => t.id === value) ?? (selected?.id === value ? selected : null));
    setSelected(found);
  };

  const done = post.data;

  const submit = async () => {
    if (!row || !selected) return;
    try {
      const result = await post.run({ kind: row.kind, raw_value: row.raw_value, target: selected.id });
      onAssigned(result);
    } catch {
      // The error is shown from post.error.
    }
  };

  return (
    <Modal opened={row !== null} onClose={onClose} title="Assign alias" size="lg" centered>
      {row ? (
        <Stack gap="md">
          <Stack gap={4}>
            <Group gap="xs">
              <Badge variant="light">{humanize(row.kind)}</Badge>
              <Text size="sm" c="dimmed">
                seen {formatInt(row.occurrences)} times
              </Text>
            </Group>
            <Text size="sm">
              Raw value: <Text span fw={700} ff="monospace">{row.raw_value}</Text>
            </Text>
            {row.suggestion ? (
              <Text size="xs" c="dimmed">
                Suggestion: {row.suggestion}
                {row.score !== null ? ` (score ${row.score.toFixed(2)})` : ''}
              </Text>
            ) : null}
          </Stack>

          {done ? (
            <Alert color="teal" icon={<IconCheck size={18} />} title="Alias saved">
              <Text size="sm">
                {done.raw_value} now maps to {done.target_id}. Re-linked rows:{' '}
                {Object.entries(done.reresolved).length
                  ? Object.entries(done.reresolved)
                      .map(([table, count]) => `${table} ${formatInt(count)}`)
                      .join(', ')
                  : 'none'}
                .
              </Text>
            </Alert>
          ) : (
            <>
              <Select
                label="Target"
                description="Search by name or id"
                placeholder="Type to search"
                searchable
                data={options}
                value={selected?.id ?? null}
                onChange={onSelect}
                searchValue={search}
                onSearchChange={setSearch}
                nothingFoundMessage={targets.loading ? 'Searching…' : 'No matching targets'}
                filter={({ options: all }) => all}
                maxDropdownHeight={260}
                data-autofocus
              />
              <ErrorState error={targets.error} compact />
              <ErrorState error={post.error} title={post.error?.kind === 'busy' ? 'Database busy, try again' : undefined} />
            </>
          )}

          <Group justify="flex-end">
            <Button variant="default" onClick={onClose}>
              {done ? 'Close' : 'Cancel'}
            </Button>
            {!done ? (
              <Button onClick={submit} loading={post.pending} disabled={!selected}>
                Assign and re-link
              </Button>
            ) : null}
          </Group>
        </Stack>
      ) : null}
    </Modal>
  );
}
