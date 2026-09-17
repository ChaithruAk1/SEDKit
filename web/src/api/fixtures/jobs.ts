/**
 * Background job fixtures shared by every POST that starts a job (report builds, uploads, pulls). A job answers
 * `running` on its first poll and finishes on the second, so pages show both states.
 */
import { ApiError } from '../client';
import type { Schema } from '../types';
import { AS_OF, isoAt } from './random';

type Job = Schema<'JobOut'>;

const JOBS = new Map<string, { job: Job; polls: number; finish: (job: Job) => void }>();
let JOB_SEQ = 0;

/** Register a queued job; `finish` completes it on the second poll (set status, finished_at and result or error). */
export function startJob(kind: string, params: Record<string, unknown>, finish: (job: Job) => void): Job {
  JOB_SEQ += 1;
  const job: Job = {
    job_id: `job-fixture-${JOB_SEQ}`,
    kind,
    status: 'queued',
    created_at: isoAt(AS_OF, 10, JOB_SEQ % 60),
    finished_at: null,
    params,
    result: null,
    error: null,
  };
  JOBS.set(job.job_id, { job, polls: 0, finish });
  return job;
}

/** The fixture job sequence number of a job id (for stable fixture timestamps). */
export function jobSeq(job: Job): number {
  return Number(job.job_id.split('-').pop() ?? '0');
}

export function jobStatus(jobId: string): Job {
  const entry = JOBS.get(jobId);
  if (!entry) throw new ApiError(412, 'precondition', `Unknown job '${jobId}'`);
  entry.polls += 1;
  const { job } = entry;
  if (entry.polls === 1) return { ...job, status: 'running' };
  if (job.status === 'queued') entry.finish(job);
  return job;
}
