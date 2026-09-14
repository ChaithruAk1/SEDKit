/** Platform pages (not a module): `#/data`. Nav for them also comes from GET /api/nav (`core.data`). */
import type { WebRoute } from '../modules/types';

export const CORE_ROUTES = [
  {
    path: 'data',
    title: 'Data',
    filters: [],
    load: () => import('./pages/DataPage'),
  },
] as const satisfies readonly WebRoute<'data'>[];
