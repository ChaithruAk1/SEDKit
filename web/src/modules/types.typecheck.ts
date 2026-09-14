/**
 * Compile-time checks for the module and API contracts (never imported by the app; `npm run typecheck` verifies them).
 * Each `@ts-expect-error` must stay an error: if the constraint is loosened, typecheck fails with "unused directive".
 */
import { apiGet, apiPost } from '../api/client';
import { useApi } from '../api/useApi';
import type { WebModule } from './types';

const Page = () => null;
const load = async () => ({ default: Page });

// A route inside the module namespace is accepted.
export const valid = {
  key: 'demo',
  title: 'Demo',
  routes: [
    { path: 'demo', title: 'Home', filters: [], load },
    { path: 'demo/items/:id', title: 'Item', filters: ['as_of'], load },
  ],
} satisfies WebModule<'demo'>;

export const outsideNamespace = {
  key: 'demo',
  title: 'Demo',
  routes: [
    // @ts-expect-error route paths must start with the module key
    { path: 'tickets', title: 'Tickets', filters: [], load },
  ],
} satisfies WebModule<'demo'>;

export const prefixLookalike = {
  key: 'demo',
  title: 'Demo',
  routes: [
    // @ts-expect-error `demonstration` is not `demo` or `demo/...`
    { path: 'demonstration', title: 'Other', filters: [], load },
  ],
} satisfies WebModule<'demo'>;

export const unknownFilter = {
  key: 'demo',
  title: 'Demo',
  routes: [
    // @ts-expect-error filters must be global filter keys
    { path: 'demo', title: 'Home', filters: ['colour'], load },
  ],
} satisfies WebModule<'demo'>;

export async function apiContractChecks(): Promise<void> {
  // Typed responses and query parameters come from contracts/openapi.json.
  const overview = await apiGet('/api/ops/overview', { query: { app: ['APM1001000'], include_drafts: true } });
  overview.kpis.map((kpi) => kpi.key);

  // @ts-expect-error unknown API path
  await apiGet('/api/ops/unknown');

  // @ts-expect-error path parameters are required for templated routes
  await apiGet('/api/ops/apps/{app_id}');

  // @ts-expect-error query parameters are typed (page is an integer)
  await apiGet('/api/ops/tickets', { query: { page: 'two' } });

  await apiPost('/api/aliases', { kind: 'app', raw_value: 'Orion ERP (Prod)', target: 'APM1001000' });

  // @ts-expect-error the POST body must match AliasIn
  await apiPost('/api/aliases', { kind: 'app', raw: 'x' });
}

export function useApiContractChecks(): void {
  useApi('/api/ops/apps/{app_id}', { params: { app_id: 'APM1001000' } });
  // @ts-expect-error useApi also requires path parameters on templated routes
  useApi('/api/ops/tickets/{ticket_id}');
}
