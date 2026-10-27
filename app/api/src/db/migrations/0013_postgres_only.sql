-- PostgreSQL becomes the only state (012): every replica reads what this schema holds, and
-- nothing a replica keeps in memory is the source of anything.

-- Two-phase deletes (D80): a delete sets deleted_at and returns; a background purge removes
-- the rows in batches. Names are free again as soon as a delete commits.
ALTER TABLE orgs ADD COLUMN deleted_at timestamptz;
ALTER TABLE projects ADD COLUMN deleted_at timestamptz;
ALTER TABLE runs ADD COLUMN deleted_at timestamptz;
ALTER TABLE orgs DROP CONSTRAINT orgs_name_key;
CREATE UNIQUE INDEX orgs_name ON orgs (name) WHERE deleted_at IS NULL;
CREATE INDEX orgs_deleted ON orgs (pk) WHERE deleted_at IS NOT NULL;
ALTER TABLE projects DROP CONSTRAINT projects_org_pk_name_key;
CREATE UNIQUE INDEX projects_org_name ON projects (org_pk, name) WHERE deleted_at IS NULL;
CREATE INDEX projects_deleted ON projects (pk) WHERE deleted_at IS NOT NULL;
ALTER TABLE runs DROP CONSTRAINT runs_project_pk_name_key;
CREATE UNIQUE INDEX runs_project_name ON runs (project_pk, name) WHERE deleted_at IS NULL;
-- A project's live runs in creation order: the default sort, row ordinals and counts.
CREATE INDEX runs_live ON runs (project_pk, created_at, pk) WHERE deleted_at IS NULL;
CREATE INDEX runs_project ON runs (project_pk);
CREATE INDEX runs_deleted ON runs (pk) WHERE deleted_at IS NOT NULL;
CREATE INDEX runs_active ON runs (heartbeat_at)
    WHERE state IN ('running', 'preempting') AND deleted_at IS NULL;
CREATE INDEX gpu_usage_project ON gpu_usage (project_pk);

