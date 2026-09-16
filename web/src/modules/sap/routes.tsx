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
    filters: ['period', 'as_of', 'include_drafts'], // include_drafts: the AI-assisted subcategory breakdown
    load: () => import('./pages/SapTicketsPage'),
  },
  {
    path: 'sap/changes',
    title: 'SAP changes',
    filters: ['period', 'as_of'],
    load: () => import('./pages/SapChangesPage'),
  },
  {
    path: 'sap/idocs',
    title: 'SAP IDocs',
    filters: ['period', 'as_of'],
    load: () => import('./pages/SapIdocsPage'),
  },
] as const satisfies readonly WebRoute<'sap'>[];
