-- Every dataset starts private; admins grant exact paths after deployment (021, D155).
CREATE TABLE public_datasets (
    org_pk bigint NOT NULL REFERENCES orgs ON DELETE CASCADE,
    path text NOT NULL,
    made_by bigint REFERENCES users ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (org_pk, path)
);
UPDATE orgs SET open_datasets = false;
ALTER TABLE orgs ALTER COLUMN open_datasets SET DEFAULT false;
ALTER TABLE orgs ADD CONSTRAINT dataset_legacy_reads_closed CHECK (NOT open_datasets);
