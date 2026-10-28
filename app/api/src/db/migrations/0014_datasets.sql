-- The Datasets tab (013): one bucket per org that Flock manages, and a trash for deleted datasets.

-- A bucket Flock creates and owns (D85): 'datasets' for the org's flock-datasets, null for the
-- rest. At most one live managed bucket of each kind per org.
ALTER TABLE s3_buckets ADD COLUMN managed text CHECK (managed IN ('datasets'));
CREATE UNIQUE INDEX s3_buckets_org_managed ON s3_buckets (org_pk, managed)
    WHERE deleted_at IS NULL AND managed IS NOT NULL;

-- Datasets deleted from their page wait here for 14 days (D93). The objects move to the trash
-- bucket under `{org pk:05}/{pk}/`; `moved_at` is set once the move is done. The org's own
-- entries go with it (its deletion removes them at once).
CREATE TABLE dataset_trash (
    pk bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    org_pk bigint REFERENCES orgs ON DELETE SET NULL,
    path text NOT NULL,
    deleted_by bigint REFERENCES users ON DELETE SET NULL,
    deleted_at timestamptz NOT NULL,
    moved_at timestamptz,
    latest_version bigint,
    objects integer NOT NULL,
    bytes bigint NOT NULL
);
CREATE INDEX dataset_trash_org ON dataset_trash (org_pk);

-- The dashboard's Datasets tab lives at `/datasets/{org}`, so `datasets` joins the names an org
-- can't have. An org that already has it would be unreachable there: refuse to migrate until it's
-- renamed, rather than hide it.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM orgs WHERE lower(name) = 'datasets' AND deleted_at IS NULL) THEN
        RAISE EXCEPTION 'an organization is named "datasets", which the dashboard now uses for '
            'its Datasets tab: rename it (UPDATE orgs SET name = ... WHERE name = ...) and start '
            'Flock again';
    END IF;
END
$$;
