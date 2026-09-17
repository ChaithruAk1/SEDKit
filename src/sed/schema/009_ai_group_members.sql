-- owner: core
-- 009_ai_group_members.sql: ticket membership of the group work items a finding run analysed (label groups,
-- text candidate groups), frozen at start-run so ingest resolves cluster members deterministically without ever
-- sending ticket ids to an agent.

CREATE TABLE ai_group_member (
    run_id     TEXT NOT NULL REFERENCES ai_run (run_id) ON DELETE CASCADE,
    item_id    TEXT NOT NULL,
    ticket_id  TEXT NOT NULL REFERENCES ticket (ticket_id) ON DELETE CASCADE,
    PRIMARY KEY (run_id, item_id, ticket_id)
);
CREATE INDEX ix_ai_group_member_ticket ON ai_group_member (ticket_id);
