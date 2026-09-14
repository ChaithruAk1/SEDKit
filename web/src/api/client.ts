/**
 * Typed API client for the local SED API.
 *
 * - Relative `/api/...` URLs only (same origin as the served dashboard; no absolute hosts).
 * - POST requests carry `X-SED-Token`, read from `<meta name="sed-token">` that `sed serve` fills in per launch.
 *   In `npm run dev` the meta keeps its placeholder and the Vite proxy adds the header instead.
 * - Non-2xx responses are parsed as the ErrorEnvelope `{ok: false, error: {kind, message, details}}` and thrown as
 *   `ApiError`; transport failures become kind `network`.
 * - In fixtures mode (VITE_SED_FIXTURES=1) calls are answered by the typed synthetic fixtures instead of fetch.
 */
import type {
  ErrorEnvelope,
  GetOptions,
  GetPath,
  GetPathParams,
  GetResponse,
  PostBody,
  PostPath,
  PostResponse,
} from './types';

export const TOKEN_META = 'sed-token';
export const TOKEN_HEADER = 'X-SED-Token';
// Assembled at runtime so a server that substitutes the placeholder in every served file cannot rewrite this check.
const TOKEN_PLACEHOLDER = ['__SED', 'TOKEN__'].join('_');

export const FIXTURES_MODE = import.meta.env.VITE_SED_FIXTURES === '1';

const KIND_BY_STATUS: Record<number, string> = {
  400: 'bad_request',
  403: 'forbidden',
  404: 'not_found',
  409: 'busy',
  412: 'precondition',
  422: 'validation',
  501: 'not_implemented',
};

export class ApiError extends Error {
  readonly status: number;
  readonly kind: string;
  readonly details: unknown;
  /** Seconds from `Retry-After` (busy responses), when present. */
  readonly retryAfter: number | null;

  constructor(status: number, kind: string, message: string, details: unknown = null, retryAfter: number | null = null) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.kind = kind;
    this.details = details;
    this.retryAfter = retryAfter;
  }
}

export function isApiError(value: unknown): value is ApiError {
  return value instanceof ApiError;
}

export function isAbortError(value: unknown): boolean {
  return value instanceof DOMException && value.name === 'AbortError';
}

export function isErrorEnvelope(value: unknown): value is ErrorEnvelope {
  if (typeof value !== 'object' || value === null) return false;
  const candidate = value as { ok?: unknown; error?: unknown };
  if (candidate.ok !== false || typeof candidate.error !== 'object' || candidate.error === null) return false;
  const error = candidate.error as { kind?: unknown; message?: unknown };
  return typeof error.kind === 'string' && typeof error.message === 'string';
}

/** The per-launch token injected into index.html, or null (placeholder not replaced: dev proxy or fixtures). */
export function readToken(doc: Document = document): string | null {
  const value = doc.querySelector(`meta[name="${TOKEN_META}"]`)?.getAttribute('content')?.trim() ?? '';
  return value && value !== TOKEN_PLACEHOLDER ? value : null;
}

type QueryValue = string | number | boolean | null | undefined | readonly (string | number | boolean)[];

/** Build `/api/...?...` from a templated path, path params (URL-encoded) and query params (arrays repeat). */
export function buildUrl(path: string, params?: Record<string, string | number>, query?: object): string {
  const resolved = path.replace(/\{([^}]+)\}/g, (_, name: string) => {
    const value = params?.[name];
    if (value === undefined || value === '') throw new ApiError(0, 'validation', `Missing path parameter '${name}'`);
    return encodeURIComponent(String(value));
  });
  const search = new URLSearchParams();
  for (const [key, raw] of Object.entries((query ?? {}) as Record<string, QueryValue>)) {
    if (raw === undefined || raw === null || raw === '') continue;
    const values = Array.isArray(raw) ? raw : [raw];
    for (const value of values) search.append(key, String(value));
  }
  const qs = search.toString();
  return qs ? `${resolved}?${qs}` : resolved;
}

function retryAfterSeconds(response: Response): number | null {
  const raw = response.headers.get('Retry-After');
  if (!raw) return null;
  const seconds = Number(raw);
  return Number.isFinite(seconds) ? seconds : null;
}

async function send<T>(method: 'GET' | 'POST', url: string, body: unknown, signal?: AbortSignal): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (method !== 'GET') {
    headers['Content-Type'] = 'application/json';
    const token = readToken();
    if (token) headers[TOKEN_HEADER] = token;
  }
  let response: Response;
  try {
    response = await fetch(url, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
      cache: 'no-store',
      credentials: 'same-origin',
      referrerPolicy: 'no-referrer',
    });
  } catch (error) {
    if (isAbortError(error)) throw error;
    throw new ApiError(0, 'network', 'The SED API is not reachable. Is `sed serve` running?');
  }
  const text = await response.text();
  let payload: unknown = undefined;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = undefined;
    }
  }
  if (!response.ok) {
    if (isErrorEnvelope(payload)) {
      const { kind, message, details } = payload.error;
      throw new ApiError(response.status, kind, message, details ?? null, retryAfterSeconds(response));
    }
    const kind = KIND_BY_STATUS[response.status] ?? 'internal';
    throw new ApiError(response.status, kind, `HTTP ${response.status} from ${url}`, null, retryAfterSeconds(response));
  }
  if (payload === undefined) {
    throw new ApiError(response.status, 'invalid_response', `Expected JSON from ${url}`);
  }
  return payload as T;
}

/** Options are required (for the path parameters) only on templated routes. */
export type GetArgs<P extends GetPath> = [GetPathParams<P>] extends [never]
  ? [options?: GetOptions<P>, signal?: AbortSignal]
  : [options: GetOptions<P>, signal?: AbortSignal];

/** Typed GET: `apiGet('/api/ops/apps/{app_id}', {params: {app_id}, query: {...}})`. */
export async function apiGet<P extends GetPath>(path: P, ...args: GetArgs<P>): Promise<GetResponse<P>> {
  const [options, signal] = args as [GetOptions<P> | undefined, AbortSignal | undefined];
  const params = (options?.params ?? undefined) as Record<string, string> | undefined;
  const query = (options?.query ?? undefined) as object | undefined;
  if (FIXTURES_MODE) {
    const fixtures = await import('./fixtures');
    return fixtures.fixtureGet(path, params ?? {}, query ?? {}, signal) as Promise<GetResponse<P>>;
  }
  return send<GetResponse<P>>('GET', buildUrl(path, params, query), undefined, signal);
}

/** Typed POST with the launch token: `apiPost('/api/aliases', {kind, raw_value, target})`. */
export async function apiPost<P extends PostPath>(path: P, body: PostBody<P>, signal?: AbortSignal): Promise<PostResponse<P>> {
  if (FIXTURES_MODE) {
    const fixtures = await import('./fixtures');
    return fixtures.fixturePost(path, body, signal) as Promise<PostResponse<P>>;
  }
  return send<PostResponse<P>>('POST', buildUrl(path), body, signal);
}
