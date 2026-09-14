import { Center, Loader } from '@mantine/core';
import { Navigate, useLocation } from 'react-router';

import { filterSearch } from '../hooks/useFilters';
import { useShell } from './ShellContext';

/** `#/` goes to the first server nav item that has a page (lowest `order`). */
export function HomeRedirect() {
  const { nav, navState } = useShell();
  const location = useLocation();
  const first = nav[0];
  if (first) return <Navigate to={`${first.path}${filterSearch(new URLSearchParams(location.search))}`} replace />;
  if (navState.loading) {
    return (
      <Center py="xl">
        <Loader size="sm" />
      </Center>
    );
  }
  return <Navigate to="/data" replace />;
}
