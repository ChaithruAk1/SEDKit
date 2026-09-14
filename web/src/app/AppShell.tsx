import { AppShell as MantineAppShell, Badge, Burger, Group, Stack, Text, Title } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { useEffect } from 'react';
import { Outlet, useMatches } from 'react-router';

import { FIXTURES_MODE } from '../api/client';
import { formatDate } from '../components/format';
import { DataClassBanner } from './DataClassBanner';
import { NavBar } from './NavBar';
import { ShellProvider, useShell } from './ShellContext';

interface RouteHandle {
  title?: string;
}

function usePageTitle(): string | null {
  const matches = useMatches();
  for (let i = matches.length - 1; i >= 0; i -= 1) {
    const handle = matches[i]?.handle as RouteHandle | undefined;
    if (handle?.title) return handle.title;
  }
  return null;
}

function ShellLayout() {
  const [opened, { toggle, close }] = useDisclosure(false);
  const { meta } = useShell();
  const title = usePageTitle();

  useEffect(() => {
    const dataClass = meta.data?.data_class === 'real' ? 'REAL' : meta.data ? 'SYNTHETIC' : null;
    document.title = ['SED', title, dataClass].filter(Boolean).join(' · ');
  }, [title, meta.data]);

  return (
    <MantineAppShell
      header={{ height: 26 + 50 }}
      navbar={{ width: 230, breakpoint: 'sm', collapsed: { mobile: !opened } }}
      padding="md"
    >
      <MantineAppShell.Header>
        <DataClassBanner />
        <Group h={50} px="md" justify="space-between" wrap="nowrap">
          <Group gap="sm" wrap="nowrap">
            <Burger opened={opened} onClick={toggle} hiddenFrom="sm" size="sm" aria-label="Toggle navigation" />
            <Title order={3} fz="lg" style={{ letterSpacing: 1 }}>
              SED
            </Title>
            {title ? (
              <Text c="dimmed" size="sm" truncate visibleFrom="xs">
                {title}
              </Text>
            ) : null}
          </Group>
          <Group gap="xs" wrap="nowrap">
            {FIXTURES_MODE ? (
              <Badge color="grape" variant="light">
                fixtures
              </Badge>
            ) : null}
            {meta.data?.as_of_default ? (
              <Text size="xs" c="dimmed" visibleFrom="sm">
                data as of {formatDate(meta.data.as_of_default)}
              </Text>
            ) : null}
          </Group>
        </Group>
      </MantineAppShell.Header>

      <MantineAppShell.Navbar>
        <NavBar onNavigate={close} />
        <Stack gap={0} p="xs" style={{ borderTop: '1px solid var(--app-shell-border-color)' }}>
          <Text size="xs" c="dimmed">
            {meta.data ? `SED ${meta.data.sed_version} · schema v${meta.data.schema_version}` : 'SED'}
          </Text>
          {meta.data ? (
            <Text size="xs" c="dimmed">
              {`${meta.data.reporting_tz} · ${meta.data.base_currency}`}
            </Text>
          ) : null}
        </Stack>
      </MantineAppShell.Navbar>

      <MantineAppShell.Main>
        <Outlet />
      </MantineAppShell.Main>
    </MantineAppShell>
  );
}

/** Root layout: data-class banner, header, server-driven nav and the routed page. */
export function AppShell() {
  return (
    <ShellProvider>
      <ShellLayout />
    </ShellProvider>
  );
}