-- What wandb.define_metric set (parsed from the config, so pages of runs needn't parse it),
-- and how many requests appended series chunks since the run's were last compacted (D78).
ALTER TABLE runs ADD COLUMN metric_defs jsonb;
ALTER TABLE runs ADD COLUMN uncompacted integer NOT NULL DEFAULT 0;
CREATE INDEX runs_uncompacted ON runs (pk) WHERE uncompacted > 0;

-- The run table (D77): one row per (run, column) that has a value. Strings compare and sort
-- by code point, as they do in Python.
CREATE TABLE run_values (
    -- Denormalized from runs, so a filter clause is one index range.
    project_pk bigint NOT NULL,
    run_pk bigint NOT NULL REFERENCES runs ON DELETE CASCADE,
    key text COLLATE "C" NOT NULL,
    num float8,  -- numbers, NaN and infinities included
    txt text COLLATE "C",  -- strings
    obj jsonb,  -- booleans, lists and other values (non-finite numbers inside as strings)
    PRIMARY KEY (run_pk, key),
    CHECK (num_nonnulls(num, txt, obj) = 1)
);
CREATE INDEX run_values_num ON run_values (project_pk, key, num, run_pk) WHERE num IS NOT NULL;
CREATE INDEX run_values_obj ON run_values (project_pk, key, run_pk) WHERE obj IS NOT NULL;
-- An index entry holds at most ~2.7 kB, so strings and lists are indexed with their value only
-- when short. Filters read the long ones (rare) from the table, found through their own index.
-- Each condition says the value isn't null, which the planner can estimate.
CREATE FUNCTION run_values_short_text(key text, txt text) RETURNS boolean
    LANGUAGE sql IMMUTABLE PARALLEL SAFE
    AS $$ SELECT txt IS NOT NULL AND octet_length(key) + octet_length(txt) < 2400 $$;
CREATE FUNCTION run_values_long_text(key text, txt text) RETURNS boolean
    LANGUAGE sql IMMUTABLE PARALLEL SAFE
    AS $$ SELECT txt IS NOT NULL AND octet_length(key) + octet_length(txt) >= 2400 $$;
-- 64 elements and 1280 bytes as text are under 2.4 kB as jsonb, whatever the elements.
CREATE FUNCTION run_values_short_list(key text, obj jsonb) RETURNS boolean
    LANGUAGE sql IMMUTABLE PARALLEL SAFE
    AS $$ SELECT obj IS NOT NULL AND CASE WHEN jsonb_typeof(obj) = 'array'
             THEN jsonb_array_length(obj) <= 64
                  AND octet_length(key) + octet_length(obj::text) < 1280 END $$;
CREATE FUNCTION run_values_long_list(key text, obj jsonb) RETURNS boolean
    LANGUAGE sql IMMUTABLE PARALLEL SAFE
    AS $$ SELECT obj IS NOT NULL AND CASE WHEN jsonb_typeof(obj) = 'array'
             THEN jsonb_array_length(obj) > 64
                  OR octet_length(key) + octet_length(obj::text) >= 1280 END $$;
CREATE INDEX run_values_txt ON run_values (project_pk, key, txt, run_pk)
    WHERE run_values_short_text(key, txt);
CREATE INDEX run_values_long_txt ON run_values (project_pk, key, run_pk)
    WHERE run_values_long_text(key, txt);
-- Lists with their elements, so "tags contains baseline" reads no table rows.
CREATE INDEX run_values_lists ON run_values (project_pk, key, run_pk) INCLUDE (obj)
    WHERE run_values_short_list(key, obj);
CREATE INDEX run_values_long_lists ON run_values (project_pk, key, run_pk)
    WHERE run_values_long_list(key, obj);

-- A project's columns, for the columns menu and display types: how many runs have a value of
-- each type. A column whose count drops to 0 is gone (the purge removes its row).
CREATE TABLE project_keys (
    project_pk bigint NOT NULL REFERENCES projects ON DELETE CASCADE,
    key text COLLATE "C" NOT NULL,
    -- Every number written since the column (re)appeared was integral (D4's display rule).
    int_only boolean NOT NULL,
    runs integer NOT NULL,
    nums integer NOT NULL,
    strs integer NOT NULL,
    bools integer NOT NULL,
    lists integer NOT NULL,
    objects integer NOT NULL,
    PRIMARY KEY (project_pk, key)
);

-- Changes to project_keys' counts not folded into it yet. Writers append here rather than
-- update project_keys, whose rows every new run of a project would otherwise lock and queue
-- on. Readers add the two up, and every replica folds changes in the background.
CREATE TABLE project_key_changes (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    project_pk bigint NOT NULL REFERENCES projects ON DELETE CASCADE,
    key text COLLATE "C" NOT NULL,
    int_only boolean NOT NULL,
    runs integer NOT NULL,
    nums integer NOT NULL,
    strs integer NOT NULL,
    bools integer NOT NULL,
    lists integer NOT NULL,
    objects integer NOT NULL
);
CREATE INDEX project_key_changes_key ON project_key_changes (project_pk, key);

-- Curves (D78): each request's new points of a metric are one chunk, in D5's encoding (int32
-- lines unless they run on from first_line, then float64 values). Compaction merges a
-- metric's chunks into one.
CREATE TABLE series_chunks (
    run_pk bigint NOT NULL REFERENCES runs ON DELETE CASCADE,
    stream text NOT NULL CHECK (stream IN ('history', 'system')),
    key text NOT NULL,
    first_line integer NOT NULL,
    n integer NOT NULL,
    data bytea NOT NULL,
    PRIMARY KEY (run_pk, stream, key, first_line)
) WITH (toast_tuple_target = 8160);
ALTER TABLE series_chunks ALTER COLUMN data SET STORAGE EXTERNAL;

CREATE TABLE run_metrics (
    run_pk bigint NOT NULL REFERENCES runs ON DELETE CASCADE,
    stream text NOT NULL,
    key text NOT NULL,
    PRIMARY KEY (run_pk, stream, key)
);

-- A compacted stream holds exactly D5's blob, which starts at line 0. Streams compacted only
-- in part (active runs, crashes, resumes) are rebuilt from their raw lines by the backfill.
INSERT INTO series_chunks (run_pk, stream, key, first_line, n, data)
SELECT c.run_pk, c.stream, c.key, 0, c.n, c.data
FROM history_columns c
JOIN runs r ON r.pk = c.run_pk
WHERE CASE c.stream
    WHEN 'history' THEN r.history_compacted_line_count >= r.history_line_count
    ELSE r.system_compacted_line_count >= r.events_line_count
END;
INSERT INTO run_metrics (run_pk, stream, key)
SELECT run_pk, stream, key FROM series_chunks;

-- Runs the backfill (app/api/scripts/backfill.py) still has to fill run_values, project_keys,
-- run_metrics and metric_defs for, from their config and summary (which need Python's lenient
-- JSON), with the streams whose chunks it rebuilds from raw lines. It deletes each row once
-- done, so it resumes by run pk.
CREATE TABLE backfill (
    run_pk bigint PRIMARY KEY REFERENCES runs ON DELETE CASCADE,
    streams text[] NOT NULL
);
INSERT INTO backfill (run_pk, streams)
SELECT pk, array_remove(ARRAY[
    CASE WHEN history_compacted_line_count < history_line_count THEN 'history' END,
    CASE WHEN system_compacted_line_count < events_line_count THEN 'system' END
], NULL)
FROM runs;

-- Chunks replace the compacted blobs (D78).
DROP TABLE history_columns;
ALTER TABLE runs DROP COLUMN history_compacted_line_count,
    DROP COLUMN system_compacted_line_count;

-- Runs deleted while a client was still logging to them (D15, D80): that client's writes are
-- acknowledged and dropped until it has been quiet for FLOCK_CRASH_TIMEOUT.
CREATE TABLE discarded_runs (
    entity text NOT NULL,
    project text NOT NULL,
    name text NOT NULL,
    last_seen timestamptz NOT NULL,
    PRIMARY KEY (entity, project, name)
);

-- Short-lived sign-in state (D84), which every replica reads.
CREATE TABLE auth_codes (
    digest bytea PRIMARY KEY,
    user_pk bigint NOT NULL REFERENCES users ON DELETE CASCADE,
    expires_at timestamptz NOT NULL
);

CREATE TABLE device_grants (
    digest bytea PRIMARY KEY,  -- of the device code
    user_code text NOT NULL UNIQUE,
    hostname text NOT NULL,
    platform text NOT NULL,
    ip text,
    created_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    poll_interval float8 NOT NULL,
    polled_at timestamptz,
    user_pk bigint REFERENCES users ON DELETE CASCADE,  -- who approved it
    denied boolean NOT NULL DEFAULT false
);
CREATE INDEX device_grants_expires ON device_grants (expires_at);

CREATE TABLE auth_failures (
    user_pk bigint NOT NULL REFERENCES users ON DELETE CASCADE,
    at timestamptz NOT NULL
);
CREATE INDEX auth_failures_user ON auth_failures (user_pk, at);

-- Imports are claimed by the replica running them (D81): it heartbeats the row, and another
-- replica marks it interrupted once the heartbeat is stale.
ALTER TABLE org_imports ADD COLUMN heartbeat_at timestamptz;
ALTER TABLE org_imports ADD COLUMN cancel_requested boolean NOT NULL DEFAULT false;

-- Notifications too large for NOTIFY's 8000 bytes (D79): the payload names a row here.
CREATE TABLE bus_messages (
    pk bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at timestamptz NOT NULL DEFAULT now(),
    payload text NOT NULL
);

-- Every replica caches credentials and roles (D84) and drops them on any change here.
CREATE FUNCTION flock_auth_invalidate() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_notify('flock_auth', TG_TABLE_NAME);
    RETURN NULL;
END;
$$;
CREATE TRIGGER users_auth_invalidate AFTER INSERT OR UPDATE OR DELETE ON users
    FOR EACH STATEMENT EXECUTE FUNCTION flock_auth_invalidate();
CREATE TRIGGER memberships_auth_invalidate AFTER INSERT OR UPDATE OR DELETE ON memberships
    FOR EACH STATEMENT EXECUTE FUNCTION flock_auth_invalidate();
CREATE TRIGGER api_keys_auth_invalidate AFTER INSERT OR UPDATE OR DELETE ON api_keys
    FOR EACH STATEMENT EXECUTE FUNCTION flock_auth_invalidate();
CREATE TRIGGER sessions_auth_invalidate AFTER INSERT OR UPDATE OR DELETE ON sessions
    FOR EACH STATEMENT EXECUTE FUNCTION flock_auth_invalidate();
CREATE TRIGGER orgs_auth_invalidate AFTER UPDATE OF deleted_at OR DELETE ON orgs
    FOR EACH STATEMENT EXECUTE FUNCTION flock_auth_invalidate();

-- A deleted org's keys stop working at once in the S3 proxy, as before deletes were deferred.
DROP TRIGGER orgs_s3_invalidate ON orgs;
CREATE TRIGGER orgs_s3_invalidate
    AFTER UPDATE OF s3_ephemeral_ttl, s3_key_max_ttl, deleted_at OR DELETE ON orgs
    FOR EACH STATEMENT EXECUTE FUNCTION s3_invalidate();
