import { Alert, Button, Center, Code, Loader, Stack, Text } from '@mantine/core';
import { IconBug } from '@tabler/icons-react';
import {
  Component,
  type ComponentType,
  type ErrorInfo,
  type LazyExoticComponent,
  type ReactNode,
  Suspense,
  lazy,
  useEffect,
} from 'react';
import { isRouteErrorResponse, useLocation, useRouteError } from 'react-router';

import type { FilterKey } from '../hooks/useFilters';
import { FilterBar } from './FilterBar';

export interface PageRoute {
  path: string;
  title: string;
  filters: readonly FilterKey[];
  load: () => Promise<{ default: ComponentType }>;
}

const lazyPages = new Map<string, LazyExoticComponent<ComponentType>>();

function lazyPage(route: PageRoute): LazyExoticComponent<ComponentType> {
  let page = lazyPages.get(route.path);
  if (!page) {
    page = lazy(route.load);
    lazyPages.set(route.path, page);
  }
  return page;
}

class PageErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  override state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  override componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('[sed] page crashed', error, info.componentStack);
  }

  override render() {
    if (this.state.error) {
      return (
        <Alert color="red" icon={<IconBug size={18} />} title="This page failed to render">
          <Stack gap="xs">
            <Code block fz="xs">
              {this.state.error.message}
            </Code>
            <div>
              <Button size="xs" variant="default" onClick={() => this.setState({ error: null })}>
                Try again
              </Button>
            </div>
          </Stack>
        </Alert>
      );
    }
    return this.props.children;
  }
}

/** Filter bar, error boundary and Suspense around one lazily loaded page. */
export function PageFrame({ route }: { route: PageRoute }) {
  const location = useLocation();
  const Page = lazyPage(route);
  useEffect(() => {
    window.scrollTo(0, 0);
  }, [location.pathname]);
  return (
    <>
      <FilterBar keys={route.filters} />
      <PageErrorBoundary key={location.pathname}>
        <Suspense
          fallback={
            <Center py="xl">
              <Loader size="sm" />
            </Center>
          }
        >
          <Page />
        </Suspense>
      </PageErrorBoundary>
    </>
  );
}

/** Router-level error element (for errors outside a page, e.g. a failed lazy chunk). */
export function RouteError() {
  const error = useRouteError();
  const message = isRouteErrorResponse(error)
    ? `${error.status} ${error.statusText}`
    : error instanceof Error
      ? error.message
      : String(error);
  return (
    <Center p="xl">
      <Alert color="red" icon={<IconBug size={18} />} title="The dashboard hit an error" maw={640}>
        <Text size="sm">{message}</Text>
        <Button mt="sm" size="xs" variant="default" onClick={() => window.location.reload()}>
          Reload
        </Button>
      </Alert>
    </Center>
  );
}
