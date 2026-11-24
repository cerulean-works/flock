-- Forks and blends of datasets (design doc 023). Renumber if 022's migrations land first.
--
-- dataset_origins: where a fork or blend came from, one row per dataset made by Flock (D171,
-- D174). `path` follows the dataset on renames and moves. `trash_pk` is set while it waits in
-- the trash, so its path counts as free again, and cleared when it's restored.
--
-- dataset_links: the pins (D173). Each row is one version of a source that a dependent's files
-- come from, kept from Lance's cleanup by the tag `flock-ref-{ulid}` on the source. A fork of a
-- fork holds an `inherited` pin on the original too. Rows go when the dependent is deleted for
-- good or made independent (D178). A source with rows here can't be renamed, moved or deleted
-- (D177).
--
-- dataset_jobs: blends and making datasets independent, run by the API in the background as
-- Hugging Face imports are (D176).
CREATE TABLE dataset_origins (
    pk bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    org_pk bigint NOT NULL REFERENCES orgs ON DELETE CASCADE,
    path text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('fork', 'blend')),
    detail jsonb NOT NULL DEFAULT '{}',
    created_by bigint REFERENCES users ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    trash_pk bigint
);
CREATE UNIQUE INDEX dataset_origins_path ON dataset_origins (org_pk, path) WHERE trash_pk IS NULL;

CREATE TABLE dataset_links (
    pk bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    org_pk bigint NOT NULL REFERENCES orgs ON DELETE CASCADE,
    ulid text NOT NULL UNIQUE,
    dependent_path text NOT NULL,
    source_path text NOT NULL,
    source_version bigint NOT NULL,
    kind text NOT NULL CHECK (kind IN ('fork', 'blend', 'inherited')),
    created_at timestamptz NOT NULL DEFAULT now(),
    trash_pk bigint
);
CREATE INDEX dataset_links_source ON dataset_links (org_pk, source_path);
CREATE INDEX dataset_links_dependent ON dataset_links (org_pk, dependent_path);

CREATE TABLE dataset_jobs (
    pk bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    org_pk bigint NOT NULL REFERENCES orgs ON DELETE CASCADE,
    kind text NOT NULL CHECK (kind IN ('blend', 'independent')),
    dest text NOT NULL,
    options jsonb NOT NULL DEFAULT '{}',
    state text NOT NULL
        CHECK (state IN ('running', 'finished', 'failed', 'cancelled', 'interrupted')),
    phase text,
    rows_total bigint NOT NULL DEFAULT 0,
    rows_done bigint NOT NULL DEFAULT 0,
    result jsonb,
    error text,
    started_by bigint REFERENCES users ON DELETE SET NULL,
    started_at timestamptz NOT NULL,
    finished_at timestamptz,
    heartbeat_at timestamptz,
    cancel_requested boolean NOT NULL DEFAULT false,
    cleaned boolean NOT NULL DEFAULT false
);
CREATE INDEX dataset_jobs_org ON dataset_jobs (org_pk, pk DESC);
CREATE INDEX dataset_jobs_running ON dataset_jobs (heartbeat_at) WHERE state = 'running';
