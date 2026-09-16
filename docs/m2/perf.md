# M2 API performance (ws5-api-ops)

Budget (build spec §8): p95 < 1 s for **every GET in `contracts/openapi.json`** (core and ops) with default filters,
and `/api/ops/tickets?q=` < 300 ms, on the scale-1.0 synthetic profile (about 108k tasks).

## How it is measured

- Test: `tests/modules/ops/api/test_api_perf.py` (`@pytest.mark.slow`, excluded from `scripts/ci.py`).
  - On main: `uv run pytest -m slow tests/modules/ops/api/test_api_perf.py -s`
  - In a worktree: `bash C:/Projects/sed-wt/<ws>/scripts/wt.sh python -m pytest -m slow tests/modules/ops/api/test_api_perf.py -s`
  - I4: add `SED_PERF_REQUIRE_ALL=1` so any GET still answering 501 fails the run.
- Profile: `build_ops_profile(scale=1.0, seed=42)` (pinned salt, as-of 2026-09-01) under `SED_PERF_DATA_ROOT`, else
  `<SED_DATA_ROOT>/perf`, else `<tempdir>/sed-perf`, in the subfolder `scale-1.0-seed-42`. It is rebuilt only when its
  marker is missing or different (build: 70 s). Pending migrations run first (`db.migrate`), then every
  `src/sed/schema/pending/*.sql` is applied with `db.split_sql` inside `db.write_tx`.
- Rows: 109,198 tickets (73,952 incidents, 28,424 requests, 6,350 changes, 472 problems), 147,904 task_sla rows,
  60 apps, 141 contracts, 221 licenses, 7,200 cost lines, 3,000 Jira issues, 18 rule findings.
- Every GET operation in `contracts/openapi.json` must have a `PERF_PARAMS` entry (a missing or stale entry fails).
  Path parameters are worst cases: App 360 for the application with the most tickets (`APM1001378`, 19,769 tickets),
  ticket detail for the newest ticket.
- Each endpoint gets 20 sequential requests through the in-process FastAPI client (full stack: middleware, per-request
  query_only connection, settings load, SQL, Pydantic validation, JSON). p95 is nearest-rank over all 20 requests, so
  it includes the endpoint's first ("cold") request. Cold is also reported separately; the process and the OS file
  cache are warm (the profile was just built or reused).
- Ticket search is additionally measured with four queries: planted multi-word text (`interface timeout`), a common
  word (`timeout`, 6,316 hits), a very broad word (`error`, 11,036 hits) and a prefix (`time*`, 9,168 hits).
- Results are written to `<perf root>/perf_results.json`.
- Machine: Windows 11 (10.0.26200), 24 logical CPUs, Python 3.11.9, SQLite 3.45.1. Other M2 worktree agents were
  running concurrently, so absolute numbers carry some noise.

## Results (2026-09-14, `m2/ws5-api-ops`, pending indexes applied, reused profile)

All measured GETs pass; the eight core routes other than `/api/health` still return 501 on this branch (ws4-api-platform
owns them) and were skipped, as the workstream plan allows. The last column is the same harness on an identical profile
without `ops_api_indexes.sql`.

