/** Ops pages (`#/ops/...`), each loaded lazily as its own chunk. Nav entries come from the ops manifest via /api/nav. */
import type { WebRoute } from '../types';

export const OPS_ROUTES = [
  {
    path: 'ops',
    title: 'Overview',
    filters: ['app', 'family', 'vendor', 'group', 'period', 'as_of', 'include_drafts'],
    load: () => import('./pages/OverviewPage'),
  },
  {
    path: 'ops/attention',
    title: 'Needs attention',
    filters: ['app', 'family', 'vendor', 'group', 'as_of'],
    load: () => import('./pages/AttentionPage'),
  },
  {
    path: 'ops/tickets',
    title: 'Tickets',
    filters: ['app', 'family', 'vendor', 'group', 'as_of', 'include_drafts'],
    load: () => import('./pages/TicketsPage'),
  },
  {
    path: 'ops/apps',
    title: 'App 360',
    filters: ['app', 'family', 'vendor', 'as_of'],
    load: () => import('./pages/AppsPage'),
  },
  {
    path: 'ops/apps/:appId',
    title: 'App 360',
    filters: ['as_of', 'include_drafts'], // KPIs use fixed windows (last 3 months, YTD, 12 months): no period filter
    load: () => import('./pages/App360Page'),
  },
  {
    path: 'ops/costs',
    title: 'Costs & Contracts',
    filters: ['app', 'family', 'vendor', 'as_of', 'include_drafts'],
    load: () => import('./pages/CostsPage'),
  },
] as const satisfies readonly WebRoute<'ops'>[];
