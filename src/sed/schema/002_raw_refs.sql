-- 002_raw_refs.sql: keep the raw reference values imported from registers so `sed import reresolve`
-- can re-link rows after an alias is assigned, without re-importing the source files.

ALTER TABLE application ADD COLUMN vendor_raw TEXT;

ALTER TABLE assignment_group ADD COLUMN vendor_raw TEXT;
ALTER TABLE assignment_group ADD COLUMN app_raw TEXT;
ALTER TABLE assignment_group ADD COLUMN is_deleted INTEGER NOT NULL DEFAULT 0;
ALTER TABLE assignment_group ADD COLUMN last_batch_id INTEGER REFERENCES import_batch (batch_id);

ALTER TABLE contract ADD COLUMN vendor_raw TEXT;
ALTER TABLE contract ADD COLUMN app_raw TEXT;

ALTER TABLE license ADD COLUMN vendor_raw TEXT;
ALTER TABLE license ADD COLUMN app_raw TEXT;
ALTER TABLE license ADD COLUMN contract_raw TEXT;

ALTER TABLE cost_line ADD COLUMN vendor_raw TEXT;
ALTER TABLE cost_line ADD COLUMN app_raw TEXT;
ALTER TABLE cost_line ADD COLUMN contract_raw TEXT;

ALTER TABLE work_item ADD COLUMN app_raw TEXT;

ALTER TABLE doc_page ADD COLUMN app_raw TEXT;
ALTER TABLE doc_page ADD COLUMN is_deleted INTEGER NOT NULL DEFAULT 0;

ALTER TABLE unmapped_value ADD COLUMN last_batch_id INTEGER REFERENCES import_batch (batch_id);
ALTER TABLE unmapped_value ADD COLUMN resolved INTEGER NOT NULL DEFAULT 0;

CREATE INDEX ix_cost_line_asof ON cost_line (line_type, as_of);
CREATE INDEX ix_license_usage_asof ON license_usage (as_of_date);