| GET | params | status | p50 ms | p95 ms | cold ms | max ms | budget ms | p95 without pending indexes |
|---|---|---|---:|---:|---:|---:|---:|---:|
| `/api/alias-targets` | kind=app | 501 (ws4 stub) | - | - | - | - | 1000 | - |
| `/api/dq/unmapped` | defaults | 501 (ws4 stub) | - | - | - | - | 1000 | - |
| `/api/findings` | defaults | 501 (ws4 stub) | - | - | - | - | 1000 | - |
| `/api/health` | defaults | 200 | 2.2 | 2.9 | 3.9 | 3.9 | 1000 | 3.9 |
| `/api/imports` | defaults | 501 (ws4 stub) | - | - | - | - | 1000 | - |
| `/api/meta` | defaults | 501 (ws4 stub) | - | - | - | - | 1000 | - |
| `/api/modules` | defaults | 501 (ws4 stub) | - | - | - | - | 1000 | - |
| `/api/nav` | defaults | 501 (ws4 stub) | - | - | - | - | 1000 | - |
| `/api/ops/apps` | defaults | 200 | 70.6 | 106.8 | 106.8 | 112.0 | 1000 | 172.5 |
| `/api/ops/apps/{app_id}` | defaults | 200 | 117.7 | 122.7 | 122.7 | 122.7 | 1000 | 425.4 |
| `/api/ops/attention` | defaults | 200 | 10.6 | 12.9 | 13.4 | 13.4 | 1000 | 81.0 |
| `/api/ops/contracts/renewals` | defaults | 200 | 7.8 | 8.6 | 7.6 | 9.7 | 1000 | 10.6 |
| `/api/ops/costs` | defaults | 200 | 11.5 | 12.9 | 11.9 | 24.8 | 1000 | 15.9 |
| `/api/ops/filters` | defaults | 200 | 5.1 | 6.3 | 5.5 | 14.2 | 1000 | 7.6 |
| `/api/ops/licenses/utilization` | defaults | 200 | 9.9 | 11.4 | 12.7 | 12.7 | 1000 | 13.8 |
| `/api/ops/overview` | defaults | 200 | 24.7 | 26.4 | 25.8 | 26.6 | 1000 | 491.1 |
| `/api/ops/tickets` | defaults | 200 | 13.1 | 14.4 | 13.7 | 14.5 | 1000 | 28.9 |
| `/api/ops/tickets/backlog` | defaults | 200 | 41.5 | 49.0 | 41.6 | 68.0 | 1000 | 208.3 |
| `/api/ops/tickets/mttr` | defaults | 200 | 30.5 | 43.0 | 30.7 | 43.0 | 1000 | 96.9 |
| `/api/ops/tickets/sla` | defaults | 200 | 48.5 | 49.9 | 49.2 | 51.0 | 1000 | 139.8 |
| `/api/ops/tickets/volumes` | defaults | 200 | 14.5 | 15.5 | 15.5 | 15.5 | 1000 | 92.7 |
| `/api/ops/tickets/{ticket_id}` | defaults | 200 | 5.6 | 8.1 | 6.1 | 17.6 | 1000 | 9.4 |
| `/api/ops/vendors/sla-trend` | defaults | 200 | 71.3 | 75.4 | 73.5 | 77.0 | 1000 | 128.6 |
| `/api/runs` | defaults | 501 (ws4 stub) | - | - | - | - | 1000 | - |
| `/api/ops/tickets` | q=interface timeout | 200 | 10.6 | 11.7 | 10.7 | 12.0 | 300 | 12.7 |
| `/api/ops/tickets` | q=timeout | 200 | 44.5 | 54.3 | 46.2 | 56.3 | 300 | 59.5 |
| `/api/ops/tickets` | q=error | 200 | 62.0 | 84.7 | 70.7 | 98.1 | 300 | 71.0 |
| `/api/ops/tickets` | q=time* | 200 | 58.3 | 65.4 | 57.4 | 69.7 | 300 | 71.3 |

The first run on a freshly built profile gave the same picture (slowest: App 360 p95 151.9 ms, apps grid 120.4 ms,
vendor trend 98.7 ms; `q=error` 64.6 ms), and a third run on the final commit had App 360 at 163.0 ms and `q=error` at
66.6 ms. Across the three runs every ops GET stayed below 165 ms p95 and every search below 100 ms p95, so the budget
holds with about 6x headroom; without the pending indexes it still holds (worst: overview 491 ms).

Heavier non-default parameters measured during development on the same data (indexed): 18 monthly volume buckets
91 ms, overview for the largest app 22 ms, backlog at 2026-W30 63 ms, tickets for the largest app 16 ms.

## Index decisions: `src/sed/schema/pending/ops_api_indexes.sql`

SQLite has no `ANALYZE` statistics in SED databases, so for `kind = ? AND <range on another column>` the planner prefers
the equality prefix of `ix_ticket_kind_opened (kind, opened_at)` and walks all 74k incidents. The plans below are the
exact statements the read models execute (captured with `set_trace_callback`), timed as best of 3 on the scale-1.0
database without and with the pending file.

### 1. `ix_ticket_kind_resolved ON ticket (kind, resolved_at)` (kept)

Serves every "resolved in a window" read (SLA, MTTR, vendor SLA trend, apps grid, App 360, overview) and the backlog
candidates (incidents with `resolved_at IS NULL` or `>= at`).

