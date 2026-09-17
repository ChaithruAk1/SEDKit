/** Delivery pages (`#/delivery/...`), each loaded lazily. */
import type { WebRoute } from '../types';

export const DELIVERY_ROUTES = [
  {
    path: 'delivery',
    title: 'Delivery portfolio',
    filters: ['as_of'],
    load: () => import('./pages/DeliveryPortfolioPage'),
  },
  {
    path: 'delivery/projects/:projectId',
    title: 'Delivery project',
    filters: ['as_of'],
    load: () => import('./pages/DeliveryProjectPage'),
  },
] as const satisfies readonly WebRoute<'delivery'>[];
