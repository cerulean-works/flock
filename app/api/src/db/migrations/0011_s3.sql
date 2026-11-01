-- S3 access through Flock's proxy: buckets owned by orgs, with a log of who created and deleted
-- them, and per-user S3 keys whose secrets are derived from `meta.s3_key_secret`.

-- How long an org's keys may live, in seconds: keys exchanged for a CLI token or API key live
-- `s3_ephemeral_ttl`, and no key outlives `s3_key_max_ttl` (null: no limit).
ALTER TABLE orgs
    ADD COLUMN s3_ephemeral_ttl integer NOT NULL DEFAULT 28800 CHECK (s3_ephemeral_ttl > 0),
    ADD COLUMN s3_key_max_ttl integer DEFAULT 2592000,
    ADD CONSTRAINT orgs_s3_ttls CHECK (s3_key_max_ttl IS NULL OR s3_ephemeral_ttl <= s3_key_max_ttl);

-- A bucket is `upstream_name` on the S3 server: `{prefix}{org pk:05}-{name}`. Deleting one
-- sets `deleted_at` (freeing the name in the org), and a background task empties and removes
-- it upstream, then sets `purged_at`. Rows stay for their history, without an org once the
-- org is deleted.
CREATE TABLE s3_buckets (
    pk bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    org_pk bigint REFERENCES orgs ON DELETE SET NULL,
    name text NOT NULL,
    upstream_name text NOT NULL,
    created_at timestamptz NOT NULL,
    created_by bigint REFERENCES users ON DELETE SET NULL,
    deleted_at timestamptz,
    purged_at timestamptz,
    CHECK (deleted_at IS NOT NULL OR org_pk IS NOT NULL),
    CHECK (purged_at IS NULL OR deleted_at IS NOT NULL)
);
CREATE UNIQUE INDEX s3_buckets_org_name ON s3_buckets (org_pk, name) WHERE deleted_at IS NULL;
CREATE UNIQUE INDEX s3_buckets_upstream_name ON s3_buckets (upstream_name) WHERE purged_at IS NULL;
CREATE INDEX s3_buckets_purging ON s3_buckets (pk) WHERE deleted_at IS NOT NULL AND purged_at IS NULL;

-- Who did what to a bucket, and through what (`detail.via`: ui, cli, api or purge).
CREATE TABLE s3_bucket_events (
    pk bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    bucket_pk bigint NOT NULL REFERENCES s3_buckets ON DELETE CASCADE,
    kind text NOT NULL CHECK (kind IN ('created', 'deleted', 'purged')),
    user_pk bigint REFERENCES users ON DELETE SET NULL,
    at timestamptz NOT NULL,
    detail jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX s3_bucket_events_bucket ON s3_bucket_events (bucket_pk);

-- An S3 key covers one org. Ephemeral keys belong to the CLI session or API key they were
-- exchanged for, and go with it; long-lived ones (from the account page) have neither.
CREATE TABLE s3_keys (
    pk bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    access_key_id text NOT NULL UNIQUE,
    user_pk bigint NOT NULL REFERENCES users ON DELETE CASCADE,
    org_pk bigint NOT NULL REFERENCES orgs ON DELETE CASCADE,
    name text NOT NULL,
    read_only boolean NOT NULL,
    created_at timestamptz NOT NULL,
    expires_at timestamptz,
    session_pk bigint REFERENCES sessions ON DELETE CASCADE,
    api_key_pk bigint REFERENCES api_keys ON DELETE CASCADE,
    last_used_at timestamptz
);
CREATE INDEX s3_keys_user ON s3_keys (user_pk);
CREATE INDEX s3_keys_org ON s3_keys (org_pk);
CREATE INDEX s3_keys_session ON s3_keys (session_pk) WHERE session_pk IS NOT NULL;
CREATE INDEX s3_keys_api_key ON s3_keys (api_key_pk) WHERE api_key_pk IS NOT NULL;
CREATE INDEX s3_keys_expires ON s3_keys (expires_at) WHERE expires_at IS NOT NULL;

-- Key that S3 secrets are derived from (244 random bits, hex).
INSERT INTO meta (key, value)
VALUES ('s3_key_secret', replace(gen_random_uuid()::text || gen_random_uuid()::text, '-', ''));

-- The proxy caches keys, memberships, buckets and org policies, and drops its cache when told
-- on this channel. Triggers send it in the changing transaction, cascades included. New keys
-- don't need it (the proxy never caches a key as missing), and `last_used_at` writes mustn't.
CREATE FUNCTION s3_invalidate() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_notify('s3_invalidate', TG_TABLE_NAME);
    RETURN NULL;
END
$$;

CREATE TRIGGER s3_keys_invalidate AFTER DELETE ON s3_keys
    FOR EACH STATEMENT EXECUTE FUNCTION s3_invalidate();
CREATE TRIGGER s3_buckets_invalidate AFTER INSERT OR UPDATE OR DELETE ON s3_buckets
    FOR EACH STATEMENT EXECUTE FUNCTION s3_invalidate();
CREATE TRIGGER memberships_s3_invalidate AFTER INSERT OR UPDATE OR DELETE ON memberships
    FOR EACH STATEMENT EXECUTE FUNCTION s3_invalidate();
CREATE TRIGGER orgs_s3_invalidate AFTER UPDATE OF s3_ephemeral_ttl, s3_key_max_ttl OR DELETE ON orgs
    FOR EACH STATEMENT EXECUTE FUNCTION s3_invalidate();
CREATE TRIGGER users_s3_invalidate AFTER UPDATE OF is_superadmin OR DELETE ON users
    FOR EACH STATEMENT EXECUTE FUNCTION s3_invalidate();
