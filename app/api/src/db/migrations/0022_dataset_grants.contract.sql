-- Switch to exact grants only after older API and proxy workers drain (021, D155).
-- Existing orgs await an atomic S3 inventory; new orgs have no legacy paths to preserve.
INSERT INTO meta (key, value) VALUES ('dataset_public_grants', '1');
ALTER TABLE orgs ALTER COLUMN dataset_grants_ready SET DEFAULT true;
