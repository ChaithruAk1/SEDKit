import { Box, Group, Text } from '@mantine/core';

import { FIXTURES_MODE } from '../api/client';
import { useShell } from './ShellContext';

/**
 * Always-visible data classification strip from /api/meta: SYNTHETIC (amber) or REAL (red). While meta is unknown the
 * strip says so instead of guessing, so real data is never presented as synthetic.
 */
export function DataClassBanner() {
  const { meta } = useShell();
  const dataClass = meta.data?.data_class?.toLowerCase() ?? null;
  const known = dataClass === 'synthetic' || dataClass === 'real';

  let label: string;
  let background: string;
  let color = 'white';
  if (dataClass === 'real') {
    label = 'REAL DATA · confidential · do not share screenshots or exports';
    background = 'var(--mantine-color-red-8)';
  } else if (dataClass === 'synthetic') {
    label = 'SYNTHETIC DATA · fictional names and numbers';
    background = 'var(--mantine-color-yellow-5)';
    color = 'var(--mantine-color-dark-9)';
  } else if (meta.loading && !meta.error) {
    label = 'Loading data classification…';
    background = 'var(--mantine-color-gray-6)';
  } else {
    label = `DATA CLASS UNKNOWN${dataClass ? ` (${dataClass})` : ''} · treat as confidential`;
    background = 'var(--mantine-color-orange-8)';
  }

  return (
    <Box
      role="status"
      aria-live="polite"
      data-data-class={known ? dataClass : 'unknown'}
      style={{ background, color, height: 26, display: 'flex', alignItems: 'center' }}
      px="md"
    >
      <Group justify="space-between" w="100%" gap="xs" wrap="nowrap">
        <Text size="xs" fw={800} truncate inherit style={{ letterSpacing: 0.4 }}>
          {label}
        </Text>
        <Text size="xs" fw={600} inherit visibleFrom="sm" style={{ whiteSpace: 'nowrap' }}>
          {FIXTURES_MODE ? 'FIXTURES MODE (no API) · ' : ''}
          {meta.data ? `profile ${meta.data.profile} · PII ${meta.data.pii_mode}` : ''}
        </Text>
      </Group>
    </Box>
  );
}
