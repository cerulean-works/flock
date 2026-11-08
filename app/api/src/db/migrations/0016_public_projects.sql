-- Public projects and datasets (015): readable by anyone with the link.

-- A project is private (its org's members only) or public (anyone can read it) (D110).
ALTER TABLE projects ADD COLUMN visibility text NOT NULL DEFAULT 'private'
    CHECK (visibility IN ('private', 'public'));

-- Datasets are public unless made private, by exact path (D115). Datasets aren't rows anywhere
-- (D85), so the flag is a row of its own. Renames move it and deletes leave it, so a stale flag
-- only ever makes something private.
CREATE TABLE private_datasets (
    org_pk bigint NOT NULL REFERENCES orgs ON DELETE CASCADE,
    path text NOT NULL,
    made_by bigint REFERENCES users ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (org_pk, path)
);

-- Whether an org's datasets without a flag are public (D115). Orgs that exist now wrote their
-- datasets while everything was private, so they start closed: Datasets.close_existing flags
-- those datasets private, then opens the org. New orgs start open.
ALTER TABLE orgs ADD COLUMN open_datasets boolean NOT NULL DEFAULT false;
ALTER TABLE orgs ALTER COLUMN open_datasets SET DEFAULT true;

-- Who made what public or private, and when (D110, D115): `project_pk` for a project, `path`
-- for a dataset.
CREATE TABLE visibility_events (
    pk bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    org_pk bigint NOT NULL REFERENCES orgs ON DELETE CASCADE,
    project_pk bigint REFERENCES projects ON DELETE CASCADE,
    path text,
    kind text NOT NULL CHECK (kind IN ('visibility', 'dataset_visibility')),
    from_visibility text NOT NULL,
    to_visibility text NOT NULL,
    user_pk bigint REFERENCES users ON DELETE SET NULL,
    at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX visibility_events_org ON visibility_events (org_pk, at);
