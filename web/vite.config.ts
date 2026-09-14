/**
 * Vite config for the SED dashboard.
 *
 * - `base: './'` so `sed serve` can host web/dist at any mount point; assets land in dist/assets (served at /assets).
 * - `npm run dev` proxies /api to the local API (127.0.0.1:8000 unless SED_API_PORT is set). The proxy adds
 *   X-SED-Token from SED_DEV_TOKEN (the token `sed serve --dev` uses), and only for same-origin requests from the dev
 *   page itself, so another site cannot borrow the token through the proxy.
 * - `npm run dev:fixtures` (mode "fixtures") sets VITE_SED_FIXTURES=1: every API call is answered by the typed
 *   synthetic fixtures in src/api/fixtures, no API needed.
 */
import react from '@vitejs/plugin-react';
import type { IncomingMessage } from 'node:http';
import { defineConfig } from 'vite';

const DEV_HOST = '127.0.0.1';
const DEV_PORT = 5173;
const LOCAL_HOSTS = new Set(['127.0.0.1', 'localhost']);

function hostName(value: string | undefined): string | null {
  if (!value) return null;
  try {
    return new URL(value.includes('://') ? value : `http://${value}`).hostname;
  } catch {
    return null;
  }
}

/** True when the proxied request comes from the dev page (local Host, and a local Origin if one is sent). */
function fromDevPage(req: IncomingMessage): boolean {
  const host = hostName(req.headers.host);
  if (!host || !LOCAL_HOSTS.has(host)) return false;
  const origin = req.headers.origin;
  if (origin === undefined) return true;
  const originHost = hostName(origin);
  return originHost !== null && LOCAL_HOSTS.has(originHost);
}

export default defineConfig(({ mode }) => {
  const fixtures = mode === 'fixtures';
  const token = process.env.SED_DEV_TOKEN ?? '';
  const apiPort = Number(process.env.SED_API_PORT ?? '8000');

  return {
    base: './',
    publicDir: false,
    plugins: [react()],
    define: {
      'import.meta.env.VITE_SED_FIXTURES': JSON.stringify(fixtures ? '1' : (process.env.VITE_SED_FIXTURES ?? '')),
    },
    build: {
      outDir: 'dist',
      assetsDir: 'assets',
      emptyOutDir: true,
      sourcemap: false,
      chunkSizeWarningLimit: 900,
    },
    server: {
      host: DEV_HOST,
      port: DEV_PORT,
      strictPort: true,
      proxy: fixtures
        ? undefined
        : {
            '/api': {
              target: `http://${DEV_HOST}:${apiPort}`,
              changeOrigin: true,
              configure(proxy) {
                if (!token) {
                  console.warn('[sed] SED_DEV_TOKEN is not set: POST requests through the dev proxy will get 403.');
                }
                proxy.on('proxyReq', (proxyReq, req) => {
                  proxyReq.removeHeader('x-sed-token');
                  if (token && fromDevPage(req)) proxyReq.setHeader('X-SED-Token', token);
                });
              },
            },
          },
    },
    preview: { host: DEV_HOST, port: 4173, strictPort: true },
  };
});
