import { Badge, Card, Group, Text, Title } from '@mantine/core';
import type { ReactNode } from 'react';

export interface SectionCardProps {
  title: string;
  description?: ReactNode;
  count?: number | null;
  actions?: ReactNode;
  children: ReactNode;
  id?: string;
}

/** Titled card for tables and lists (charts use ChartCard). */
export function SectionCard({ title, description, count, actions, children, id }: SectionCardProps) {
  return (
    <Card withBorder radius="md" padding="md" id={id}>
      <Group justify="space-between" align="flex-start" mb="sm" gap="xs">
        <div>
          <Group gap={6}>
            <Title order={4} fz="md">
              {title}
            </Title>
            {count !== undefined && count !== null ? (
              <Badge size="sm" variant="light" color="gray">
                {count}
              </Badge>
            ) : null}
          </Group>
          {description ? (
            <Text size="xs" c="dimmed">
              {description}
            </Text>
          ) : null}
        </div>
        {actions}
      </Group>
      {children}
    </Card>
  );
}
