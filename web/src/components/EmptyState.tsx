import { EmptyState as MantineEmptyState } from '@mantine/core';
import { IconInbox } from '@tabler/icons-react';
import type { ReactNode } from 'react';

export interface EmptyStateProps {
  title?: string;
  description?: ReactNode;
  icon?: ReactNode;
  action?: ReactNode;
  compact?: boolean;
}

/** Shown when a query returns no rows (never for loading or errors). */
export function EmptyState({ title = 'Nothing to show', description, icon, action, compact = false }: EmptyStateProps) {
  return (
    <MantineEmptyState
      size={compact ? 'xs' : 'sm'}
      py={compact ? 'sm' : 'xl'}
      title={title}
      description={description}
      icon={icon ?? <IconInbox size={compact ? 20 : 28} stroke={1.5} />}
    >
      {action ? <MantineEmptyState.Actions>{action}</MantineEmptyState.Actions> : null}
    </MantineEmptyState>
  );
}
