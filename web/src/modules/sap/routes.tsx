/** SAP pages (`#/sap/...`), each loaded lazily. Area and landscape are page-local selectors, not global filters. */
import type { WebRoute } from '../types';

export const SAP_ROUTES = [
  {
    path: 'sap',
    title: 'SAP overview',
    filters: ['as_of'],
    load: () => import('./pages/SapOverviewPage'),
  },
  {
    path: 'sap/tickets',
    title: 'SAP L3 tickets',
    filters: ['period', 'as_of'],
    load: () => import('./pages/SapTicketsPage'),
  },
] as const satisfies readonly WebRoute<'sap'>[];
