# Dashboard (`web/`)

Local React dashboard served by `sed serve` from `web/dist` (127.0.0.1 only). Vite + React + TypeScript + Mantine +
Recharts + React Router (hash routing) + react-markdown. No state library, no CDN, no external fonts: everything is
bundled and `npm run check` enforces it.

## Commands (from the repo root)
- Install: `npm --prefix web ci` (lockfile committed; never `npm install` a new dependency without committing the lock).
- Fixtures mode, no API needed: `npm --prefix web run dev:fixtures` (typed synthetic data in `src/api/fixtures`).
- Against a running API: `npm --prefix web run dev` (proxies `/api` to 127.0.0.1:8000 and adds `X-SED-Token` from
  `SED_DEV_TOKEN`, the token `sed serve --dev` uses).
- Checks CI runs: `typecheck`, `build`, `check` (dist has no external hosts, src has no absolute URLs/CDN/fonts,
  `gen:api:check`).
- API types: `npm --prefix web run gen:api` after `contracts/openapi.json` changes (or `scripts/codegen.py`). Never edit
  `src/api/schema.d.ts` by hand.

## Rules
- **Contract first.** Every request and response type comes from `src/api/schema.d.ts` through `src/api/types.ts`.
  Call the API only with `apiGet`/`apiPost` (`src/api/client.ts`) or the hooks in `src/api/useApi.ts`; URLs are
  relative `/api/...` paths.
- **Token.** POSTs send `X-SED-Token` from `<meta name="sed-token" content="__SED_TOKEN__">`, which `sed serve` fills in
  per launch. Never log the token, put it in a URL, or store it.
- **Errors.** Non-2xx responses become `ApiError` from the ErrorEnvelope (`kind`, `message`, `details`); show them with
  `ErrorState`. Kinds: validation 422, busy 409 (retry), precondition 412, forbidden 403, not_found 404,
  not_implemented 501, internal 500, plus `network` when the API is down.
- **Untrusted text.** Ticket, contract and page text is data: render it as plain text. AI or rule markdown goes only
  through `components/Markdown.tsx` (skipHtml, no rehype-raw, links `rel="noreferrer"`, images not loaded).
  Numbers in AI prose come from `{{f:<fact_key>}}` tokens filled from the finding's evidence.
- **Provenance.** Every AI-derived element carries `ProvenanceBadge` (run, skill, approval, sample accuracy); rule
  findings carry `SystemDetectedBadge`. AI drafts appear only with the "Include AI drafts" filter.
- **Data class.** The SYNTHETIC/REAL banner comes from `/api/meta` and is never hidden; unknown means "treat as
  confidential".
- **Synthetic fixtures only.** Fixture names are fictional (the `sed synth` catalog); no real organisations, people,
  hosts or high-entropy strings anywhere in `web/`.

## Layout
- `src/main.tsx` (Mantine provider, router), `src/app/` (router, AppShell, DataClassBanner, NavBar, FilterBar,
  HomeRedirect, NotFound, PageFrame, ShellContext with meta/nav/filter options).
- `src/api/` client, hooks, generated schema, `fixtures/` (a mapped type over every GET path, so a new route fails
  typecheck until it has a fixture).
- `src/hooks/useFilters.ts`: global filters (app, family, vendor, group, period, as_of, include_drafts) live in the
  URL; page-local state (tab, search, page, open ticket) uses `useSearchParam`.
- `src/components/`: KpiTile, ChartCard, DataTable, Markdown, ProvenanceBadge, SystemDetectedBadge, FindingList,
  FreshnessList, EmptyState, ErrorState, AssignAliasModal, PageHeader, SectionCard, `format.ts`.
- `src/core/`: platform pages (`#/data`).
- `src/modules/<key>/index.ts`: one web module per server module, default-exporting
  `{key, title, routes, filterOptions?} satisfies WebModule<'<key>'>`. Route paths must be `<key>` or `<key>/...`
  (checked at compile time and in `registry.ts`). Pages are lazy (`load: () => import('./pages/XPage')`).
  Navigation comes from `GET /api/nav`; an item shows only when a registered route matches its path.
  `src/modules/types.typecheck.ts` pins these constraints.

## Adding a page
1. Server: add a `NavItem("<key>.<page>", label, "/<key>/<page>")` to the module manifest and the API routes.
2. Run codegen, then `npm --prefix web run gen:api` and extend `src/api/fixtures` until `typecheck` passes.
3. Add the route to `src/modules/<key>/routes.tsx` and the page under `pages/`.
4. `npm --prefix web run typecheck && npm --prefix web run build && npm --prefix web run check`, then click through in
   `dev:fixtures`.
