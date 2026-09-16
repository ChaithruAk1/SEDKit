-- owner: sap
-- 007_sap_changes.sql: SAP Solution Manager ChaRM change documents, their status history and transport imports.
-- Raw values only: change type, stage, area, landscape and system roles are derived on read from config/sap/.

CREATE TABLE sap_change (
    change_id           TEXT PRIMARY KEY,      -- ChaRM document id
    transaction_type    TEXT,
    title               TEXT,                  -- scrubbed free text
    status_raw          TEXT,
    priority            TEXT,
    component_raw       TEXT,
    cycle_raw           TEXT,                  -- change cycle / release
    created_at          TEXT,
    changed_at          TEXT,
    requester_pid       TEXT,
    developer_pid       TEXT,
    change_manager_pid  TEXT,
    external_ref        TEXT,                  -- external reference as exported (for example a Jira key)
    ticket_ref          TEXT,                  -- ServiceNow ticket number
    last_batch_id       INTEGER REFERENCES import_batch (batch_id)
);
CREATE INDEX ix_sap_change_created ON sap_change (created_at);

-- One row each time an import shows a change with a different status than the last recorded one.
CREATE TABLE sap_change_status (
    change_id   TEXT NOT NULL REFERENCES sap_change (change_id),
    seen_at     TEXT NOT NULL,                 -- changed_at of the first export with this status
    status_raw  TEXT NOT NULL,
    batch_id    INTEGER REFERENCES import_batch (batch_id),
    PRIMARY KEY (change_id, seen_at, status_raw)
);

-- Latest import of each transport into each system.
CREATE TABLE sap_transport_import (
    transport       TEXT NOT NULL,
    system_id       TEXT NOT NULL,
    change_id       TEXT,
    transport_type  TEXT,
    description     TEXT,                      -- scrubbed free text
    owner_pid       TEXT,
    released_at     TEXT,
    import_status   TEXT,
    return_code     INTEGER,
    imported_at     TEXT,
    last_batch_id   INTEGER REFERENCES import_batch (batch_id),
    PRIMARY KEY (transport, system_id)
);
CREATE INDEX ix_sap_transport_change ON sap_transport_import (change_id);
CREATE INDEX ix_sap_transport_system ON sap_transport_import (system_id, imported_at);
