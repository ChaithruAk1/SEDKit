import { Alert, NavLink, ScrollArea, Skeleton, Stack, Text } from '@mantine/core';
import { Link, matchPath, useLocation } from 'react-router';

import { filterSearch } from '../hooks/useFilters';
import { NavIcon } from './navIcons';
import { useShell } from './ShellContext';

/**
 * Navigation from GET /api/nav. Only items whose path matches a registered page are shown (a module enabled on the
 * server without web pages, or pages for a disabled module, never produce dead links). Links keep the global filters.
 */
export function NavBar({ onNavigate }: { onNavigate?: () => void }) {
  const { nav, navState, navFallback } = useShell();
  const location = useLocation();
  const search = filterSearch(new URLSearchParams(location.search));

  const activeId = [...nav]
    .filter((item) => matchPath({ path: item.path, end: false }, location.pathname))
    .sort((a, b) => b.path.length - a.path.length)[0]?.id;

  return (
    <ScrollArea type="auto" style={{ flex: 1 }}>
      <Stack gap={2} p="xs">
        {navState.loading && nav.length === 0
          ? [0, 1, 2, 3, 4].map((i) => <Skeleton key={i} height={34} radius="sm" />)
          : null}
        {nav.map((item) => (
          <NavLink
            key={item.id}
            component={Link}
            to={`${item.path}${search}`}
            label={item.label}
            leftSection={<NavIcon name={item.icon} />}
            active={item.id === activeId}
            onClick={onNavigate}
            data-nav-id={item.id}
          />
        ))}
        {navFallback ? (
          <Alert color="yellow" variant="light" p="xs" mt="sm">
            <Text size="xs">Navigation service unavailable ({navState.error?.message}); showing local pages.</Text>
          </Alert>
        ) : null}
        {!navState.loading && !navFallback && nav.length === 0 ? (
          <Text size="xs" c="dimmed" p="xs">
            No pages available.
          </Text>
        ) : null}
      </Stack>
    </ScrollArea>
  );
}
