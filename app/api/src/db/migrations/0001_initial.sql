-- Runs and everything they log. Raw wandb JSON (config, summary, history/event lines) is kept
-- as text rather than jsonb: wandb writes NaN/Infinity literals, which jsonb rejects.

CREATE TABLE runs (
    pk bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entity text NOT NULL,
    project text NOT NULL,
    name text NOT NULL,
    display_name text,
    state text NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    heartbeat_at timestamptz NOT NULL,
    config text,
    summary text,
    tags text[] NOT NULL DEFAULT '{}',
    notes text,
    group_name text,
    job_type text,
    host text,
    program text,
    commit_hash text,
    description text,
    sweep text,
    exitcode integer,
    -- Number of lines received per wandb file; clients resume appending from these.
    history_line_count integer NOT NULL DEFAULT 0,
    events_line_count integer NOT NULL DEFAULT 0,
    log_line_count integer NOT NULL DEFAULT 0,
    -- History lines covered by history_columns; less than history_line_count means the
    -- columnar copy is stale and is rebuilt from the history table.
    compacted_line_count integer NOT NULL DEFAULT 0,
    UNIQUE (entity, project, name)
);

-- wandb-history.jsonl, wandb-events.jsonl and output.log, one row per line. Clients address
-- lines by number and may resend them, so writes upsert on (run_pk, line).
CREATE TABLE history (
    run_pk bigint NOT NULL REFERENCES runs ON DELETE CASCADE,
    line integer NOT NULL,
    data text NOT NULL,
    PRIMARY KEY (run_pk, line)
);

CREATE TABLE events (
    run_pk bigint NOT NULL REFERENCES runs ON DELETE CASCADE,
    line integer NOT NULL,
    data text NOT NULL,
    PRIMARY KEY (run_pk, line)
);

CREATE TABLE logs (
    run_pk bigint NOT NULL REFERENCES runs ON DELETE CASCADE,
    line integer NOT NULL,
    data text NOT NULL,
    PRIMARY KEY (run_pk, line)
);

-- Columnar copy of a finished run's history: one blob per metric holding n float64 values,
-- preceded by n int32 line numbers unless the metric was logged on every line (little-endian).
CREATE TABLE history_columns (
    run_pk bigint NOT NULL REFERENCES runs ON DELETE CASCADE,
    key text NOT NULL,
    n integer NOT NULL,
    data bytea NOT NULL,
    PRIMARY KEY (run_pk, key)
) WITH (toast_tuple_target = 8160);
-- Float arrays barely compress, so never pglz them. Together with the raised tuple target,
-- curves up to ~8 KB (1k points) stay inline in the heap page: one read per curve, no TOAST
-- lookups. Larger ones move out of line uncompressed.
ALTER TABLE history_columns ALTER COLUMN data SET STORAGE EXTERNAL;

-- Run files uploaded through signed URLs; contents live in the files directory.
CREATE TABLE run_files (
    run_pk bigint NOT NULL REFERENCES runs ON DELETE CASCADE,
    name text NOT NULL,
    size bigint NOT NULL,
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (run_pk, name)
);

CREATE TABLE meta (
    key text PRIMARY KEY,
    value text NOT NULL
);

-- Key for signing file upload URLs (two UUIDv4s: 244 random bits, hex encoded).
INSERT INTO meta (key, value)
VALUES ('upload_secret', replace(gen_random_uuid()::text || gen_random_uuid()::text, '-', ''));
