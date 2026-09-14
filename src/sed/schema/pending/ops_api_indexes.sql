-- owner: ops
-- ops_api_indexes.sql (pending; the integrator numbers it at merge): read indexes for the /api/ops dashboard routes.
-- Measured query plans and timings on the scale-1.0 synthetic profile are in docs/m2/perf.md.

-- Incidents resolved in a window (SLA, MTTR, vendor SLA trend, apps grid, overview) and the backlog candidates
-- (resolved_at NULL or after the backlog moment). Without it the planner walks every incident via ix_ticket_kind_opened.
CREATE INDEX IF NOT EXISTS ix_ticket_kind_resolved ON ticket (kind, resolved_at);

-- Stale-open counts and the Needs-attention scan (kind, stale_open = 0, is_open = 1, opened_at < as-of).
CREATE INDEX IF NOT EXISTS ix_ticket_kind_stale_open ON ticket (kind, stale_open, is_open, opened_at);

-- Ticket search without full-text: newest-first pages read the index in order instead of sorting every ticket.
CREATE INDEX IF NOT EXISTS ix_ticket_opened ON ticket (opened_at);
