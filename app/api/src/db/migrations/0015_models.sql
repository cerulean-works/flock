-- The model registry (014): each org's flock-models bucket, and the tables that number, tag and
-- link the versions stored in it.

-- A second kind of bucket Flock creates and owns (D97): 'models' for the org's flock-models.
ALTER TABLE s3_buckets DROP CONSTRAINT s3_buckets_managed_check;
ALTER TABLE s3_buckets ADD CONSTRAINT s3_buckets_managed_check
    CHECK (managed IN ('datasets', 'models'));

-- "Keep the newest N untagged versions of each model" (D109), an org setting: null is off.
ALTER TABLE orgs ADD COLUMN model_retention integer CHECK (model_retention > 0);

-- A model is `org/name` (D98). `last_number` is the last version number handed out, so numbers
-- are never reused, even once deleted versions are gone from the trash. Deleting a model sets
-- `deleted_at` on it and on its live versions, to the same time, so restoring it brings back
-- exactly those.
CREATE TABLE models (
    pk bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    org_pk bigint NOT NULL REFERENCES orgs ON DELETE CASCADE,
    name text NOT NULL,
    created_by bigint REFERENCES users ON DELETE SET NULL,
    created_at timestamptz NOT NULL,
    last_number integer NOT NULL DEFAULT 0,
    primary_version_pk bigint,
    primary_set_by bigint REFERENCES users ON DELETE SET NULL,
    primary_set_at timestamptz,
    deleted_at timestamptz,
    deleted_by bigint REFERENCES users ON DELETE SET NULL
);
CREATE UNIQUE INDEX models_org_name ON models (org_pk, name) WHERE deleted_at IS NULL;
CREATE INDEX models_deleted ON models (org_pk) WHERE deleted_at IS NOT NULL;

-- An upload attempt (D99, D100), written to `{name}/u/{ulid}/` of flock-models through the proxy
-- while `open`. `files` is what was declared when it was opened: [{path, size}].
CREATE TABLE model_uploads (
    pk bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ulid text NOT NULL UNIQUE,
    model_pk bigint NOT NULL REFERENCES models ON DELETE CASCADE,
    user_pk bigint REFERENCES users ON DELETE SET NULL,
    state text NOT NULL CHECK (state IN ('open', 'committed', 'aborted', 'expired')),
    files jsonb NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}',
    tags text[] NOT NULL DEFAULT '{}',
    make_primary boolean NOT NULL DEFAULT false,
    run_pk bigint REFERENCES runs ON DELETE SET NULL,
    step bigint,
    dedupe_key text,
    created_at timestamptz NOT NULL,
    last_activity_at timestamptz NOT NULL
);
CREATE INDEX model_uploads_model ON model_uploads (model_pk, state);
CREATE INDEX model_uploads_open ON model_uploads (last_activity_at) WHERE state = 'open';

-- A committed upload, numbered (D98). Its files stay in its upload's folder. A deleted version's
-- files move to the trash bucket; `trashed_at` is set once they're all there, and
-- `deleted_tags` keeps the checkpoint tags that went with it, for a restore. `manifest_at` is
-- set once manifest.json is written, so the startup pass knows which are missing.
CREATE TABLE model_versions (
    pk bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    model_pk bigint NOT NULL REFERENCES models ON DELETE CASCADE,
    number integer NOT NULL,
    upload_pk bigint NOT NULL UNIQUE REFERENCES model_uploads,
    digest text NOT NULL,
    total_bytes bigint NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}',
    run_pk bigint REFERENCES runs ON DELETE SET NULL,
    step bigint,
    dedupe_key text,
    created_by bigint REFERENCES users ON DELETE SET NULL,
    created_at timestamptz NOT NULL,
    manifest_at timestamptz,
    deleted_at timestamptz,
    deleted_by bigint REFERENCES users ON DELETE SET NULL,
    trashed_at timestamptz,
    deleted_tags text[] NOT NULL DEFAULT '{}',
    UNIQUE (model_pk, number)
);
-- Only live versions answer for a dedupe key, so a deleted one doesn't block saving it again.
CREATE UNIQUE INDEX model_versions_dedupe ON model_versions (model_pk, dedupe_key)
    WHERE deleted_at IS NULL AND dedupe_key IS NOT NULL;
CREATE INDEX model_versions_run ON model_versions (run_pk) WHERE run_pk IS NOT NULL;
CREATE INDEX model_versions_deleted ON model_versions (model_pk) WHERE deleted_at IS NOT NULL;

ALTER TABLE models ADD CONSTRAINT models_primary_version
    FOREIGN KEY (primary_version_pk) REFERENCES model_versions ON DELETE SET NULL;

CREATE TABLE model_files (
    version_pk bigint NOT NULL REFERENCES model_versions ON DELETE CASCADE,
    path text NOT NULL,
    size bigint NOT NULL,
    sha256 text NOT NULL,
    PRIMARY KEY (version_pk, path)
);

-- Tags are on live versions only: a deleted version's checkpoint tags go with it.
CREATE TABLE model_tags (
    model_pk bigint NOT NULL REFERENCES models ON DELETE CASCADE,
    name text NOT NULL,
    version_pk bigint NOT NULL REFERENCES model_versions ON DELETE CASCADE,
    moved_by bigint REFERENCES users ON DELETE SET NULL,
    moved_at timestamptz NOT NULL,
    PRIMARY KEY (model_pk, name)
);
CREATE INDEX model_tags_version ON model_tags (version_pk);

-- Eval runs that scored a version (D106).
CREATE TABLE model_version_runs (
    version_pk bigint NOT NULL REFERENCES model_versions ON DELETE CASCADE,
    run_pk bigint NOT NULL REFERENCES runs ON DELETE CASCADE,
    kind text NOT NULL CHECK (kind IN ('eval')),
    PRIMARY KEY (version_pk, run_pk)
);
CREATE INDEX model_version_runs_run ON model_version_runs (run_pk);

-- The S3 proxy caches open uploads (D97, D101) and drops one when its state changes.
CREATE FUNCTION model_upload_changed() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_notify('flock_model_uploads', NEW.ulid);
    RETURN NULL;
END
$$;
CREATE TRIGGER model_uploads_state AFTER UPDATE OF state ON model_uploads
    FOR EACH ROW WHEN (OLD.state IS DISTINCT FROM NEW.state)
    EXECUTE FUNCTION model_upload_changed();

-- The dashboard's Models tab lives at `/models/{org}` (D108), as Datasets took `/datasets/`.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM orgs WHERE lower(name) = 'models' AND deleted_at IS NULL) THEN
        RAISE EXCEPTION 'an organization is named "models", which the dashboard now uses for '
            'its Models tab: rename it (UPDATE orgs SET name = ... WHERE name = ...) and start '
            'Flock again';
    END IF;
END
$$;
