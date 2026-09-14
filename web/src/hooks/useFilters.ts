/**
 * Global filters live in the URL (hash query string), so every view is linkable and survives reloads:
 * `#/ops/tickets?app=APM1001000&period=2026-W35&include_drafts=true`.
 *
 * Only well-formed values are passed on to the API (invalid dates or periods in a hand-edited URL are ignored
 * instead of producing validation errors). Page-local parameters (tab, q, page, ...) are separate; see
 * `useSearchParam`.
 */
import { useCallback, useMemo } from 'react';
import { useMatches, useSearchParams } from 'react-router';

import type { CommonFilterQuery } from '../api/types';

export const FILTER_KEYS = ['app', 'family', 'vendor', 'group', 'period', 'as_of', 'include_drafts'] as const;
export type FilterKey = (typeof FILTER_KEYS)[number];

export interface Filters {
  app: string[];
  family: string | null;
  vendor: string | null;
  group: string | null;
  period: string | null;
  as_of: string | null;
  include_drafts: boolean;
}

export const EMPTY_FILTERS: Filters = {
  app: [],
  family: null,
  vendor: null,
  group: null,
  period: null,
  as_of: null,
  include_drafts: false,
};

const DATE_RE = /^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$/;
const PERIOD_RE = /^\d{4}-(W(0[1-9]|[1-4]\d|5[0-3])|0[1-9]|1[0-2]|Q[1-4])$/;
const MAX_TEXT = 200;

function text(value: string | null): string | null {
  const trimmed = value?.trim() ?? '';
  return trimmed && trimmed.length <= MAX_TEXT ? trimmed : null;
}

export function parseFilters(params: URLSearchParams): Filters {
  const asOf = params.get('as_of');
  const period = params.get('period');
  return {
    app: [...new Set(params.getAll('app').map((v) => v.trim()).filter((v) => v && v.length <= MAX_TEXT))],
    family: text(params.get('family')),
    vendor: text(params.get('vendor')),
    group: text(params.get('group')),
    period: period && PERIOD_RE.test(period) ? period : null,
    as_of: asOf && DATE_RE.test(asOf) ? asOf : null,
    include_drafts: params.get('include_drafts') === 'true',
  };
}

/** API query parameters for the ops `CommonFilters` (unset values omitted). */
export function toQuery(filters: Filters): CommonFilterQuery {
  const query: CommonFilterQuery = {};
  if (filters.app.length) query.app = filters.app;
  if (filters.family) query.family = filters.family;
  if (filters.vendor) query.vendor = filters.vendor;
  if (filters.group) query.group = filters.group;
  if (filters.period) query.period = filters.period;
  if (filters.as_of) query.as_of = filters.as_of;
  if (filters.include_drafts) query.include_drafts = true;
  return query;
}

/** `?app=...&period=...` with only the global filters, to carry them across navigation. */
export function filterSearch(params: URLSearchParams): string {
  const out = new URLSearchParams();
  for (const key of FILTER_KEYS) for (const value of params.getAll(key)) out.append(key, value);
  const qs = out.toString();
  return qs ? `?${qs}` : '';
}

export interface FiltersApi {
  filters: Filters;
  query: CommonFilterQuery;
  /** Global filters as a search string (`?...` or empty) for links. */
  search: string;
  setFilter: <K extends FilterKey>(key: K, value: Filters[K]) => void;
  clearFilters: () => void;
}

/** Filters with every key the page does not declare reset, so a filter the page does not show never changes it. */
export function onlyKeys(filters: Filters, keys: readonly FilterKey[] | null): Filters {
  if (keys === null) return filters;
  const out: Filters = { ...EMPTY_FILTERS };
  for (const key of keys) (out as unknown as Record<FilterKey, unknown>)[key] = filters[key];
  return out;
}

/** The global filters the current page declares (route handle `filters`); null outside a page route. */
function useRouteFilterKeys(): readonly FilterKey[] | null {
  const matches = useMatches();
  for (let i = matches.length - 1; i >= 0; i -= 1) {
    const handle = matches[i]?.handle as { filters?: readonly FilterKey[] } | undefined;
    if (handle?.filters) return handle.filters;
  }
  return null;
}

/**
 * The page's filters. Filters in the URL that the page does not declare (carried over from another page by nav
 * links) stay in the URL for the next page but are not applied here: `filters` and `query` only hold declared keys.
 */
export function useFilters(): FiltersApi {
  const [params, setParams] = useSearchParams();
  const serialized = params.toString();
  const keys = useRouteFilterKeys(); // the route's static array: a stable dependency
  const filters = useMemo(() => onlyKeys(parseFilters(new URLSearchParams(serialized)), keys), [serialized, keys]);
  const query = useMemo(() => toQuery(filters), [filters]);
  const search = useMemo(() => filterSearch(new URLSearchParams(serialized)), [serialized]);

  const setFilter = useCallback(
    <K extends FilterKey>(key: K, value: Filters[K]) => {
      setParams(
        (previous) => {
          const next = new URLSearchParams(previous);
          next.delete(key);
          if (Array.isArray(value)) value.forEach((v) => next.append(key, v));
          else if (typeof value === 'boolean') {
            if (value) next.set(key, 'true');
          } else if (value) next.set(key, value as string);
          next.delete('page');
          return next;
        },
        { replace: true },
      );
    },
    [setParams],
  );

  const clearFilters = useCallback(() => {
    setParams(
      (previous) => {
        const next = new URLSearchParams(previous);
        FILTER_KEYS.forEach((key) => next.delete(key));
        next.delete('page');
        return next;
      },
      { replace: true },
    );
  }, [setParams]);

  return { filters, query, search, setFilter, clearFilters };
}

/** Set or clear several page-local URL parameters in one history update. */
export function usePatchSearchParams(): (patch: Record<string, string | null>) => void {
  const [, setParams] = useSearchParams();
  return useCallback(
    (patch: Record<string, string | null>) => {
      setParams(
        (previous) => {
          const updated = new URLSearchParams(previous);
          for (const [key, value] of Object.entries(patch)) {
            if (value === null || value === '') updated.delete(key);
            else updated.set(key, value);
          }
          return updated;
        },
        { replace: true },
      );
    },
    [setParams],
  );
}

/** A page-local URL parameter (tab, search text, page number...) with a default. */
export function useSearchParam(key: string, fallback: string): [string, (value: string | null) => void] {
  const [params, setParams] = useSearchParams();
  const value = params.get(key) ?? fallback;
  const setValue = useCallback(
    (next: string | null) => {
      setParams(
        (previous) => {
          const updated = new URLSearchParams(previous);
          if (next === null || next === '' || next === fallback) updated.delete(key);
          else updated.set(key, next);
          return updated;
        },
        { replace: true },
      );
    },
    [key, fallback, setParams],
  );
  return [value, setValue];
}
