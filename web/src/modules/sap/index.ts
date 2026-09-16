/** SAP web module (module #2): manifest discovered by src/modules/registry.ts. */
import type { WebModule } from '../types';
import { SAP_ROUTES } from './routes';

const sap = {
  key: 'sap',
  title: 'SAP application support',
  routes: SAP_ROUTES,
} satisfies WebModule<'sap'>;

export default sap;
