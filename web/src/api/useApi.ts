/**
 * Plain fetch hooks (no state library).
 *
 * - `useApi(path, options)` refetches whenever the path, path params or query change, aborts stale requests and keeps
 *   the previous data visible while a refetch is running.
 * - `useCachedApi(path)` shares one request per path across components (meta, nav, runs); `reload()` refreshes it.
 * - `useApiPost(path)` runs a POST on demand and exposes its pending state and error.
 */
import { useCallback, useEffect, useMemo, useState, useSyncExternalStore } from 'react';

import { ApiError, type GetArgs, type PostArgs, apiGet, apiPost, isAbortError } from './client';
import type { GetOptions, GetPath, GetPathParams, GetResponse, PostBody, PostPath, PostResponse } from './types';

export interface ApiState<T> {
  data: T | undefined;
  error: ApiError | undefined;
  loading: boolean;
  reload: () => void;
}

export function toApiError(error: unknown): ApiError {
  if (error instanceof ApiError) return error;
  const message = error instanceof Error ? error.message : String(error);
  return new ApiError(0, 'internal', message);
}

interface Snapshot<T> {
  key: string | null;
  data: T | undefined;
  error: ApiError | undefined;
  loading: boolean;
}

/** `null` path = do not fetch (for example until a selection exists). Templated routes require path params. */
export function useApi<P extends GetPath>(
  path: P | null,
  ...args: [GetPathParams<P>] extends [never] ? [options?: GetOptions<P>] : [options: GetOptions<P>]
): ApiState<GetResponse<P>> {
  const options = args[0] as GetOptions<P> | undefined;
  const key = path === null ? null : JSON.stringify([path, options?.params ?? null, options?.query ?? null]);
  const [nonce, setNonce] = useState(0);
  const [snapshot, setSnapshot] = useState<Snapshot<GetResponse<P>>>({
    key: null,
    data: undefined,
    error: undefined,
    loading: key !== null,
  });

  useEffect(() => {
    if (key === null) {
      setSnapshot({ key: null, data: undefined, error: undefined, loading: false });
      return;
    }
    const [requestPath, params, query] = JSON.parse(key) as [P, unknown, unknown];
    const requestOptions = { params: params ?? undefined, query: query ?? undefined } as unknown as GetOptions<P>;
    const controller = new AbortController();
    setSnapshot((previous) => ({ ...previous, loading: true }));
    apiGet(requestPath, ...([requestOptions, controller.signal] as unknown as GetArgs<P>)).then(
      (data) => {
        if (!controller.signal.aborted) setSnapshot({ key, data, error: undefined, loading: false });
      },
      (error: unknown) => {
        if (controller.signal.aborted || isAbortError(error)) return;
        setSnapshot((previous) => ({
          key,
          data: previous.key === key ? previous.data : undefined,
          error: toApiError(error),
          loading: false,
        }));
      },
    );
    return () => controller.abort();
  }, [key, nonce]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  const stale = snapshot.key !== key;
  return {
    data: snapshot.data,
    error: stale ? undefined : snapshot.error,
    loading: key !== null && (snapshot.loading || stale),
    reload,
  };
}

// ---------------------------------------------------------------------------
// shared (cached) GETs
// ---------------------------------------------------------------------------

interface CacheEntry {
  data: unknown;
  error: ApiError | undefined;
  loading: boolean;
  listeners: Set<() => void>;
  snapshot: Snapshot<unknown>;
}

const cache = new Map<string, CacheEntry>();

function entryFor(path: string): CacheEntry {
  let entry = cache.get(path);
  if (!entry) {
    entry = {
      data: undefined,
      error: undefined,
      loading: false,
      listeners: new Set(),
      snapshot: { key: path, data: undefined, error: undefined, loading: true },
    };
    cache.set(path, entry);
  }
  return entry;
}

function publish(entry: CacheEntry, path: string): void {
  entry.snapshot = { key: path, data: entry.data, error: entry.error, loading: entry.loading };
  entry.listeners.forEach((listener) => listener());
}

function fetchShared(path: GetPath): void {
  const entry = entryFor(path);
  if (entry.loading) return;
  entry.loading = true;
  publish(entry, path);
  (apiGet as (p: GetPath) => Promise<unknown>)(path).then(
    (data) => {
      entry.data = data;
      entry.error = undefined;
      entry.loading = false;
      publish(entry, path);
    },
    (error: unknown) => {
      entry.error = toApiError(error);
      entry.loading = false;
      publish(entry, path);
    },
  );
}

/** Shared GET for parameterless, rarely changing endpoints (meta, nav, runs, module filter options). */
export function useCachedApi<P extends GetPath>(path: P): ApiState<GetResponse<P>> {
  const subscribe = useCallback(
    (listener: () => void) => {
      const entry = entryFor(path);
      entry.listeners.add(listener);
      if (entry.data === undefined && entry.error === undefined && !entry.loading) fetchShared(path);
      return () => entry.listeners.delete(listener);
    },
    [path],
  );
  const snapshot = useSyncExternalStore(subscribe, () => entryFor(path).snapshot);
  const reload = useCallback(() => fetchShared(path), [path]);
  return useMemo(
    () => ({ data: snapshot.data as GetResponse<P> | undefined, error: snapshot.error, loading: snapshot.loading, reload }),
    [snapshot, reload],
  );
}

// ---------------------------------------------------------------------------
// POST
// ---------------------------------------------------------------------------

export interface PostState<P extends PostPath> {
  /** Templated routes take `{params: {...}}` as the second argument. */
  run: (body: PostBody<P>, ...args: PostArgs<P>) => Promise<PostResponse<P>>;
  data: PostResponse<P> | undefined;
  error: ApiError | undefined;
  pending: boolean;
  reset: () => void;
}

export function useApiPost<P extends PostPath>(path: P): PostState<P> {
  const [pending, setPending] = useState(false);
  const [data, setData] = useState<PostResponse<P> | undefined>(undefined);
  const [error, setError] = useState<ApiError | undefined>(undefined);

  const run = useCallback(
    async (body: PostBody<P>, ...args: PostArgs<P>) => {
      setPending(true);
      setError(undefined);
      try {
        const result = await apiPost(path, body, ...args);
        setData(result);
        return result;
      } catch (caught) {
        const apiError = toApiError(caught);
        setError(apiError);
        throw apiError;
      } finally {
        setPending(false);
      }
    },
    [path],
  );
  const reset = useCallback(() => {
    setData(undefined);
    setError(undefined);
  }, []);
  return { run, data, error, pending, reset };
}
