import { useMemo } from 'react';

import type { FindingOut, GetQuery } from '../../../api/types';
import { useApi } from '../../../api/useApi';
import type { ApiError } from '../../../api/client';

type FindingsQuery = NonNullable<GetQuery<'/api/findings'>>;

export interface FindingsState {
  items: FindingOut[] | undefined;
  loading: boolean;
  error: ApiError | undefined;
  reload: () => void;
}

/**
 * Published findings (rule: active and not suppressed; AI: approved), plus AI drafts when the "Include AI drafts"
 * filter is on. Drafts come from `status=all&origin=ai` and are marked by their status in the UI.
 */
export function useFindings(query: Omit<FindingsQuery, 'status'>, includeDrafts: boolean, kinds?: readonly string[]): FindingsState {
  const published = useApi('/api/findings', { query: { ...query, status: 'published' } });
  const drafts = useApi(includeDrafts ? '/api/findings' : null, { query: { ...query, origin: 'ai', status: 'all' } });

  const items = useMemo(() => {
    if (!published.data) return undefined;
    const rows = [...published.data.items];
    if (includeDrafts && drafts.data) {
      const seen = new Set(rows.map((f) => f.finding_id));
      for (const finding of drafts.data.items) {
        if (!seen.has(finding.finding_id) && finding.status === 'draft') rows.push(finding);
      }
    }
    return kinds ? rows.filter((f) => kinds.includes(f.kind)) : rows;
  }, [published.data, drafts.data, includeDrafts, kinds]);

  return {
    items,
    loading: published.loading || (includeDrafts && drafts.loading),
    error: published.error ?? (includeDrafts ? drafts.error : undefined),
    reload: () => {
      published.reload();
      if (includeDrafts) drafts.reload();
    },
  };
}
