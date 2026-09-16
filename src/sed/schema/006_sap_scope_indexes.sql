-- owner: sap
-- 006_sap_scope_indexes.sql: resolve the SAP ticket scope (config/sap/scope.yaml, Scope.resolve) with index lookups.
-- SAP assignment groups already use ix_ticket_group_opened.

-- Tickets marked as SAP by their ServiceNow category.
CREATE INDEX IF NOT EXISTS ix_ticket_category ON ticket (category);

-- Tickets carrying kept custom fields (raw_keep_json), few of all tickets.
CREATE INDEX IF NOT EXISTS ix_ticket_raw_keep ON ticket (ticket_id) WHERE raw_keep_json IS NOT NULL;
