/**
 * Web module contract. A module lives in `src/modules/<key>/index.ts` and default-exports its manifest, checked with
 * `satisfies WebModule<'<key>'>` so every route path is `<key>` or `<key>/...` at compile time (pages `#/<key>/...`,
 * the same namespace as `/api/<key>` and nav ids `<key>.<page>`). Navigation itself comes from GET /api/nav; a nav
 * item is shown only when one of these routes matches its path.
 */
import type { ComponentType } from 'react';

import type { FilterOption } from '../api/types';
import type { FilterKey } from '../hooks/useFilters';

/** Route path inside a module namespace (no leading slash): `ops`, `ops/tickets`, `ops/apps/:appId`. */
export type ModuleRoutePath<K extends string> = K | `${K}/${string}`;

export interface WebRoute<K extends string> {
  path: ModuleRoutePath<K>;
  /** Page title (browser tab and header). */
  title: string;
  /** Global filters this page honours (the FilterBar shows only these). */
  filters: readonly FilterKey[];
  /** Lazily loaded page component (a separate chunk). */
  load: () => Promise<{ default: ComponentType }>;
}

/** Extra filter choices a module contributes (for example ops families); merged with /api/meta entities. */
export type FilterOptionSets = Partial<Record<'app' | 'family' | 'vendor' | 'group', FilterOption[]>>;

export interface WebModule<K extends string> {
  key: K;
  title: string;
  routes: readonly WebRoute<K>[];
  filterOptions?: (signal: AbortSignal) => Promise<FilterOptionSets>;
}

export type AnyWebModule = WebModule<string>;

/** Module keys follow the server rule (`^[a-z][a-z0-9]{1,15}$`). */
export const MODULE_KEY_RE = /^[a-z][a-z0-9]{1,15}$/;
