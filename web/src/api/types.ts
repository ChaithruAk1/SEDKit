/**
 * Type helpers over the generated OpenAPI types (schema.d.ts, never hand-edited).
 *
 * Every request and response type in the app is derived from `paths` / `components` so a contract change in
 * contracts/openapi.json surfaces as a typecheck error after `npm run gen:api`.
 */
import type { components, paths } from './schema';

export type Schemas = components['schemas'];
export type Schema<K extends keyof Schemas> = Schemas[K];

type OperationOf<P extends keyof paths, M extends 'get' | 'post'> = paths[P] extends { [K in M]: infer O } ? O : never;
type JsonOf<R> = R extends { content: { 'application/json': infer T } } ? T : never;

/** API paths that have a GET operation. */
export type GetPath = { [P in keyof paths]: paths[P] extends { get: object } ? P : never }[keyof paths];
/** API paths that have a POST operation. */
export type PostPath = { [P in keyof paths]: paths[P] extends { post: object } ? P : never }[keyof paths];

export type GetResponse<P extends GetPath> =
  OperationOf<P, 'get'> extends { responses: { 200: infer R } } ? JsonOf<R> : never;
export type GetQuery<P extends GetPath> =
  OperationOf<P, 'get'> extends { parameters: { query?: infer Q } } ? ([Q] extends [undefined] ? never : Q) : never;
export type GetPathParams<P extends GetPath> =
  OperationOf<P, 'get'> extends { parameters: { path: infer X } } ? X : never;

export type PostBody<P extends PostPath> =
  OperationOf<P, 'post'> extends { requestBody: { content: { 'application/json': infer B } } } ? B : never;
export type PostResponse<P extends PostPath> =
  OperationOf<P, 'post'> extends { responses: { 200: infer R } } ? JsonOf<R> : never;
export type PostPathParams<P extends PostPath> =
  OperationOf<P, 'post'> extends { parameters: { path: infer X } } ? X : never;

/** Options for a GET call: query parameters, plus path parameters when the route has any. */
export type GetOptions<P extends GetPath> = {
  query?: GetQuery<P>;
} & ([GetPathParams<P>] extends [never] ? { params?: undefined } : { params: GetPathParams<P> });

/** Options for a POST call: path parameters when the route has any, and an abort signal. */
export type PostOptions<P extends PostPath> = {
  signal?: AbortSignal;
} & ([PostPathParams<P>] extends [never] ? { params?: undefined } : { params: PostPathParams<P> });

// Frequently used schema aliases.
export type Kpi = Schema<'Kpi'>;
export type FindingOut = Schema<'FindingOut'>;
export type FreshnessRow = Schema<'FreshnessRow'>;
export type FilterOption = Schema<'FilterOption'>;
export type MetaOut = Schema<'MetaOut'>;
export type NavItemOut = Schema<'NavItemOut'>;
export type RunRow = Schema<'RunRow'>;
export type ErrorEnvelope = Schema<'ErrorEnvelope'>;
export type TicketRow = Schema<'TicketRow'>;
export type TicketDetail = Schema<'TicketDetail'>;

/** Query parameters shared by every ops GET (`CommonFilters` on the server). */
export type CommonFilterQuery = Pick<
  NonNullable<GetQuery<'/api/ops/overview'>>,
  'app' | 'family' | 'vendor' | 'group' | 'period' | 'as_of' | 'include_drafts'
>;
