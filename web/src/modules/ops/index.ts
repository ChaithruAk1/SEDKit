/** Ops web module (module #1): manifest discovered by src/modules/registry.ts. */
import { apiGet } from '../../api/client';
import type { WebModule } from '../types';
import { OPS_ROUTES } from './routes';

const ops = {
  key: 'ops',
  title: 'Application operations',
  routes: OPS_ROUTES,
  async filterOptions(signal) {
    const data = await apiGet('/api/ops/filters', undefined, signal);
    return {
      family: data.families.map((family) => ({ value: family, label: family })),
      app: data.apps.map((app) => ({ value: app.app_id, label: app.family ? `${app.name} (${app.family})` : app.name })),
    };
  },
} satisfies WebModule<'ops'>;

export default ops;
