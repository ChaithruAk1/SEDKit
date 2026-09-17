/** Platform pages (not a module): `#/review`, `#/runs`, `#/runs/:runId` and `#/data`. Nav comes from GET /api/nav. */
import type { WebRoute } from '../modules/types';

export const CORE_ROUTES = [
  {
    path: 'review',
    title: 'Review',
    filters: [],
    load: () => import('./pages/ReviewPage'),
  },
  {
    path: 'runs',
    title: 'AI runs',
    filters: [],
    load: () => import('./pages/RunsPage'),
  },
  {
    path: 'runs/:runId',
    title: 'AI run',
    filters: [],
    load: () => import('./pages/RunDetailPage'),
  },
  {
    path: 'data',
    title: 'Data',
    filters: [],
    load: () => import('./pages/DataPage'),
  },
] as const satisfies readonly WebRoute<'review' | 'runs' | 'data'>[];
