/** Delivery web module (module #3): manifest discovered by src/modules/registry.ts. */
import { apiGet } from '../../api/client';
import type { WebModule } from '../types';
import { DELIVERY_ROUTES } from './routes';

const PER_GROUP = 5;

const delivery = {
  key: 'delivery',
  title: 'Delivery management',
  routes: DELIVERY_ROUTES,
  async search(query, signal) {
    const needle = query.toLowerCase();
    const portfolio = await apiGet('/api/delivery/portfolio', undefined, signal);
    return portfolio.projects
      .filter((p) => [p.name, p.project_id, p.app_raw].some((value) => value?.toLowerCase().includes(needle)))
      .slice(0, PER_GROUP)
      .map((p) => ({
        group: 'Delivery projects',
        label: p.name,
        detail: `${p.project_id} · ${p.phase ?? 'phase n/a'}`,
        to: `/delivery/projects/${encodeURIComponent(p.project_id)}`,
      }));
  },
} satisfies WebModule<'delivery'>;

export default delivery;
