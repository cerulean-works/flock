-- Organizations (wandb entities) become records that own projects, and the server gets user
-- accounts: memberships with a role per org, per-user API keys, refresh-token sessions, and
-- one-time invite and password-reset links.

CREATE TABLE orgs (
    pk bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name text NOT NULL UNIQUE,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL
);

INSERT INTO orgs (name, created_at, updated_at)
SELECT entity, min(created_at), max(updated_at)
FROM projects
GROUP BY entity;

ALTER TABLE projects ADD COLUMN org_pk bigint REFERENCES orgs ON DELETE CASCADE;
UPDATE projects SET org_pk = o.pk FROM orgs o WHERE o.name = projects.entity;
ALTER TABLE projects ALTER COLUMN org_pk SET NOT NULL;
ALTER TABLE projects DROP CONSTRAINT projects_entity_name_key;
ALTER TABLE projects ADD CONSTRAINT projects_org_pk_name_key UNIQUE (org_pk, name);
ALTER TABLE projects DROP COLUMN entity;

CREATE TABLE users (
    pk bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email text NOT NULL UNIQUE CHECK (email = lower(email)),
    name text NOT NULL,
    -- argon2id; null for accounts that only sign in with Google.
    password_hash text,
    google_sub text UNIQUE,
    is_superadmin boolean NOT NULL DEFAULT false,
    default_org_pk bigint REFERENCES orgs ON DELETE SET NULL,
    created_at timestamptz NOT NULL
);

CREATE TABLE memberships (
    user_pk bigint NOT NULL REFERENCES users ON DELETE CASCADE,
    org_pk bigint NOT NULL REFERENCES orgs ON DELETE CASCADE,
    role text NOT NULL CHECK (role IN ('admin', 'member')),
    created_at timestamptz NOT NULL,
    PRIMARY KEY (user_pk, org_pk)
);

-- Secrets (API keys, refresh tokens, link tokens) are stored as sha256 digests only.
CREATE TABLE api_keys (
    pk bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_pk bigint NOT NULL REFERENCES users ON DELETE CASCADE,
    name text NOT NULL,
    prefix text NOT NULL,
    hash bytea NOT NULL UNIQUE,
    created_at timestamptz NOT NULL
);

CREATE TABLE sessions (
    pk bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_pk bigint NOT NULL REFERENCES users ON DELETE CASCADE,
    refresh_hash bytea NOT NULL UNIQUE,
    created_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL
);

CREATE TABLE invites (
    pk bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    org_pk bigint NOT NULL REFERENCES orgs ON DELETE CASCADE,
    email text NOT NULL,
    role text NOT NULL CHECK (role IN ('admin', 'member')),
    token_hash bytea NOT NULL UNIQUE,
    created_by bigint REFERENCES users ON DELETE SET NULL,
    created_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL
);

CREATE TABLE password_resets (
    pk bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_pk bigint NOT NULL REFERENCES users ON DELETE CASCADE,
    token_hash bytea NOT NULL UNIQUE,
    created_by bigint REFERENCES users ON DELETE SET NULL,
    created_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL
);

-- Who logged the run; runs from before accounts existed have none.
ALTER TABLE runs ADD COLUMN owner_pk bigint REFERENCES users ON DELETE SET NULL;

-- Key for signing access tokens, unless TRACKER_JWT_SECRET is set (244 random bits, hex).
INSERT INTO meta (key, value)
VALUES ('jwt_secret', replace(gen_random_uuid()::text || gen_random_uuid()::text, '-', ''));
