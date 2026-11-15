-- Explicit public grants (021, D155): retain legacy behavior until the contract cutover.
CREATE TABLE public_datasets (
    org_pk bigint NOT NULL REFERENCES orgs ON DELETE CASCADE,
    path text NOT NULL,
    made_by bigint REFERENCES users ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (org_pk, path)
);
ALTER TABLE orgs ADD COLUMN dataset_grants_ready boolean NOT NULL DEFAULT false;
