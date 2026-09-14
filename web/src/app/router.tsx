import { type RouteObject, createHashRouter } from 'react-router';

import { CORE_ROUTES } from '../core/routes';
import { MODULES } from '../modules/registry';
import { AppShell } from './AppShell';
import { HomeRedirect } from './HomeRedirect';
import { NotFound } from './NotFound';
import { PageFrame, type PageRoute, RouteError } from './PageFrame';

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
        { index: true, element: <HomeRedirect /> },
        ...pages.map(toRouteObject),
        { path: '*', element: <NotFound />, handle: { title: 'Not found' } },
      ],
    },
  ];
}

export function createAppRouter() {
  return createHashRouter(buildRoutes());
}
