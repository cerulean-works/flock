-- Imports of a W&B entity's projects and runs into an organization: one row per org, the
-- latest import. The W&B API key is never stored, so an import cut short by a restart is
-- marked interrupted and resumed by starting it again with the key (copied runs are skipped).

CREATE TABLE org_imports (
    org_pk bigint PRIMARY KEY REFERENCES orgs ON DELETE CASCADE,
    entity text NOT NULL,
    base_url text NOT NULL,
    state text NOT NULL
        CHECK (state IN ('running', 'finished', 'failed', 'cancelled', 'interrupted')),
    projects_total int NOT NULL DEFAULT 0,
    projects_done int NOT NULL DEFAULT 0,
    runs_total int NOT NULL DEFAULT 0,
    runs_done int NOT NULL DEFAULT 0,
    runs_skipped int NOT NULL DEFAULT 0,
    runs_failed int NOT NULL DEFAULT 0,
    current_project text,
    error text,
    started_by bigint REFERENCES users ON DELETE SET NULL,
    started_at timestamptz NOT NULL,
    finished_at timestamptz
);
