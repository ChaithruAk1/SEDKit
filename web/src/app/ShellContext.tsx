/**
 * Shell-wide data loaded once: /api/meta (data class, periods, entities, definitions), /api/nav (server-driven
 * navigation) and the filter choices contributed by modules. Pages read it with `useShell()`.
 */
import { createContext, type ReactNode, useContext, useEffect, useMemo, useState } from 'react';
import { matchPath } from 'react-router';

import type { ApiError } from '../api/client';
import type { FilterOption, MetaOut, NavItemOut } from '../api/types';
import type { ApiState } from '../api/useApi';
import { useCachedApi } from '../api/useApi';
import { setBaseCurrency } from '../components/format';
import { CORE_ROUTES } from '../core/routes';
import { MODULES } from '../modules/registry';
import type { FilterOptionSets } from '../modules/types';

export interface RegisteredRoute {
  /** Absolute router path, e.g. `/ops/apps/:appId`. */
  path: string;
  title: string;
  moduleKey: string;
}

export const REGISTERED_ROUTES: readonly RegisteredRoute[] = [
  ...MODULES.flatMap((m) => m.routes.map((r) => ({ path: `/${r.path}`, title: r.title, moduleKey: m.key }))),
  ...CORE_ROUTES.map((r) => ({ path: `/${r.path}`, title: r.title, moduleKey: 'core' })),
];

/** True when a server nav path is served by a registered page. */
export function hasRegisteredRoute(path: string): boolean {
  return REGISTERED_ROUTES.some((route) => matchPath({ path: route.path, end: true }, path) !== null);
}

export interface NavEntry {
  id: string;
  label: string;
  path: string;
  icon: string | null;
  order: number;
  module: string;
}

export interface ShellValue {
  meta: ApiState<MetaOut>;
  navState: ApiState<{ items: NavItemOut[] }>;
  /** Server nav items that have a registered page, in server order. */
  nav: NavEntry[];
  /** True when /api/nav failed and the nav was derived from the local registry. */
  navFallback: boolean;
  filterOptions: FilterOptionSets;
  filterOptionsError: ApiError | Error | null;
}

const ShellContext = createContext<ShellValue | null>(null);

function localNav(): NavEntry[] {
  return REGISTERED_ROUTES.filter((r) => !r.path.includes(':')).map((r, index) => ({
    id: `${r.moduleKey}.${index}`,
    label: r.title,
    path: r.path,
    icon: r.moduleKey === 'core' ? 'database' : null,
    order: r.moduleKey === 'core' ? 900 + index : 10 + index,
    module: r.moduleKey,
  }));
}

function mergeOptions(meta: MetaOut | undefined, modules: FilterOptionSets): FilterOptionSets {
  const byValue = (lists: (FilterOption[] | undefined)[]) => {
    const merged = new Map<string, FilterOption>();
    for (const list of lists) for (const option of list ?? []) merged.set(option.value, option);
    return [...merged.values()].sort((a, b) => a.label.localeCompare(b.label));
  };
  return {
    app: byValue([meta?.entities.app, modules.app]),
    family: byValue([meta?.entities.family, modules.family]),
    vendor: byValue([meta?.entities.vendor, modules.vendor]),
    group: byValue([meta?.entities.group, modules.group]),
  };
}

export function ShellProvider({ children }: { children: ReactNode }) {
  const meta = useCachedApi('/api/meta');
  const navState = useCachedApi('/api/nav');
  const [moduleOptions, setModuleOptions] = useState<FilterOptionSets>({});
  const [filterOptionsError, setFilterOptionsError] = useState<ApiError | Error | null>(null);

  useEffect(() => {
    setBaseCurrency(meta.data?.base_currency);
  }, [meta.data?.base_currency]);

  const enabledKeys = meta.data ? meta.data.modules.map((m) => m.key).join(',') : null;
  useEffect(() => {
    const controller = new AbortController();
    const enabled = enabledKeys === null ? null : new Set(enabledKeys.split(','));
    const sources = MODULES.filter((m) => m.filterOptions && (enabled === null || enabled.has(m.key)));
    Promise.allSettled(sources.map((m) => m.filterOptions!(controller.signal))).then((results) => {
      if (controller.signal.aborted) return;
      const merged: FilterOptionSets = {};
      for (const result of results) {
        if (result.status === 'rejected') {
          setFilterOptionsError(result.reason instanceof Error ? result.reason : new Error(String(result.reason)));
          continue;
        }
        for (const [key, options] of Object.entries(result.value) as [keyof FilterOptionSets, FilterOption[]][]) {
          merged[key] = [...(merged[key] ?? []), ...options];
        }
      }
      setModuleOptions(merged);
    });
    return () => controller.abort();
  }, [enabledKeys]);

  const value = useMemo<ShellValue>(() => {
    const serverItems = navState.data?.items;
    const navFallback = !serverItems && Boolean(navState.error);
    const nav = serverItems
      ? [...serverItems]
          .filter((item) => hasRegisteredRoute(item.path))
          .sort((a, b) => a.order - b.order || a.label.localeCompare(b.label))
          .map((item) => ({ ...item }))
      : navFallback
        ? localNav()
        : [];
    return {
      meta,
      navState,
      nav,
      navFallback,
      filterOptions: mergeOptions(meta.data, moduleOptions),
      filterOptionsError,
    };
  }, [meta, navState, moduleOptions, filterOptionsError]);

  return <ShellContext.Provider value={value}>{children}</ShellContext.Provider>;
}

export function useShell(): ShellValue {
  const value = useContext(ShellContext);
  if (!value) throw new Error('useShell() outside ShellProvider');
  return value;
}
