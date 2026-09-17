import { type RouteObject, createHashRouter } from 'react-router';

import { CORE_ROUTES } from '../core/routes';
import { MODULES } from '../modules/registry';
import { AppShell } from './AppShell';
import { NotFound } from './NotFound';
import { PageFrame, type PageRoute, RouteError } from './PageFrame';

/** The front screen at `#/` (core, not a module page and not a nav item: the shell links to it). */
const HOME_ROUTE: PageRoute = { path: '', title: 'Home', filters: [], load: () => import('../core/pages/HomePage') };

function toRouteObject(route: PageRoute): RouteObject {
  return {
    path: route.path,
    handle: { title: route.title, filters: route.filters },
    element: <PageFrame route={route} />,
  };
}

/** All pages: registered module routes (`#/<key>/...`) plus core pages, under the shell. Hash routing only. */
export function buildRoutes(): RouteObject[] {
  const pages: PageRoute[] = [...MODULES.flatMap((module) => module.routes), ...CORE_ROUTES];
  return [
    {
      path: '/',
      element: <AppShell />,
      errorElement: <RouteError />,
      children: [
        { index: true, handle: { title: HOME_ROUTE.title, filters: [] }, element: <PageFrame route={HOME_ROUTE} /> },
        ...pages.map(toRouteObject),
        { path: '*', element: <NotFound />, handle: { title: 'Not found' } },
      ],
    },
  ];
}

export function createAppRouter() {
  return createHashRouter(buildRoutes());
}
