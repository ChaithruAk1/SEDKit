import { Anchor, Button, Stack, Text, Title } from '@mantine/core';
import { IconMapOff } from '@tabler/icons-react';
import { Link, useLocation } from 'react-router';

import { useShell } from './ShellContext';

export function NotFound() {
  const location = useLocation();
  const { nav } = useShell();
  return (
    <Stack align="center" gap="sm" py={48}>
      <IconMapOff size={40} stroke={1.4} />
      <Title order={2}>Page not found</Title>
      <Text c="dimmed" size="sm">
        No page is registered for <code>#{location.pathname}</code>.
      </Text>
      {nav.length > 0 ? (
        <Text size="sm">
          Available:{' '}
          {nav.map((item, index) => (
            <span key={item.id}>
              {index > 0 ? ', ' : ''}
              <Anchor component={Link} to={item.path}>
                {item.label}
              </Anchor>
            </span>
          ))}
        </Text>
      ) : null}
      <Button component={Link} to="/" variant="light">
        Go to the start page
      </Button>
    </Stack>
  );
}
