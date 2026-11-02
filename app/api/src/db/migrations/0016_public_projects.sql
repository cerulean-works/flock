-- Public projects and datasets (015): readable by anyone with the link.

-- A project is private (its org's members only) or public (anyone can read it) (D110).
ALTER TABLE projects ADD COLUMN visibility text NOT NULL DEFAULT 'private'
    CHECK (visibility IN ('private', 'public'));

-- The datasets an org made public, by exact path (D115). Datasets aren't rows anywhere (D85),
-- so the flag is a row of its own: renames move it, deletes drop it.
CREATE TABLE public_datasets (
    org_pk bigint NOT NULL REFERENCES orgs ON DELETE CASCADE,
    path text NOT NULL,
    made_by bigint REFERENCES users ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (org_pk, path)
);

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
