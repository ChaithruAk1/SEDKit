-- owner: core
-- 011_table_layouts.sql: named column layouts for the dashboard's tables.
--
-- A real export carries far more columns than a table can show at once, and which ones matter depends on the job in
-- hand: a weekly review and a vendor meeting want different sets. A layout names one set so it can be chosen again
-- rather than rebuilt. They live in the store, not the browser, so they survive a new browser or a reinstall.
--
-- `columns_json` is an ordered list of column keys; order is the display order. A key the table no longer offers is
-- ignored on read, so a layout outlives a renamed column instead of breaking.

CREATE TABLE table_layout (
    layout_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    table_key    TEXT NOT NULL,                        -- which table, e.g. 'ops.tickets'
    name         TEXT NOT NULL,
    columns_json TEXT NOT NULL,                        -- ordered visible column keys
    is_default   INTEGER NOT NULL DEFAULT 0 CHECK (is_default IN (0, 1)),
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    UNIQUE (table_key, name)
);

-- One default per table at most: a partial index, so the rows that are not the default do not collide.
CREATE UNIQUE INDEX ux_table_layout_default ON table_layout (table_key) WHERE is_default = 1;
