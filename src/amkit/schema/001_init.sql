-- 001_init.sql: canonical schema v1 (plan Appendix A5).
-- Conventions: timestamps are ISO-8601 UTC text; money = amount + currency + amount_base (base currency);
-- *_pid columns hold pseudonyms (P-xxxxxxxxxx), never real names; booleans are 0/1 integers.

-- ---------------------------------------------------------------------------
-- Meta and ingest provenance
-- ---------------------------------------------------------------------------

CREATE TABLE meta (
    key         TEXT PRIMARY KEY,
    value       TEXT,
    updated_at  TEXT NOT NULL
);

CREATE TABLE import_batch (
    batch_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    file_name         TEXT NOT NULL,
    file_sha256       TEXT NOT NULL UNIQUE,
    mapping_name      TEXT NOT NULL,
    mapping_sha256    TEXT NOT NULL,
    load_mode         TEXT NOT NULL CHECK (load_mode IN ('delta', 'full_snapshot', 'append_snapshot', 'active_snapshot')),
    as_of             TEXT,
    status            TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'completed', 'failed')),
    rows_read         INTEGER NOT NULL DEFAULT 0,
    rows_inserted     INTEGER NOT NULL DEFAULT 0,
    rows_updated      INTEGER NOT NULL DEFAULT 0,
    rows_unchanged    INTEGER NOT NULL DEFAULT 0,
    rows_rejected     INTEGER NOT NULL DEFAULT 0,
    rows_soft_deleted INTEGER NOT NULL DEFAULT 0,
    dq_json           TEXT NOT NULL DEFAULT '{}',
    imported_at       TEXT NOT NULL
);

CREATE TABLE row_reject (
    reject_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id              INTEGER NOT NULL REFERENCES import_batch (batch_id) ON DELETE CASCADE,
    row_num               INTEGER NOT NULL,
    reason                TEXT NOT NULL,
    scrubbed_payload_json TEXT
);
CREATE INDEX ix_row_reject_batch ON row_reject (batch_id);

CREATE TABLE alias (
    kind        TEXT NOT NULL CHECK (kind IN ('app', 'vendor', 'group', 'ci', 'jira_project', 'jira_component', 'confluence_space')),
    alias_norm  TEXT NOT NULL,
    target_id   TEXT NOT NULL,
    origin      TEXT NOT NULL CHECK (origin IN ('seed', 'manual', 'auto_exact', 'cmdb_rel')),
    created_at  TEXT NOT NULL,
    PRIMARY KEY (kind, alias_norm)
);

CREATE TABLE unmapped_value (
    kind            TEXT NOT NULL,
    raw_value       TEXT NOT NULL,
    occurrences     INTEGER NOT NULL DEFAULT 0,
    first_batch_id  INTEGER REFERENCES import_batch (batch_id),
    suggestion      TEXT,
    score           REAL,
    PRIMARY KEY (kind, raw_value)
);

-- Salted name hashes for free-text name scrubbing (no raw names stored).
CREATE TABLE person_key (
    name_hash   TEXT PRIMARY KEY,
    pid         TEXT NOT NULL
);

