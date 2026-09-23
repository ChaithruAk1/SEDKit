/**
 * Fixtures mode (VITE_SED_FIXTURES=1, `npm run dev:fixtures`): answers every API call with typed synthetic data.
 *
 * `GET_HANDLERS` is a mapped type over every GET path in the generated contract, so a new or changed route in
 * contracts/openapi.json fails `npm run typecheck` until its fixture is updated. Data is fictional only.
 */
import { ApiError, isAbortError } from '../client';
import type { GetPath, GetPathParams, GetQuery, GetResponse, PostBody, PostPath, PostPathParams, PostResponse } from '../types';
import * as core from './core';
import * as delivery from './delivery';
import * as jobs from './jobs';
import * as ops from './ops';
import * as reports from './reports';
import * as review from './review';
import * as sap from './sap';
import * as sources from './sources';

type GetHandlers = {
  [P in GetPath]: (params: GetPathParams<P>, query: GetQuery<P>) => GetResponse<P>;
};
type PostHandlers = {
  [P in PostPath]: (body: PostBody<P>, params: PostPathParams<P>) => PostResponse<P>;
};

const GET_HANDLERS: GetHandlers = {
  '/api/health': () => ({ ok: true, version: '0.2.0' }),
  '/api/branding': () => ({ title: null, logo: false, watermark: false, watermark_dark: false }),
  '/api/meta': () => core.meta(),
  '/api/nav': () => core.nav(),
  '/api/modules': () => core.modules(),
  '/api/findings': (_, query) => core.findings(query),
  '/api/imports': (_, query) => core.imports(query),
  '/api/dq/unmapped': (_, query) => core.unmapped(query),
  '/api/alias-targets': (_, query) => core.aliasTargets(query),
  '/api/runs': (_, query) => core.runs(query),
  '/api/runs/{run_id}': (params) => review.runDetail(params.run_id, core.allRuns()),
  '/api/review/queue': (_, query) => review.queue(query),
  '/api/ai/review-rates': (_, query) => review.reviewRates(query),
  '/api/reports': (_, query) => reports.reports(query),
  '/api/reports/readiness': (_, query) => reports.readiness(query),
  '/api/jobs/{job_id}': (params) => jobs.jobStatus(params.job_id),
  '/api/ops/filters': () => ops.filters(),
  '/api/ops/overview': (_, query) => ops.overview(query),
  '/api/ops/attention': (_, query) => ops.attention(query),
  '/api/ops/tickets': (_, query) => ops.tickets(query),
  '/api/ops/tickets/volumes': (_, query) => ops.volumes(query),
  '/api/ops/tickets/sla': (_, query) => ops.sla(query),
  '/api/ops/tickets/mttr': (_, query) => ops.mttr(query),
  '/api/ops/tickets/backlog': (_, query) => ops.backlog(query),
  '/api/ops/tickets/{ticket_id}': (params) => ops.ticketDetail(params.ticket_id),
  '/api/ops/apps': (_, query) => ops.apps(query),
  '/api/ops/apps/{app_id}': (params, query) => ops.app360(params.app_id, query),
  '/api/ops/costs': (_, query) => ops.costs(query),
  '/api/ops/contracts/renewals': (_, query) => ops.renewals(query),
  '/api/ops/licenses/utilization': (_, query) => ops.licenses(query),
  '/api/ops/vendors/sla-trend': (_, query) => ops.vendorTrend(query),
  '/api/sap/overview': () => sap.overview(),
  '/api/sap/l3': (_, query) => sap.l3(query),
  '/api/sap/changes': (_, query) => sap.changes(query),
  '/api/sap/idocs': (_, query) => sap.idocs(query),
  '/api/delivery/portfolio': () => delivery.portfolio(),
  '/api/delivery/projects/{project_id}': (params) => delivery.project(params.project_id),
  '/api/sources': () => sources.sources(),
  '/api/layouts': (_, query) => core.layouts(String(query?.table ?? '')),
};

const POST_HANDLERS: PostHandlers = {
  '/api/aliases': (body) => core.createAlias(body),
  '/api/layouts': (body) => core.saveLayout(body),
  '/api/layouts/default': (body) => core.layoutDefault(body.layout_id),
  '/api/layouts/delete': (body) => core.layoutDeleted(body.layout_id),
  '/api/findings/{finding_id}/review': (body, params) => review.reviewFinding(params.finding_id, body),
  '/api/findings/bulk-review': (body) => review.bulkReview(body),
  '/api/runs/{run_id}/verdicts': (body, params) => review.runVerdicts(params.run_id, body),
  '/api/runs/{run_id}/review': (body, params) => review.runReview(params.run_id, body),
  '/api/labels/correct': (body) => review.correctLabel(body),
  '/api/reports/build': (body) => reports.startBuild(body),
  '/api/sources/{connector}/pull': (body, params) => sources.startPull(params.connector, body),
  '/api/imports/upload': () => {
    throw new ApiError(400, 'bad_request', 'Uploads send the file as the request body: use apiUpload');
  },
};

const LATENCY_MS = 150;

function delay(signal: AbortSignal | undefined): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException('Aborted', 'AbortError'));
      return;
    }
    const timer = window.setTimeout(resolve, LATENCY_MS);
    signal?.addEventListener('abort', () => {
      window.clearTimeout(timer);
      reject(new DOMException('Aborted', 'AbortError'));
    });
  });
}

/** Deep copy so pages can never mutate the fixture state by accident. */
function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

export async function fixtureGet(
  path: GetPath,
  params: Record<string, string>,
  query: object,
  signal?: AbortSignal,
): Promise<unknown> {
  await delay(signal);
  if (path === '/api/branding') {
    // Branding is this machine's, not fixture data: the fixtures dev server answers it from the branding folder.
    try {
      const response = await fetch('/api/branding', { signal, cache: 'no-store' });
      if (response.ok) return (await response.json()) as unknown;
    } catch (error) {
      if (isAbortError(error)) throw error;
    }
  }
  const handler = GET_HANDLERS[path] as unknown as ((p: Record<string, string>, q: object) => unknown) | undefined;
  if (!handler) throw new ApiError(404, 'not_found', `No fixture for GET ${path}`);
  try {
    return clone(handler(params, query));
  } catch (error) {
    if (error instanceof ApiError || isAbortError(error)) throw error;
    throw new ApiError(500, 'internal', `Fixture for GET ${path} failed: ${String(error)}`);
  }
}

export async function fixturePost(
  path: PostPath,
  body: unknown,
  params: Record<string, string>,
  signal?: AbortSignal,
): Promise<unknown> {
  await delay(signal);
  const handler = POST_HANDLERS[path] as unknown as ((b: unknown, p: Record<string, string>) => unknown) | undefined;
  if (!handler) throw new ApiError(404, 'not_found', `No fixture for POST ${path}`);
  return clone(handler(clone(body), params));
}

/** Fixture answer for `apiUpload` (the upload body is a file, so it is not one of the typed POST handlers). */
export async function fixtureUpload(name: string, size: number, syntheticOk: boolean, signal?: AbortSignal) {
  await delay(signal);
  return clone(sources.startUpload(name, size, syntheticOk));
}
