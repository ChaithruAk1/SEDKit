-- owner: delivery
-- 010_delivery.sql: delivery management of new business applications (M7 D1).
-- Projects come from the project register, milestones from plan exports (MS Project or Excel; one row per task per
-- plan status date, so slips stay visible), RAID items from the RAID log. Jira issues and Confluence pages stay in the
-- ops tables (work_item, doc_page) and are linked through the register's Jira project keys and Confluence space.

CREATE TABLE delivery_project (
    project_id        TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    app_raw           TEXT,
    app_id            TEXT,                   -- resolved business application, when the project changes one
    phase             TEXT,                   -- raw phase from the register
    rag_raw           TEXT,                   -- RAG status the project manager reported
    sponsor_pid       TEXT,
    manager_pid       TEXT,
    jira_keys_json    TEXT NOT NULL DEFAULT '[]',
    confluence_space  TEXT,
    start_date        TEXT,
    target_date       TEXT,                   -- target go-live
    budget            REAL,
    currency          TEXT,
    budget_base       REAL,
    is_deleted        INTEGER NOT NULL DEFAULT 0,
    last_batch_id     INTEGER REFERENCES import_batch (batch_id)
);

CREATE TABLE delivery_milestone (
    project_id        TEXT NOT NULL,
    task_id           TEXT NOT NULL,          -- plan task id (unique within the project plan)
    status_date       TEXT NOT NULL,          -- the plan's status date: one row per plan version
    name              TEXT,
    is_milestone      INTEGER NOT NULL DEFAULT 0,
    start_date        TEXT,
    finish_date       TEXT,                   -- current forecast finish
    baseline_finish   TEXT,
    actual_finish     TEXT,
    percent_complete  REAL,
    last_batch_id     INTEGER REFERENCES import_batch (batch_id),
    PRIMARY KEY (project_id, task_id, status_date)
);
CREATE INDEX ix_delivery_milestone_status ON delivery_milestone (project_id, status_date);

CREATE TABLE delivery_raid (
    raid_id           TEXT PRIMARY KEY,
    project_id        TEXT NOT NULL,
    raid_type         TEXT,                   -- risk | assumption | issue | dependency | decision
    title             TEXT,                   -- scrubbed free text
    description       TEXT,                   -- scrubbed free text
    owner_pid         TEXT,
    severity          TEXT,                   -- low | medium | high | critical
    status            TEXT,                   -- open | closed (raw values mapped on read)
    raised_on         TEXT,
    due_date          TEXT,
    closed_on         TEXT,
    is_deleted        INTEGER NOT NULL DEFAULT 0,
    last_batch_id     INTEGER REFERENCES import_batch (batch_id)
);
CREATE INDEX ix_delivery_raid_project ON delivery_raid (project_id, status);
