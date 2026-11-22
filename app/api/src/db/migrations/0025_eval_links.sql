-- Eval runs that scored a version (014's D106, restored by 025's D182). A database that applied
-- 0015 before the table was taken out of it still has the table, empty, so both are made only if
-- they're missing.
CREATE TABLE IF NOT EXISTS model_version_runs (
    version_pk bigint NOT NULL REFERENCES model_versions ON DELETE CASCADE,
    run_pk bigint NOT NULL REFERENCES runs ON DELETE CASCADE,
    kind text NOT NULL CHECK (kind IN ('eval')),
    PRIMARY KEY (version_pk, run_pk)
);
CREATE INDEX IF NOT EXISTS model_version_runs_run ON model_version_runs (run_pk);