-- Local-only display names; populated only when settings.display_names = true on the real profile.
CREATE TABLE person_display (
    pid           TEXT PRIMARY KEY,
    display_name  TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- Master, cost and license data
-- ---------------------------------------------------------------------------

CREATE TABLE vendor (
    vendor_id       TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    vendor_type     TEXT,
    tier            TEXT,
    sla_target_pct  REAL,
    is_deleted      INTEGER NOT NULL DEFAULT 0,
    last_batch_id   INTEGER REFERENCES import_batch (batch_id)
);

CREATE TABLE application (
    app_id                TEXT PRIMARY KEY,
    name                  TEXT NOT NULL,
    app_family            TEXT,
    business_criticality  TEXT,
    life_cycle_stage      TEXT,
    it_owner_pid          TEXT,
    cost_center           TEXT,
    primary_vendor_id     TEXT REFERENCES vendor (vendor_id),
    is_deleted            INTEGER NOT NULL DEFAULT 0,
    last_batch_id         INTEGER REFERENCES import_batch (batch_id)
);

CREATE TABLE assignment_group (
    name        TEXT PRIMARY KEY,
    vendor_id   TEXT REFERENCES vendor (vendor_id),
    app_id      TEXT REFERENCES application (app_id)
);

CREATE TABLE contract (
    contract_id         TEXT PRIMARY KEY,
    contract_number     TEXT,
    vendor_id           TEXT REFERENCES vendor (vendor_id),
    app_id              TEXT REFERENCES application (app_id),
    product             TEXT,
    start_date          TEXT,
    end_date            TEXT,
    notice_period_days  INTEGER,
    notice_deadline     TEXT,
    auto_renew          INTEGER,
    renewal_status      TEXT,
    annual_value        REAL,
    currency            TEXT,
    annual_value_base   REAL,
    owner_pid           TEXT,
    comments_scrubbed   TEXT,
    is_deleted          INTEGER NOT NULL DEFAULT 0,
    last_batch_id       INTEGER REFERENCES import_batch (batch_id)
);
CREATE INDEX ix_contract_end ON contract (end_date);
CREATE INDEX ix_contract_vendor ON contract (vendor_id);

CREATE TABLE license (
    license_id      TEXT PRIMARY KEY,
    app_id          TEXT REFERENCES application (app_id),
    vendor_id       TEXT REFERENCES vendor (vendor_id),
    contract_id     TEXT REFERENCES contract (contract_id),
    product         TEXT,
    license_metric  TEXT,
    entitled_qty    REAL,
    unit_cost_base  REAL,
    is_deleted      INTEGER NOT NULL DEFAULT 0,
    last_batch_id   INTEGER REFERENCES import_batch (batch_id)
);

CREATE TABLE license_usage (
    license_id      TEXT NOT NULL REFERENCES license (license_id),
    as_of_date      TEXT NOT NULL,
    assigned_qty    REAL,
    active_qty_90d  REAL,
    last_batch_id   INTEGER REFERENCES import_batch (batch_id),
    PRIMARY KEY (license_id, as_of_date)
);

CREATE TABLE cost_line (
    cost_line_id    TEXT PRIMARY KEY,           -- hash of the natural key
    app_id          TEXT REFERENCES application (app_id),
    vendor_id       TEXT REFERENCES vendor (vendor_id),
    contract_id     TEXT REFERENCES contract (contract_id),
    period          TEXT NOT NULL,              -- YYYY-MM
    line_type       TEXT NOT NULL CHECK (line_type IN ('actual', 'budget', 'forecast')),
    cost_category   TEXT,
    amount          REAL NOT NULL,
    currency        TEXT NOT NULL,
    amount_base     REAL,
    cost_center     TEXT,
    as_of           TEXT,                       -- budget/forecast version
    last_batch_id   INTEGER REFERENCES import_batch (batch_id)
);
CREATE INDEX ix_cost_line_app_period ON cost_line (app_id, period);
CREATE INDEX ix_cost_line_vendor_period ON cost_line (vendor_id, period);

-- ---------------------------------------------------------------------------
-- Work data
-- ---------------------------------------------------------------------------

CREATE TABLE ticket (
    ticket_id             TEXT PRIMARY KEY,     -- '<table>:<number>', e.g. incident:INC0012345
    kind                  TEXT NOT NULL CHECK (kind IN ('incident', 'sc_req_item', 'change_request', 'problem')),
    number                TEXT NOT NULL,
    sys_id                TEXT,
    app_id                TEXT REFERENCES application (app_id),
    cmdb_ci_raw           TEXT,
    business_service_raw  TEXT,
    short_description     TEXT,
    description           TEXT,
    close_notes           TEXT,
    category              TEXT,                 -- ServiceNow category, kept as exported
    subcategory           TEXT,
    priority              INTEGER,
    impact                INTEGER,
    urgency               INTEGER,
    state                 TEXT,
    is_open               INTEGER NOT NULL DEFAULT 1,
    assignment_group      TEXT,
    assigned_to_pid       TEXT,
    caller_pid            TEXT,
    vendor_id             TEXT REFERENCES vendor (vendor_id),
    opened_at             TEXT,
    resolved_at           TEXT,
    closed_at             TEXT,
    sys_updated_on        TEXT NOT NULL,
    made_sla              INTEGER,
    reassignment_count    INTEGER,
    reopen_count          INTEGER,
    close_code            TEXT,
    problem_id            TEXT,
    caused_by             TEXT,                 -- change number that caused the incident
    parent_incident       TEXT,
    change_type           TEXT,
    risk                  TEXT,
    start_date            TEXT,
    end_date              TEXT,
    open_hash             TEXT,
    resolved_hash         TEXT,
    stale_open            INTEGER NOT NULL DEFAULT 0,
    raw_keep_json         TEXT,
    last_batch_id         INTEGER REFERENCES import_batch (batch_id),
    UNIQUE (kind, number)
);
CREATE INDEX ix_ticket_kind_opened ON ticket (kind, opened_at);
CREATE INDEX ix_ticket_app_opened ON ticket (app_id, opened_at);
CREATE INDEX ix_ticket_resolved ON ticket (resolved_at);
CREATE INDEX ix_ticket_group_opened ON ticket (assignment_group, opened_at);
CREATE INDEX ix_ticket_updated ON ticket (sys_updated_on);
CREATE INDEX ix_ticket_open ON ticket (is_open, kind);

CREATE VIRTUAL TABLE ticket_fts USING fts5 (
    short_description,
    description,
    content = 'ticket',
    content_rowid = 'rowid',
    tokenize = 'unicode61 remove_diacritics 2'
);

CREATE TRIGGER trg_ticket_fts_ai AFTER INSERT ON ticket BEGIN
    INSERT INTO ticket_fts (rowid, short_description, description)
    VALUES (new.rowid, new.short_description, new.description);
END;

CREATE TRIGGER trg_ticket_fts_ad AFTER DELETE ON ticket BEGIN
    INSERT INTO ticket_fts (ticket_fts, rowid, short_description, description)
    VALUES ('delete', old.rowid, old.short_description, old.description);
END;

CREATE TRIGGER trg_ticket_fts_au AFTER UPDATE OF short_description, description ON ticket BEGIN
    INSERT INTO ticket_fts (ticket_fts, rowid, short_description, description)
    VALUES ('delete', old.rowid, old.short_description, old.description);
    INSERT INTO ticket_fts (rowid, short_description, description)
    VALUES (new.rowid, new.short_description, new.description);
END;

CREATE TABLE task_sla (
    task_sla_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id            TEXT NOT NULL REFERENCES ticket (ticket_id) ON DELETE CASCADE,
    sla_name             TEXT NOT NULL,
    sla_type             TEXT CHECK (sla_type IN ('response', 'resolution', 'other')),
    stage                TEXT,
    has_breached         INTEGER,
    start_time           TEXT NOT NULL DEFAULT '',   -- '' when the export has no start time (NULLs would defeat UNIQUE)
    end_time             TEXT,
    business_duration_s  INTEGER,
    sla_sys_id           TEXT UNIQUE,                -- preferred identity when exported
    last_batch_id        INTEGER REFERENCES import_batch (batch_id),
    UNIQUE (ticket_id, sla_name, start_time)
);
CREATE INDEX ix_task_sla_ticket ON task_sla (ticket_id);

CREATE TABLE ci_rel (
    parent_ci      TEXT NOT NULL,
    child_ci       TEXT NOT NULL,
    type           TEXT NOT NULL,
    is_deleted     INTEGER NOT NULL DEFAULT 0,
    last_batch_id  INTEGER REFERENCES import_batch (batch_id),
    PRIMARY KEY (parent_ci, child_ci, type)
);

CREATE TABLE work_item (
    issue_key          TEXT PRIMARY KEY,
    project_key        TEXT NOT NULL,
    app_id             TEXT REFERENCES application (app_id),
    summary            TEXT,
    issue_type         TEXT,
    status             TEXT,
    status_category    TEXT,
    priority           TEXT,
    created            TEXT,
    updated            TEXT,
    resolved           TEXT,
    sprint_json        TEXT,
    labels_json        TEXT,
    components_json    TEXT,
    fix_versions_json  TEXT,
    parent_key         TEXT,
    story_points       REAL,
    assignee_pid       TEXT,
    last_batch_id      INTEGER REFERENCES import_batch (batch_id)
);
CREATE INDEX ix_work_item_app ON work_item (app_id, resolved);

CREATE TABLE doc_page (
    page_id             TEXT PRIMARY KEY,
    space_key           TEXT NOT NULL,
    app_id              TEXT REFERENCES application (app_id),
    title               TEXT NOT NULL,
    labels_json         TEXT,
    page_type           TEXT NOT NULL DEFAULT 'other' CHECK (page_type IN ('kb', 'runbook', 'release_note', 'roadmap', 'other')),
    last_updated        TEXT,
    body_text_scrubbed  TEXT,
    source_batch_id     INTEGER REFERENCES import_batch (batch_id)
);
CREATE INDEX ix_doc_page_app ON doc_page (app_id, page_type);

-- ---------------------------------------------------------------------------
-- AI enrichment and provenance (never overwrites canonical columns)
-- ---------------------------------------------------------------------------

CREATE TABLE ai_run (
    run_seq               INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                TEXT NOT NULL UNIQUE,
    skill                 TEXT NOT NULL,
    skill_hash            TEXT NOT NULL,
    schema_version        INTEGER NOT NULL,
    git_commit            TEXT,
    model_arg             TEXT,
    model_reported        TEXT,
    claude_version        TEXT,
    invoked_via           TEXT NOT NULL CHECK (invoked_via IN ('interactive', 'workflow', 'headless', 'manual')),
    profile               TEXT NOT NULL,
    params_json           TEXT NOT NULL DEFAULT '{}',
    input_manifest_sha    TEXT,
    input_run_ids_json    TEXT NOT NULL DEFAULT '[]',
    status                TEXT NOT NULL DEFAULT 'running'
                          CHECK (status IN ('running', 'completed', 'failed', 'approved', 'rejected')),
    counts_json           TEXT NOT NULL DEFAULT '{}',
    sample_accuracy       REAL,
    sample_ci_low         REAL,
    sample_ci_high        REAL,
    lowest_conf_error_rate REAL,
    started_at            TEXT NOT NULL,
    finished_at           TEXT,
    reviewed_by           TEXT,
    reviewed_at           TEXT,
    review_note           TEXT
);
CREATE INDEX ix_ai_run_skill_status ON ai_run (skill, status);

CREATE TABLE ai_batch (
    batch_id          TEXT PRIMARY KEY,         -- '<run_id>/batch_0007'
    run_id            TEXT NOT NULL REFERENCES ai_run (run_id) ON DELETE CASCADE,
    seq               INTEGER NOT NULL,
    packet_path       TEXT NOT NULL,
    packet_sha        TEXT NOT NULL,
    item_count        INTEGER NOT NULL,
    output_sha        TEXT,
    status            TEXT NOT NULL DEFAULT 'planned' CHECK (status IN ('planned', 'ingested', 'failed', 'split')),
    attempts          INTEGER NOT NULL DEFAULT 0,
    last_errors_json  TEXT,
    UNIQUE (run_id, seq)
);

CREATE TABLE ai_claim (
    ticket_id         TEXT NOT NULL REFERENCES ticket (ticket_id) ON DELETE CASCADE,
    stage             TEXT NOT NULL CHECK (stage IN ('open', 'resolved')),
    run_id            TEXT NOT NULL REFERENCES ai_run (run_id) ON DELETE CASCADE,
    lease_expires_at  TEXT NOT NULL,
    PRIMARY KEY (ticket_id, stage)
);

CREATE TABLE ai_ticket_label (
    ticket_id       TEXT NOT NULL REFERENCES ticket (ticket_id) ON DELETE CASCADE,
    stage           TEXT NOT NULL CHECK (stage IN ('open', 'resolved')),
    run_id          TEXT NOT NULL REFERENCES ai_run (run_id) ON DELETE CASCADE,
    batch_id        TEXT,
    input_hash      TEXT NOT NULL,
    am_category     TEXT NOT NULL,
    am_subcategory  TEXT,
    symptom_key     TEXT,
    misfiled_as     TEXT,
    confidence      REAL CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    rationale       TEXT,
    created_at      TEXT NOT NULL,
    PRIMARY KEY (ticket_id, stage, run_id)
);
CREATE INDEX ix_label_run ON ai_ticket_label (run_id);

CREATE TABLE symptom_key_alias (
    app_id    TEXT NOT NULL,
    from_key  TEXT NOT NULL,
    to_key    TEXT NOT NULL,
    run_id    TEXT NOT NULL REFERENCES ai_run (run_id) ON DELETE CASCADE,
    status    TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'approved', 'rejected')),
    PRIMARY KEY (app_id, from_key, run_id)
);

