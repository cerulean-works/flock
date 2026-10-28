-- Budget management: admins can leave the spend of deleted projects out of the budget, and
-- dismiss the budget's warnings for the whole organization.

-- A deleted project whose runs had used GPUs. Deleting a project deletes its runs, and their
-- ledger rows lose the project (project_pk becomes null); they now point here instead, so
-- the spend can still be told apart by project. Its pk is not the deleted project's pk.
-- ``name`` is null for the projects deleted before this migration, whose names are gone:
-- they're kept as one row per organization.
CREATE TABLE deleted_projects (
    pk bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    org_pk bigint NOT NULL REFERENCES orgs ON DELETE CASCADE,
    name text,
    deleted_at timestamptz,
    -- Set while its spend is left out of the budget; cleared to count it again.
    excluded_at timestamptz,
    excluded_by bigint REFERENCES users ON DELETE SET NULL
);
CREATE INDEX deleted_projects_org ON deleted_projects (org_pk);

ALTER TABLE gpu_usage ADD COLUMN deleted_project_pk bigint
    REFERENCES deleted_projects ON DELETE SET NULL;

INSERT INTO deleted_projects (org_pk)
SELECT DISTINCT org_pk FROM gpu_usage WHERE project_pk IS NULL;
UPDATE gpu_usage u SET deleted_project_pk = d.pk
FROM deleted_projects d
WHERE u.project_pk IS NULL AND d.org_pk = u.org_pk;

-- A budget warning of a month dismissed for the organization, with what it was about then
-- (GPU types without a price, or runs without GPU information). It stays dismissed while
-- what it's about is among those items, and shows again once something new is.
CREATE TABLE budget_dismissals (
    org_pk bigint NOT NULL REFERENCES orgs ON DELETE CASCADE,
    month date NOT NULL CHECK (extract(day FROM month) = 1),
    kind text NOT NULL CHECK (kind IN ('unpriced', 'unknown_gpu')),
    items text[] NOT NULL,
    dismissed_by bigint REFERENCES users ON DELETE SET NULL,
    dismissed_at timestamptz NOT NULL,
    PRIMARY KEY (org_pk, month, kind)
);
