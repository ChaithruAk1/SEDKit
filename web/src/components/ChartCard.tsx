import { Card, Group, Loader, Stack, Text, Title } from '@mantine/core';
import type { ReactElement, ReactNode } from 'react';
import { ResponsiveContainer } from 'recharts';

import type { ApiError } from '../api/client';
import { EmptyState } from './EmptyState';
import { ErrorState } from './ErrorState';

export interface ChartCardProps {
  title: string;
  description?: ReactNode;
  /** Controls on the right of the title (granularity switch, selects...). */
  actions?: ReactNode;
  loading?: boolean;
  error?: ApiError;
  onRetry?: () => void;
  /** True when there is nothing to plot. */
  empty?: boolean;
  emptyText?: string;
  height?: number;
  /** A single Recharts chart; it is wrapped in a ResponsiveContainer. */
  children: ReactElement;
  footer?: ReactNode;
}

export const CHART_COLORS = ['#1c7ed6', '#f76707', '#2f9e44', '#ae3ec9', '#e03131', '#0c8599', '#f59f00', '#5c7cfa'];

export function ChartCard({
  title,
  description,
  actions,
  loading = false,
  error,
  onRetry,
  empty = false,
  emptyText = 'No data for these filters',
  height = 260,
  children,
  footer,
}: ChartCardProps) {
  let body: ReactNode;
  if (error) body = <ErrorState error={error} onRetry={onRetry} compact />;
  else if (loading && empty) {
    body = (
      <Stack h={height} align="center" justify="center">
        <Loader size="sm" />
      </Stack>
    );
  } else if (empty) body = <EmptyState title={emptyText} compact />;
  else {
    body = (
      <div style={{ width: '100%', height, opacity: loading ? 0.55 : 1, transition: 'opacity 120ms' }}>
        <ResponsiveContainer width="100%" height="100%">
          {children}
        </ResponsiveContainer>
      </div>
    );
  }
  return (
    <Card withBorder radius="md" padding="md">
      <Group justify="space-between" align="flex-start" mb="sm" gap="xs">
        <div>
          <Title order={4} fz="md">
            {title}
          </Title>
          {description ? (
            <Text size="xs" c="dimmed">
              {description}
            </Text>
          ) : null}
        </div>
        {actions}
      </Group>
      {body}
      {footer}
    </Card>
  );
}
