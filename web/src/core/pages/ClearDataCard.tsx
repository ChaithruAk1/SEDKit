/**
 * Clear what one source imported (`GET /api/data/sources`, `POST /api/data/clear`).
 *
 * Reloading a source is ordinary: an export was wrong, a mapping changed, a system was reconnected. This removes the
 * rows that source imported and nothing else — column mappings, saved layouts, the scrambling key and branding all
 * stay. Deleting data cannot be undone, so each one asks first and names what will go.
 */
import { Alert, Button, Group, Modal, Stack, Text } from '@mantine/core';
import { IconAlertTriangle, IconTrash } from '@tabler/icons-react';
import { useState } from 'react';

import { apiPost } from '../../api/client';
import type { Schema } from '../../api/types';
import { useApi } from '../../api/useApi';
import { ErrorState } from '../../components/ErrorState';
import { formatInt } from '../../components/format';
import { SectionCard } from '../../components/SectionCard';

type SourceRow = Schema<'DataSourceRow'>;

export function ClearDataCard({ onCleared }: { onCleared: () => void }) {
  const sources = useApi('/api/data/sources');
  const [asking, setAsking] = useState<SourceRow | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);

  const confirm = async () => {
    if (!asking) return;
    setBusy(true);
    try {
      const result = await apiPost('/api/data/clear', { source: asking.key });
      setDone(`Cleared ${formatInt(result.rows)} rows of ${result.label} data.`);
      setAsking(null);
      sources.reload();
      onCleared();
    } finally {
      setBusy(false);
    }
  };

  return (
    <SectionCard
      title="Clear imported data"
      description="Remove what one source imported and load it again. Mappings, layouts, the scrambling key and branding are untouched."
    >
      <Stack gap="sm">
        {sources.error ? <ErrorState error={sources.error} onRetry={sources.reload} compact /> : null}
        {done ? (
          <Alert color="teal" variant="light">
            {done}
          </Alert>
        ) : null}
        {(sources.data?.items ?? []).map((source) => (
          <Group key={source.key} justify="space-between" wrap="nowrap" align="flex-start" gap="sm">
            <div>
              <Text size="sm" fw={600}>
                {source.label}
              </Text>
              <Text size="xs" c="dimmed">
                {source.description}
              </Text>
            </div>
            <Group gap="xs" wrap="nowrap">
              <Text size="sm" fw={600} style={{ fontVariantNumeric: 'tabular-nums' }}>
                {formatInt(source.rows)}
              </Text>
              <Button
                size="xs"
                variant="light"
                color="red"
                leftSection={<IconTrash size={14} />}
                disabled={source.rows === 0 || busy}
                onClick={() => setAsking(source)}
              >
                Clear
              </Button>
            </Group>
          </Group>
        ))}
      </Stack>

      <Modal opened={asking !== null} onClose={() => setAsking(null)} title={`Clear ${asking?.label ?? ''} data?`} centered>
        <Stack gap="sm">
          <Alert color="red" variant="light" icon={<IconAlertTriangle size={16} />}>
            This deletes {formatInt(asking?.rows ?? 0)} rows and cannot be undone. You would import the source again to
            get them back.
          </Alert>
          <Text size="sm">What goes:</Text>
          <Stack gap={2}>
            {Object.entries(asking?.tables ?? {})
              .filter(([, n]) => n > 0)
              .map(([table, n]) => (
                <Text key={table} size="xs" c="dimmed">
                  {`${table}: ${formatInt(n)} rows`}
                </Text>
              ))}
          </Stack>
          <Text size="xs" c="dimmed">
            Your column mappings, saved layouts, scrambling key and branding are not touched.
          </Text>
          <Group justify="flex-end" gap="xs">
            <Button size="xs" variant="default" onClick={() => setAsking(null)} disabled={busy}>
              Cancel
            </Button>
            <Button size="xs" color="red" loading={busy} onClick={() => void confirm()}>
              {`Clear ${asking?.label ?? ''} data`}
            </Button>
          </Group>
        </Stack>
      </Modal>
    </SectionCard>
  );
}
