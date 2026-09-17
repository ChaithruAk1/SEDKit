/**
 * Poll one background job (`GET /api/jobs/{job_id}`) until it is done or failed. `onFinished` runs once when it
 * finishes (e.g. to reload the data the job changed).
 */
import { useEffect, useRef, useState } from 'react';

import { type ApiError, apiGet } from './client';
import type { Schema } from './types';
import { toApiError } from './useApi';

type Job = Schema<'JobOut'>;

const POLL_MS = 1000;

export function useJob(
  jobId: string | null,
  onFinished?: (job: Job) => void,
): { job: Job | undefined; error: ApiError | undefined } {
  const [job, setJob] = useState<Job | undefined>(undefined);
  const [error, setError] = useState<ApiError | undefined>(undefined);
  const finished = useRef(onFinished);
  finished.current = onFinished;
  useEffect(() => {
    setJob(undefined);
    setError(undefined);
    if (!jobId) return;
    let stopped = false;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const next = await apiGet('/api/jobs/{job_id}', { params: { job_id: jobId } });
        if (stopped) return;
        setJob(next);
        if (next.status === 'queued' || next.status === 'running') timer = window.setTimeout(poll, POLL_MS);
        else finished.current?.(next);
      } catch (caught) {
        if (!stopped) setError(toApiError(caught));
      }
    };
    void poll();
    return () => {
      stopped = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [jobId]);
  return { job, error };
}