| Statement (route) | without | with |
|---|---:|---:|
| backlog candidates, `CROSS JOIN` on rowid (backlog, overview x2, App 360) | 108-115 ms | 0.1-0.4 ms |
| SLA rows with task_sla breach subquery, 12 weeks (sla) | 84.4 ms | 34.5 ms |
| SLA rows, 3 months, all apps (apps) | 94.3 ms | 41.1 ms |
| vendor trend rows, 6 months (vendors/sla-trend) | 90.0 ms | 52.9 ms |
| MTTR hours, 12 weeks (mttr) | 66.4 ms | 17.9 ms |
| volumes, resolved buckets (volumes) | 60.3 ms | 2.9 ms |
| flow, closures per group and week (backlog) | 64.0 ms | 15.7 ms |
| SLA rows, 2 weeks (overview) | 52.9 ms | 2.7 ms |

Plan change, e.g. MTTR: `SEARCH t USING INDEX ix_ticket_kind_opened (kind=? AND opened_at>?)` became
`SEARCH t USING INDEX ix_ticket_kind_resolved (kind=? AND resolved_at>? AND resolved_at<?)`; volumes and the backlog
candidates become `COVERING INDEX` searches. The remaining cost of the SLA reads is the per-incident
`ix_task_sla_ticket` lookup for `MAX(has_breached)`, kept because it mirrors `metrics.sla` exactly.

### 2. `ix_ticket_kind_stale_open ON ticket (kind, stale_open, is_open, opened_at)` (kept)

One index for the stale-open count (`kind = ? AND stale_open = 1`, `metrics.stale_open_count`) and the Needs-attention
scan (`kind = ? AND is_open = 1 AND stale_open = 0 AND opened_at < ?`, `metrics.attention`), which only touches the
~280 open incidents.

| Statement (route) | without | with |
|---|---:|---:|
| `SELECT COUNT(*) ... stale_open = 1` (overview) | 55.5 ms, `SEARCH ix_ticket_kind_opened (kind=?)` | 0.0 ms, `COVERING INDEX ix_ticket_kind_stale_open (kind=? AND stale_open=?)` |
| attention rows (attention, overview) | 55.8-56.1 ms, `ix_ticket_kind_opened (kind=? AND opened_at<?)` | 0.5 ms, `ix_ticket_kind_stale_open (kind=? AND stale_open=? AND is_open=? AND opened_at<?)` |

The existing `ix_ticket_open (is_open, kind)` is not chosen by the planner for these statements.

### 3. `ix_ticket_opened ON ticket (opened_at)` (kept)

The default ticket list (`ORDER BY t.opened_at DESC, t.rowid DESC LIMIT 50`) went from
`SCAN t USING COVERING INDEX ix_ticket_group_opened | USE TEMP B-TREE FOR ORDER BY` (12.6 ms) to
`SCAN t USING COVERING INDEX ix_ticket_opened` (0.0 ms); the rowid tie-breaker is the implicit last index column.
Filtered lists (`app`, `kind`, `group`) already had matching `(x, opened_at)` indexes.

### Cost

- Build: 120 ms, 135 ms and 116 ms at scale 1.0 (0.4 s for the file, one `write_tx`).
- Size: the database grows from 133 MiB to 143 MiB (+7%).
- Ingest maintains three more ticket indexes; the ticket upsert already maintains seven, so the extra write cost is
  small next to the parsing and FTS maintenance of the import (not separately measured).

### Considered and not done

- `ANALYZE` / `sqlite_stat1`: would fix several plan choices without new indexes, but the pending file may only contain
  `CREATE INDEX IF NOT EXISTS`, and statistics would have to be refreshed after imports.
- `(app_id, resolved_at)`: App 360's resolved-side volume buckets for the largest app read 40 ms through
  `ix_ticket_kind_resolved` plus an app filter; not worth a fourth index at the current headroom.
- `(app_id, is_open, stale_open)` for App 360 open tickets: 3 ms today via `ix_ticket_open`.
- TTL cache keyed by (endpoint, params, `MAX(import_batch.batch_id)`, `meta.ops.rule_findings_as_of`): not implemented. The
  budget is met by the queries themselves, and a cache would hide the cold path this gate is meant to measure.
