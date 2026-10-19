-- System metrics (wandb-events.jsonl) get the same columnar copy as history. history_columns
-- now holds one blob per (run, stream, metric) where stream is 'history' or 'system', and each
-- stream tracks how many of its raw lines the columnar copy covers.

ALTER TABLE history_columns ADD COLUMN stream text NOT NULL DEFAULT 'history'
    CHECK (stream IN ('history', 'system'));
ALTER TABLE history_columns ALTER COLUMN stream DROP DEFAULT;
ALTER TABLE history_columns DROP CONSTRAINT history_columns_pkey;
ALTER TABLE history_columns ADD PRIMARY KEY (run_pk, stream, key);

ALTER TABLE runs RENAME COLUMN compacted_line_count TO history_compacted_line_count;
-- Existing runs start uncompacted; the server builds their system columns on startup.
ALTER TABLE runs ADD COLUMN system_compacted_line_count integer NOT NULL DEFAULT 0;
