-- owner: core
-- A second module can declare its own alias kinds, finding kinds and report keys: those columns lose their CHECK
-- constraints, and sed.modules validates the values against what the installed modules declare. SQLite cannot drop a
-- CHECK, so each table is rebuilt (create new, copy, drop, rename) with its indexes and views. Foreign keys are off
-- during migrations, so child rows (ai_cluster_member, report_artifact) keep pointing at the rebuilt tables by name.

CREATE TABLE alias_new (
    kind        TEXT NOT NULL,
    alias_norm  TEXT NOT NULL,
    target_id   TEXT NOT NULL,
    origin      TEXT NOT NULL CHECK (origin IN ('seed', 'manual', 'auto_exact', 'cmdb_rel')),
    created_at  TEXT NOT NULL,
    PRIMARY KEY (kind, alias_norm)
);
INSERT INTO alias_new (kind, alias_norm, target_id, origin, created_at)
SELECT kind, alias_norm, target_id, origin, created_at FROM alias;
DROP TABLE alias;
ALTER TABLE alias_new RENAME TO alias;

-- The published-findings view reads finding, so it is dropped first and recreated unchanged.
DROP VIEW v_findings_published;
CREATE TABLE finding_new (
    finding_id           TEXT PRIMARY KEY,
    run_id               TEXT REFERENCES ai_run (run_id) ON DELETE CASCADE,   -- NULL for rule-origin
    origin               TEXT NOT NULL CHECK (origin IN ('rule', 'ai')),
    stable_key           TEXT NOT NULL,
    kind                 TEXT NOT NULL,
    subject_type         TEXT,
    subject_id           TEXT,
    period               TEXT,
    title                TEXT NOT NULL,
    body_md              TEXT,
    pending_body_md      TEXT,
    severity             TEXT CHECK (severity IS NULL OR severity IN ('low', 'medium', 'high', 'critical')),
    confidence           REAL,
    payload_json         TEXT NOT NULL DEFAULT '{}',
    status               TEXT NOT NULL CHECK (status IN ('active', 'draft', 'approved', 'update_pending', 'rejected',
                                                         'superseded', 'stale_input', 'acknowledged')),
    suppress_until       TEXT,
    carried_forward_from TEXT,
    edited               INTEGER NOT NULL DEFAULT 0,
    created_at           TEXT NOT NULL,
    reviewed_by          TEXT,
    reviewed_at          TEXT,
    review_note          TEXT
);
INSERT INTO finding_new (finding_id, run_id, origin, stable_key, kind, subject_type, subject_id, period, title,
                         body_md, pending_body_md, severity, confidence, payload_json, status, suppress_until,
                         carried_forward_from, edited, created_at, reviewed_by, reviewed_at, review_note)
SELECT finding_id, run_id, origin, stable_key, kind, subject_type, subject_id, period, title,
       body_md, pending_body_md, severity, confidence, payload_json, status, suppress_until,
       carried_forward_from, edited, created_at, reviewed_by, reviewed_at, review_note
FROM finding;
DROP TABLE finding;
ALTER TABLE finding_new RENAME TO finding;
CREATE INDEX ix_finding_stable ON finding (stable_key, status);
CREATE INDEX ix_finding_kind_status ON finding (kind, status);
CREATE INDEX ix_finding_run ON finding (run_id);
-- Idempotent ingest: one AI finding per (run, stable_key); one active rule finding per stable_key.
CREATE UNIQUE INDEX ux_finding_run_stable ON finding (run_id, stable_key) WHERE run_id IS NOT NULL;
CREATE UNIQUE INDEX ux_finding_rule_active ON finding (stable_key) WHERE origin = 'rule' AND status = 'active';

-- Published findings: approved AI findings (update_pending ones keep their approved body_md) and
-- rule-origin findings that are not superseded, acknowledged, or suppressed until a future date.
-- Date filtering against a report's as_of is done in Python; this view uses the wall clock.
CREATE VIEW v_findings_published AS
SELECT f.*
FROM finding AS f
WHERE f.origin = 'ai' AND f.status IN ('approved', 'update_pending')
UNION ALL
SELECT f.*
FROM finding AS f
WHERE f.origin = 'rule'
  AND f.status = 'active'
  AND (f.suppress_until IS NULL OR f.suppress_until < strftime('%Y-%m-%d', 'now'));

CREATE TABLE report_snapshot_new (
    snapshot_id         TEXT PRIMARY KEY,
    report_key          TEXT NOT NULL,
    period              TEXT NOT NULL,
    vendor_id           TEXT,
    as_of               TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    git_commit          TEXT,
    facts_json          TEXT NOT NULL,
    tables_json         TEXT NOT NULL,
    sla_source          TEXT,
    reporting_tz        TEXT NOT NULL,
    freshness_json      TEXT NOT NULL DEFAULT '{}',
    input_batches_json  TEXT NOT NULL DEFAULT '[]',
    data_class          TEXT NOT NULL CHECK (data_class IN ('synthetic', 'real')),
    sha256              TEXT NOT NULL,
    provenance_json     TEXT NOT NULL DEFAULT '{}'
);
INSERT INTO report_snapshot_new (snapshot_id, report_key, period, vendor_id, as_of, created_at, git_commit, facts_json,
                                 tables_json, sla_source, reporting_tz, freshness_json, input_batches_json, data_class,
                                 sha256, provenance_json)
SELECT snapshot_id, report_key, period, vendor_id, as_of, created_at, git_commit, facts_json,
       tables_json, sla_source, reporting_tz, freshness_json, input_batches_json, data_class,
       sha256, provenance_json
FROM report_snapshot;
DROP TABLE report_snapshot;
ALTER TABLE report_snapshot_new RENAME TO report_snapshot;
CREATE INDEX ix_snapshot_report_period ON report_snapshot (report_key, period, vendor_id, created_at);

-- Rule findings now keep one refresh state per module; the only state written so far is the ops one. Newer code may
-- already have written the new key before this migration ran: the later as-of wins.
INSERT INTO meta (key, value, updated_at)
SELECT 'ops.rule_findings_as_of', value, updated_at FROM meta WHERE key = 'rule_findings_as_of'
ON CONFLICT (key) DO UPDATE SET value = MAX(meta.value, excluded.value),
                                updated_at = MAX(meta.updated_at, excluded.updated_at);
DELETE FROM meta WHERE key = 'rule_findings_as_of';