- FTS: the broad-word search cost (`q=error`, 11k hits) is the external-content join plus sorting all matches by
  `opened_at` for the page and a separate `COUNT(*)`; 62 ms p50, so no FTS-specific structure was added.

## Query shapes that matter (independent of indexes)

- Ticket search selects the page of rowids first (filters, FTS `MATCH`, whitelisted `ORDER BY`, `LIMIT/OFFSET`), then
  joins application, vendor and the current AI label for at most 200 rows.
- Trends read each window once and bucket in SQL (`CASE` on consecutive period bounds, `GROUP BY`) or in Python by
  bisecting ISO-8601 bounds, instead of one query per period as `sed.metrics` does. `tests/modules/ops/api/test_read_models.py`
  proves the numbers equal `metrics.volume_trend`, `sla`, `mttr`, `backlog`, `group_flow`, `vendor_sla_trend` and
  `cost_vs_budget` on the fixture.
- The backlog reads candidates from the `(kind, resolved_at)` side with a `CROSS JOIN` to pin the join order; without the
  pending index this shape is slower than the plain predicate (108 ms instead of about 16 ms), so the index is required
  once it is numbered. It is, at I4.

## I4 result (integration, all routes required)

Done on main after all merges: the index file is migration `004_ops_api_indexes.sql`, and the perf test ran with
`SED_PERF_REQUIRE_ALL=1` (no skips) on the scale-1.0 profile. p95 over 20 requests per endpoint:

| Endpoint | p95 | Cold |
|---|---|---|
| /api/health | 2.9 ms | 3.1 ms |
| /api/meta | 10.1 ms | 14.0 ms |
| /api/nav | 3.6 ms | 2.7 ms |
| /api/modules | 3.6 ms | 2.9 ms |
| /api/findings | 9.3 ms | 9.6 ms |
| /api/imports | 7.7 ms | 7.2 ms |
| /api/dq/unmapped | 6.1 ms | 5.9 ms |
| /api/alias-targets?kind=app | 7.8 ms | 20.2 ms |
| /api/runs | 8.0 ms | 7.2 ms |
| /api/ops/filters | 7.1 ms | 7.0 ms |
| /api/ops/overview | 27.8 ms | 25.4 ms |
| /api/ops/attention | 13.5 ms | 13.5 ms |
| /api/ops/tickets | 19.7 ms | 17.3 ms |
| /api/ops/tickets/volumes | 18.8 ms | 25.1 ms |
| /api/ops/tickets/sla | 69.1 ms | 52.7 ms |
| /api/ops/tickets/mttr | 35.9 ms | 38.1 ms |
| /api/ops/tickets/backlog | 50.4 ms | 45.7 ms |
| /api/ops/tickets/{ticket_id} | 11.6 ms | 7.0 ms |
| /api/ops/apps | 104.5 ms | 106.8 ms |
| /api/ops/apps/{app_id} | 168.0 ms | 165.8 ms |
| /api/ops/costs | 13.6 ms | 12.1 ms |
| /api/ops/contracts/renewals | 10.1 ms | 8.9 ms |
| /api/ops/licenses/utilization | 14.3 ms | 11.0 ms |
| /api/ops/vendors/sla-trend | 105.5 ms | 105.5 ms |
| search `interface timeout` | 14.4 ms | 16.2 ms |
| search `timeout` | 70.1 ms | 61.3 ms |
| search `error` | 94.0 ms | 94.0 ms |
| search `time*` | 72.8 ms | 67.7 ms |

Every GET is well under the 1 s budget and ticket search under 300 ms. A served-app smoke test (`sed serve` on the
109k-ticket synthetic profile, curl) returned 200 for all routes, each under 0.1 s.

## After the I9 review fixes

Same test and profile after the review fixes (per-month budget versions, label run status on ticket rows, the shared
SLA expression in vendor trends). The slowest routes: App 360 172.5 ms, apps grid 131.2 ms, vendor SLA trend
92.3 ms, ticket SLA 77.6 ms; costs rose from 13.6 to 27.0 ms (the budget-version join). Search: 16.7 / 62.1 / 103.1 /
106.3 ms. Every GET stays under 1 s and search under 300 ms.
