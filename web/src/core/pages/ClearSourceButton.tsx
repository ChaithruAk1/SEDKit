/**
 * Clear what one source imported (`POST /api/data/clear`), from the row that source already occupies.
 *
 * It sits beside "Pull now" because that is where the reader already thinks about a source: pull it, or empty it and
 * pull again. Deleting cannot be undone, so it names what will go and asks first.
 *
 * Removed: the rows that source imported. Kept: column mappings, saved layouts, the scrambling key and branding.
 */
import { Alert, Button, Group, Modal, Stack, Text, Tooltip } from '@mantine/core';
import { IconAlertTriangle, IconTrash } from '@tabler/icons-react';
import { useState } from 'react';

import { apiPost } from '../../api/client';
import type { Schema } from '../../api/types';
import { ErrorState } from '../../components/ErrorState';
import { formatInt } from '../../components/format';
import { toApiError } from '../../api/useApi';
import { ApiError } from '../../api/client';

type SourceRow = Schema<'DataSourceRow'>;

export function ClearSourceButton({ source, onCleared }: { source: SourceRow | undefined; onCleared: () => void }) {
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | undefined>(undefined);

  if (!source) return null;
  const rows = source.rows;

  const confirm = async () => {
    setBusy(true);
    setError(undefined);
    try {
      await apiPost('/api/data/clear', { source: source.key });
      setAsking(false);
      onCleared();
    } catch (caught) {
      setError(toApiError(caught));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <Tooltip label={rows ? `Delete ${formatInt(rows)} imported rows` : 'Nothing imported from this source'} withArrow>
        <Button
          size="compact-xs"
          variant="light"
          color="red"
          leftSection={<IconTrash size={12} />}
          disabled={rows === 0}
          onClick={() => setAsking(true)}
        >
          {rows ? `Clear ${formatInt(rows)}` : 'Clear'}
        </Button>
      </Tooltip>

      <Modal opened={asking} onClose={() => setAsking(false)} title={`Clear ${source.label} data?`} centered>
        <Stack gap="sm">
          <Alert color="red" variant="light" icon={<IconAlertTriangle size={16} />}>
            {`This deletes ${formatInt(rows)} rows and cannot be undone. Import the source again to get them back.`}
          </Alert>
          <Text size="sm">What goes:</Text>
          <Stack gap={2}>
            {Object.entries(source.tables)
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
          <ErrorState error={error} compact />
          <Group justify="flex-end" gap="xs">
            <Button size="xs" variant="default" onClick={() => setAsking(false)} disabled={busy}>
              Cancel
            </Button>
            <Button size="xs" color="red" loading={busy} onClick={() => void confirm()}>
              {`Clear ${source.label} data`}
            </Button>
          </Group>
        </Stack>
      </Modal>
    </>
  );
}
