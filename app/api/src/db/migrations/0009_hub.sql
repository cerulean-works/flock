-- The hub: datasets stored as Lance datasets in S3, under datasets/<uuid>/ in the hub bucket.
-- Lance keeps the versions; these tables keep who may touch a dataset, who wrote each version,
-- and a summary of each version that outlives Lance's cleanup of old transaction files.

CREATE TABLE datasets (
    pk bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- The S3 prefix. Names can change and be reused; the uuid never does.
    uuid uuid NOT NULL UNIQUE,
    org_pk bigint NOT NULL REFERENCES orgs ON DELETE CASCADE,
    name text NOT NULL,
    description text NOT NULL DEFAULT '',
    visibility text NOT NULL DEFAULT 'private' CHECK (visibility IN ('private', 'internal')),
    created_by bigint REFERENCES users ON DELETE SET NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    -- Set when deleted; the objects under the prefix are purged in the background, then the
    -- row is removed.
    deleted_at timestamptz
);

CREATE UNIQUE INDEX datasets_live_name ON datasets (org_pk, name) WHERE deleted_at IS NULL;

-- One row per Lance version. The S3 proxy inserts (dataset_pk, version, user_pk, created_at)
-- when the version's manifest is written, so authorship can't be forged. The API fills in the
-- rest from the version's transaction.
CREATE TABLE dataset_commits (
    dataset_pk bigint NOT NULL REFERENCES datasets ON DELETE CASCADE,
    version bigint NOT NULL,
    user_pk bigint REFERENCES users ON DELETE SET NULL,
    created_at timestamptz NOT NULL,
    op text,
    message text,
    rows bigint,
    bytes bigint,
    fragments int,
    -- The version's manifest was removed by a prune; this row is all that is left of it.
    pruned boolean NOT NULL DEFAULT false,
    PRIMARY KEY (dataset_pk, version)
);

-- Long-lived S3 keys. The secret is HMAC-SHA256(meta.hub_key_secret, access_key_id), so it is
-- never stored; deleting the row revokes the key.
CREATE TABLE hub_keys (
    pk bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_pk bigint NOT NULL REFERENCES users ON DELETE CASCADE,
    name text NOT NULL,
    access_key_id text NOT NULL UNIQUE,
    read_only boolean NOT NULL DEFAULT false,
    -- Limits the key to one org's datasets; NULL means every org the user is in.
    org_pk bigint REFERENCES orgs ON DELETE CASCADE,
    created_at timestamptz NOT NULL,
    last_used_at timestamptz
);

-- Imports of Hugging Face datasets into the mirror org, deduplicated on (repo, config, sha).
CREATE TABLE hub_mirrors (
    pk bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    repo text NOT NULL,
    config text NOT NULL DEFAULT '',
    sha text NOT NULL,
    dataset_pk bigint REFERENCES datasets ON DELETE SET NULL,
    state text NOT NULL CHECK (state IN ('running', 'finished', 'failed')),
    version bigint,
    error text,
    requested_by bigint REFERENCES users ON DELETE SET NULL,
    started_at timestamptz NOT NULL,
    finished_at timestamptz,
    UNIQUE (repo, config, sha)
);

INSERT INTO meta (key, value)
VALUES ('hub_key_secret', replace(gen_random_uuid()::text || gen_random_uuid()::text, '-', ''));
