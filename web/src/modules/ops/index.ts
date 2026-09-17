/** Ops web module (module #1): manifest discovered by src/modules/registry.ts. */
import { apiGet } from '../../api/client';
import type { SearchHit, WebModule } from '../types';
import { OPS_ROUTES } from './routes';

const PER_GROUP = 5;

function matches(query: string, ...values: (string | null | undefined)[]): boolean {
  const needle = query.toLowerCase();
  return values.some((value) => value?.toLowerCase().includes(needle));
}

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
  async search(query, signal) {
    const [tickets, filters, meta] = await Promise.all([
      apiGet('/api/ops/tickets', { query: { q: query, page_size: PER_GROUP } }, signal),
      apiGet('/api/ops/filters', undefined, signal),
      apiGet('/api/meta', undefined, signal),
    ]);
    const hits: SearchHit[] = [];
    for (const app of filters.apps.filter((a) => matches(query, a.name, a.app_id, a.family)).slice(0, PER_GROUP)) {
      hits.push({ group: 'Applications', label: app.name, detail: app.family ?? app.app_id, to: `/ops/apps/${encodeURIComponent(app.app_id)}` });
    }
    for (const ticket of tickets.items) {
      hits.push({
        group: 'Tickets',
        label: `${ticket.number} ${ticket.short_description ?? ''}`.trim(),
        detail: ticket.app_name ?? undefined,
        to: `/ops/tickets?ticket=${encodeURIComponent(ticket.ticket_id)}`,
      });
    }
    for (const vendor of (meta.entities.vendor ?? []).filter((v) => matches(query, v.label, v.value)).slice(0, PER_GROUP)) {
      hits.push({ group: 'Vendors', label: vendor.label, detail: 'costs and contracts', to: `/ops/costs?vendor=${encodeURIComponent(vendor.value)}` });
    }
    return hits;
  },
} satisfies WebModule<'ops'>;

export default ops;
