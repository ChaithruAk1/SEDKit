/** Delivery web module (module #3): manifest discovered by src/modules/registry.ts. */
import type { WebModule } from '../types';
import { DELIVERY_ROUTES } from './routes';

const delivery = {
  key: 'delivery',
  title: 'Delivery management',
  routes: DELIVERY_ROUTES,
} satisfies WebModule<'delivery'>;

export default delivery;
