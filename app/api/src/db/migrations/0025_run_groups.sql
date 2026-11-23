-- Run groups (025): saved lists of runs with the filter they were built with, the project's
-- "best by" setting, dismissed suggestions, and the org switch for the assistant (D193).

-- A group (D183). `filter` is the Mongo-style filter it was built with ('{}': none), which only
-- decides the new matches it offers. `loaded_at` is when its runs were last loaded.
CREATE TABLE run_groups (
    pk bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    project_pk bigint NOT NULL REFERENCES projects ON DELETE CASCADE,
    name text NOT NULL,
    color text NOT NULL,
    filter jsonb NOT NULL DEFAULT '{}',
    loaded_at timestamptz NOT NULL DEFAULT now(),
    -- D184: null takes the project's.
    best_key text,
    best_order text CHECK (best_order IN ('asc', 'desc')),
    -- D185: the repeat key set by hand, and kinds dragged by hand ({"config.x": "varies"}).
    repeat_key text,
    columns jsonb NOT NULL DEFAULT '{}',
    -- D189: Split by, Color by, Seeds and the panel layout.
    view jsonb NOT NULL DEFAULT '{}',
    created_by bigint REFERENCES users ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_pk, name)
);

-- A group's runs and dropped runs, by run pk so a renamed run stays in its groups (D183). A
-- deleted run's rows go with it; a run moved to another project has its rows removed by the
-- move. `added_by` says how a member joined, for the edit page (D190); null when dropped.
CREATE TABLE run_group_runs (
    group_pk bigint NOT NULL REFERENCES run_groups ON DELETE CASCADE,
    run_pk bigint NOT NULL REFERENCES runs ON DELETE CASCADE,
    status text NOT NULL CHECK (status IN ('member', 'dropped')),
    added_by text CHECK (added_by IN ('filter', 'hand')),
    changed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (group_pk, run_pk)
);
-- Runs in no group, the ungrouped runs (D187), and the run's rows a move removes.
CREATE INDEX run_group_runs_run ON run_group_runs (run_pk);

-- Dismissed suggestions (D186), by signature: the sha256 of their sorted run ids. A suggestion
-- whose runs change has a new signature and is offered again.
CREATE TABLE run_group_dismissals (
    project_pk bigint NOT NULL REFERENCES projects ON DELETE CASCADE,
    signature bytea NOT NULL,
    PRIMARY KEY (project_pk, signature)
);

-- The project's "best by" (D184): a summary column and 'asc' or 'desc'. Null until someone
-- picks one; until then it defaults to the first of summary.val/loss, summary.eval/loss and
-- summary.loss the project has, lowest first.
ALTER TABLE projects ADD COLUMN best_key text,
    ADD COLUMN best_order text CHECK (best_order IN ('asc', 'desc'));

-- The assistant on the group pages (D191) is off until an org admin turns it on.
ALTER TABLE orgs ADD COLUMN assistant_enabled boolean NOT NULL DEFAULT false;
