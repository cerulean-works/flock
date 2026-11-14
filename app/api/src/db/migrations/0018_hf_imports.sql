-- Imports of a Hugging Face dataset or model, started from the web app and run by the API in
-- the background (D124). One row per import. The access token is never stored: an import cut
-- short by a restart is marked interrupted and started again from the dialog.
--
-- `dest` is the dataset folder or the model's name, `options` the rest of what was asked for
-- (a dataset's config and splits, a model's tags and primary), and `result` what was made
-- (each split's dataset path and version, or the model version's number). `phase` is the step
-- running: downloading or converting a dataset, uploading or committing a model.
CREATE TABLE hf_imports (
    pk bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    org_pk bigint NOT NULL REFERENCES orgs ON DELETE CASCADE,
    kind text NOT NULL CHECK (kind IN ('dataset', 'model')),
    repo text NOT NULL,
    revision text NOT NULL,
    sha text,
    dest text NOT NULL,
    options jsonb NOT NULL DEFAULT '{}',
    state text NOT NULL
        CHECK (state IN ('running', 'finished', 'failed', 'cancelled', 'interrupted')),
    phase text,
    bytes_total bigint NOT NULL DEFAULT 0,
    bytes_done bigint NOT NULL DEFAULT 0,
    files_total integer NOT NULL DEFAULT 0,
    files_done integer NOT NULL DEFAULT 0,
    rows_done bigint NOT NULL DEFAULT 0,
    result jsonb,
    error text,
    command text NOT NULL,
    started_by bigint REFERENCES users ON DELETE SET NULL,
    started_at timestamptz NOT NULL,
    finished_at timestamptz,
    heartbeat_at timestamptz,
    cancel_requested boolean NOT NULL DEFAULT false
);
CREATE INDEX hf_imports_org ON hf_imports (org_pk, pk DESC);
CREATE INDEX hf_imports_running ON hf_imports (heartbeat_at) WHERE state = 'running';
