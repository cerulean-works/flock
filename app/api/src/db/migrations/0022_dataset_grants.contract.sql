-- flock: clean-drain
-- Exact grants require clean retirement of every older worker (021, D155).
-- Quarantine legacy readers too, even if an obsolete process unexpectedly resumes.
UPDATE orgs SET dataset_legacy_public = open_datasets, open_datasets = false;
ALTER TABLE orgs ALTER COLUMN open_datasets SET DEFAULT false;
ALTER TABLE orgs ADD CONSTRAINT dataset_legacy_reads_closed CHECK (NOT open_datasets);
INSERT INTO meta (key, value) VALUES ('dataset_public_grants', '1');
-- Existing orgs await an atomic S3 inventory; new orgs have no legacy paths to preserve.
ALTER TABLE orgs ALTER COLUMN dataset_grants_ready SET DEFAULT true;