CREATE TABLE finding (
    finding_id           TEXT PRIMARY KEY,
    run_id               TEXT REFERENCES ai_run (run_id) ON DELETE CASCADE,   -- NULL for rule-origin
    origin               TEXT NOT NULL CHECK (origin IN ('rule', 'ai')),
    stable_key           TEXT NOT NULL,
    kind                 TEXT NOT NULL CHECK (kind IN ('issue_cluster', 'renewal_risk', 'license_risk', 'vendor_risk',
                                                       'cost_risk', 'rationalization', 'report_section')),
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
CREATE INDEX ix_finding_stable ON finding (stable_key, status);
CREATE INDEX ix_finding_kind_status ON finding (kind, status);
CREATE INDEX ix_finding_run ON finding (run_id);
-- Idempotent ingest: one AI finding per (run, stable_key); one active rule finding per stable_key.
CREATE UNIQUE INDEX ux_finding_run_stable ON finding (run_id, stable_key) WHERE run_id IS NOT NULL;
CREATE UNIQUE INDEX ux_finding_rule_active ON finding (stable_key) WHERE origin = 'rule' AND status = 'active';

CREATE TABLE ai_cluster_member (
    finding_id  TEXT NOT NULL REFERENCES finding (finding_id) ON DELETE CASCADE,
    ticket_id   TEXT NOT NULL REFERENCES ticket (ticket_id) ON DELETE CASCADE,
    PRIMARY KEY (finding_id, ticket_id)
);
CREATE INDEX ix_cluster_member_ticket ON ai_cluster_member (ticket_id);

CREATE TABLE review_decision (
    decision_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    target_type   TEXT NOT NULL CHECK (target_type IN ('finding', 'run', 'label', 'alias', 'symptom_key_alias')),
    target_id     TEXT NOT NULL,
    decision      TEXT NOT NULL CHECK (decision IN ('approve', 'reject', 'edit', 'approve_run', 'reject_run',
                                                    'correct_label', 'approve_update', 'acknowledge', 'suppress_until')),
    payload_json  TEXT NOT NULL DEFAULT '{}',
    reviewer      TEXT NOT NULL,
    decided_at    TEXT NOT NULL
);
CREATE INDEX ix_review_target ON review_decision (target_type, target_id);

CREATE TRIGGER trg_review_decision_no_update BEFORE UPDATE ON review_decision BEGIN
    SELECT RAISE (ABORT, 'review_decision is append-only');
END;

CREATE TRIGGER trg_review_decision_no_delete BEFORE DELETE ON review_decision BEGIN
    SELECT RAISE (ABORT, 'review_decision is append-only');
END;

-- ---------------------------------------------------------------------------
-- Reports and jobs
-- ---------------------------------------------------------------------------

CREATE TABLE report_snapshot (
    snapshot_id         TEXT PRIMARY KEY,
    report_key          TEXT NOT NULL CHECK (report_key IN ('weekly', 'monthly', 'quarterly', 'vendor', 'data-pack')),
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
    sha256              TEXT NOT NULL
);
CREATE INDEX ix_snapshot_report_period ON report_snapshot (report_key, period, vendor_id, created_at);

CREATE TABLE report_artifact (
    artifact_id              TEXT PRIMARY KEY,
    snapshot_id              TEXT NOT NULL REFERENCES report_snapshot (snapshot_id),
    format                   TEXT NOT NULL CHECK (format IN ('pptx', 'xlsx', 'md')),
    path                     TEXT NOT NULL,
    sha256                   TEXT NOT NULL,
    template_map_sha         TEXT,
    ai_mode                  TEXT NOT NULL CHECK (ai_mode IN ('approved', 'none', 'draft')),
    ai_run_ids_json          TEXT NOT NULL DEFAULT '[]',
    unapproved_omitted_json  TEXT NOT NULL DEFAULT '[]',
    built_at                 TEXT NOT NULL
);

CREATE TABLE job (
    job_id       TEXT PRIMARY KEY,
    kind         TEXT NOT NULL,
    params_json  TEXT NOT NULL DEFAULT '{}',
    status       TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
    pid          INTEGER,
    created_at   TEXT NOT NULL,
    started_at   TEXT,
    finished_at  TEXT,
    result_json  TEXT,
    error        TEXT
);

-- ---------------------------------------------------------------------------
-- Views
-- ---------------------------------------------------------------------------

-- Current label per (ticket, stage): manual corrections win, then the latest approved run, only when the
-- label was computed on the ticket's current content hash for that stage. Implemented as an index-driven
-- correlated subquery on the ai_ticket_label primary key (no window over all label history).
CREATE VIEW v_label_current AS
SELECT l.ticket_id, l.stage, l.run_id, l.batch_id, l.input_hash, l.am_category, l.am_subcategory, l.symptom_key,
       l.misfiled_as, l.confidence, l.rationale, l.created_at, r.run_seq
FROM ticket AS t
JOIN ai_ticket_label AS l ON l.rowid = (
    SELECT l2.rowid
    FROM ai_ticket_label AS l2
    JOIN ai_run AS r2 ON r2.run_id = l2.run_id AND r2.status = 'approved'
    WHERE l2.ticket_id = t.ticket_id AND l2.stage = 'open' AND l2.input_hash = t.open_hash
    ORDER BY CASE WHEN r2.skill = 'manual' THEN 0 ELSE 1 END, r2.run_seq DESC
    LIMIT 1
)
JOIN ai_run AS r ON r.run_id = l.run_id
UNION ALL
SELECT l.ticket_id, l.stage, l.run_id, l.batch_id, l.input_hash, l.am_category, l.am_subcategory, l.symptom_key,
       l.misfiled_as, l.confidence, l.rationale, l.created_at, r.run_seq
FROM ticket AS t
JOIN ai_ticket_label AS l ON l.rowid = (
    SELECT l2.rowid
    FROM ai_ticket_label AS l2
    JOIN ai_run AS r2 ON r2.run_id = l2.run_id AND r2.status = 'approved'
    WHERE l2.ticket_id = t.ticket_id AND l2.stage = 'resolved' AND l2.input_hash = t.resolved_hash
    ORDER BY CASE WHEN r2.skill = 'manual' THEN 0 ELSE 1 END, r2.run_seq DESC
    LIMIT 1
)
JOIN ai_run AS r ON r.run_id = l.run_id;

-- Ticket with app/vendor names and ONE current label row (resolved stage preferred over open stage).
-- All label columns come from the same row, so values from different runs are never mixed.
CREATE VIEW v_ticket AS
SELECT
    t.*,
    a.name AS app_name,
    a.app_family,
    a.business_criticality,
    v.name AS vendor_name,
    cl.stage AS label_stage,
    cl.am_category,
    cl.am_subcategory,
    cl.symptom_key,
    cl.misfiled_as,
    cl.confidence AS label_confidence,
    cl.run_id AS label_run_id
FROM ticket AS t
LEFT JOIN application AS a ON a.app_id = t.app_id
LEFT JOIN vendor AS v ON v.vendor_id = t.vendor_id
LEFT JOIN ai_ticket_label AS cl ON cl.rowid = (
    SELECT l2.rowid
    FROM ai_ticket_label AS l2
    JOIN ai_run AS r2 ON r2.run_id = l2.run_id AND r2.status = 'approved'
    WHERE l2.ticket_id = t.ticket_id
      AND l2.input_hash = CASE l2.stage WHEN 'open' THEN t.open_hash ELSE t.resolved_hash END
    ORDER BY CASE l2.stage WHEN 'resolved' THEN 0 ELSE 1 END,
             CASE WHEN r2.skill = 'manual' THEN 0 ELSE 1 END,
             r2.run_seq DESC
    LIMIT 1
);

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
