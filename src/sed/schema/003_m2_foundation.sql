-- owner: core
-- 003_m2_foundation.sql: AI batch reference map and review samples (generic, no ticket foreign keys), sample size on
-- runs, and snapshot provenance (AI runs, AI-derived facts/tables, data as-of, suppressed findings).

CREATE TABLE ai_batch_item (
    batch_id    TEXT NOT NULL REFERENCES ai_batch (batch_id) ON DELETE CASCADE,
    ref         TEXT NOT NULL,
    item_id     TEXT NOT NULL,
    stage       TEXT NOT NULL,
    input_hash  TEXT NOT NULL,
    PRIMARY KEY (batch_id, ref),
    UNIQUE (batch_id, item_id, stage)
);

CREATE TABLE ai_sample (
    run_id           TEXT NOT NULL REFERENCES ai_run (run_id) ON DELETE CASCADE,
    item_id          TEXT NOT NULL,
    stage            TEXT NOT NULL,
    sample_kind      TEXT NOT NULL CHECK (sample_kind IN ('random', 'lowest_conf')),
    stratum          TEXT NOT NULL,
    weight           REAL NOT NULL,
    verdict          TEXT CHECK (verdict IS NULL OR verdict IN ('correct', 'incorrect')),
    correction_json  TEXT,
    reviewer         TEXT,
    decided_at       TEXT,
    PRIMARY KEY (run_id, item_id, stage, sample_kind)
);

ALTER TABLE ai_run ADD COLUMN sample_n INTEGER;

ALTER TABLE report_snapshot ADD COLUMN provenance_json TEXT NOT NULL DEFAULT '{}';
