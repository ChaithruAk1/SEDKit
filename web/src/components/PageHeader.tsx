import { Group, Stack, Text, Title } from '@mantine/core';
import type { ReactNode } from 'react';

export interface PageHeaderProps {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  badges?: ReactNode;
}

export function PageHeader({ title, description, actions, badges }: PageHeaderProps) {
  return (
    <Group justify="space-between" align="flex-end" mb="md" gap="sm">
      <Stack gap={2} style={{ minWidth: 0 }}>
        <Group gap="xs">
          <Title order={2} fz="h3">
            {title}
          </Title>
          {badges}
        </Group>
        {description ? (
          <Text size="sm" c="dimmed">
            {description}
          </Text>
        ) : null}
      </Stack>
      {actions}
    </Group>
  );
}
