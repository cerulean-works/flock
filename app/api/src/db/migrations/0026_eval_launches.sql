-- Evals launched from the web app on Modal (026's D188 to D192). One row per launch. The replica
-- that started it watches the Modal call and heartbeats; any replica takes over a launch whose
-- heartbeat stopped, since the call runs on Modal whatever happens to the replica.
--
-- `model` and `sha` are the Hugging Face repository and the commit its revision resolved to;
-- `run` is the tracker run the worker logs to, named before it starts. `api_key_pk` is the wandb
-- key made for this launch and deleted when it ends: the key itself is never stored. `cap_usd`
-- and `usd_per_hour` are the cap and price at launch, which bound what it may cost.
CREATE TABLE eval_launches (
    pk bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    org_pk bigint NOT NULL REFERENCES orgs ON DELETE CASCADE,
    project text NOT NULL,
    model text NOT NULL,
    revision text NOT NULL,
    sha text NOT NULL,
    suites text[] NOT NULL DEFAULT '{}',
    tasks text[] NOT NULL DEFAULT '{}',
    add_bos boolean NOT NULL DEFAULT false,
    run text NOT NULL,
    state text NOT NULL CHECK (state IN ('running', 'finished', 'failed', 'cancelled')),
    call_id text,
    api_key_pk bigint REFERENCES api_keys ON DELETE SET NULL,
    cap_usd float8 NOT NULL,
    usd_per_hour float8 NOT NULL,
    flock_commit text,
    error text,
    started_by bigint REFERENCES users ON DELETE SET NULL,
    started_at timestamptz NOT NULL,
    finished_at timestamptz,
    heartbeat_at timestamptz
);
CREATE INDEX eval_launches_org ON eval_launches (org_pk, pk DESC);
CREATE INDEX eval_launches_running ON eval_launches (heartbeat_at) WHERE state = 'running';
CREATE INDEX eval_launches_month ON eval_launches (started_at);
