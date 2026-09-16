-- owner: sap
-- 008_sap_idocs.sql: SAP IDocs from the IDoc monitor export and their status history.
-- Raw status codes only: status groups and areas are derived on read from config/sap/idoc.yaml.

CREATE TABLE sap_idoc (
    system_id       TEXT NOT NULL,
    docnum          TEXT NOT NULL,          -- IDoc number
    direction       TEXT,                   -- inbound | outbound
    message_type    TEXT,
    basic_type      TEXT,
    partner_type    TEXT,
    partner_number  TEXT,
    status_code     TEXT,
    status_text     TEXT,                   -- scrubbed and truncated free text
    created_at      TEXT,
    status_at       TEXT,
    last_batch_id   INTEGER REFERENCES import_batch (batch_id),
    PRIMARY KEY (system_id, docnum)
);
CREATE INDEX ix_sap_idoc_created ON sap_idoc (created_at);

-- One row each time an import shows an IDoc with a different status code than the last one recorded.
CREATE TABLE sap_idoc_status (
    system_id    TEXT NOT NULL,
    docnum       TEXT NOT NULL,
    status_at    TEXT NOT NULL,
    status_code  TEXT NOT NULL,
    status_text  TEXT,
    batch_id     INTEGER REFERENCES import_batch (batch_id),
    PRIMARY KEY (system_id, docnum, status_at, status_code),
    FOREIGN KEY (system_id, docnum) REFERENCES sap_idoc (system_id, docnum)
);
CREATE INDEX ix_sap_idoc_status_code ON sap_idoc_status (status_code, system_id, docnum);
