import { Alert, Button, Code, Group, Stack, Text } from '@mantine/core';
import { IconAlertTriangle, IconPlugConnectedX, IconTool } from '@tabler/icons-react';

import type { ApiError } from '../api/client';

export interface ErrorStateProps {
  error: ApiError | Error | undefined;
  title?: string;
  onRetry?: () => void;
  compact?: boolean;
}

function describe(error: ApiError | Error): { title: string; hint: string | null; color: string; kind: string | null } {
  const kind = 'kind' in error ? error.kind : null;
  switch (kind) {
    case 'network':
      return { title: 'API not reachable', hint: 'Start the dashboard with `sed serve` (or `npm run dev:fixtures`).', color: 'gray', kind };
    case 'not_implemented':
      return { title: 'Not available yet', hint: 'This API route is not implemented in this build.', color: 'gray', kind };
    case 'forbidden':
      return { title: 'Request refused', hint: 'The launch token is missing or stale: reload the page served by `sed serve`.', color: 'red', kind };
    case 'unauthenticated':
      return { title: 'Signed out', hint: 'Your sign-in has ended. Sign in again to continue.', color: 'yellow', kind };
    case 'busy': {
      const seconds = 'retryAfter' in error && error.retryAfter ? ` in ${error.retryAfter} s` : '';
      return { title: 'Database busy', hint: `Another job is writing. Retry${seconds}.`, color: 'yellow', kind };
    }
    case 'not_found':
      return { title: 'Not found', hint: null, color: 'orange', kind };
    case 'validation':
      return { title: 'Invalid request', hint: null, color: 'orange', kind };
    case 'precondition':
      return { title: 'Precondition failed', hint: null, color: 'orange', kind };
    default:
      return { title: 'Something went wrong', hint: null, color: 'red', kind };
  }
}

function detailsText(details: unknown): string | null {
  if (details === null || details === undefined) return null;
  if (typeof details === 'string') return details;
  try {
    return JSON.stringify(details, null, 2);
  } catch {
    return null;
  }
}

/** API error display: the envelope kind picks the wording; message and details are shown as plain text. */
export function ErrorState({ error, title, onRetry, compact = false }: ErrorStateProps) {
  if (!error) return null;
  const info = describe(error);
  const details = 'details' in error ? detailsText(error.details) : null;
  const Icon = info.kind === 'network' ? IconPlugConnectedX : info.kind === 'not_implemented' ? IconTool : IconAlertTriangle;
  return (
    <Alert
      color={info.color}
      variant="light"
      icon={<Icon size={18} />}
      title={title ?? info.title}
      py={compact ? 'xs' : undefined}
      role="alert"
    >
      <Stack gap={6}>
        <Text size="sm">{error.message}</Text>
        {info.hint ? (
          <Text size="xs" c="dimmed">
            {info.hint}
          </Text>
        ) : null}
        {details && !compact ? (
          <Code block fz="xs" mah={160} style={{ overflow: 'auto' }}>
            {details}
          </Code>
        ) : null}
        {onRetry ? (
          <Group>
            <Button size="xs" variant="default" onClick={onRetry}>
              Retry
            </Button>
          </Group>
        ) : null}
      </Stack>
    </Alert>
  );
}
