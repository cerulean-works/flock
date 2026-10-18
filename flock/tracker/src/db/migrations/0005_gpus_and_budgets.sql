-- GPU tracking and budgets: the GPUs each run used (detected from the wandb metadata, or set
-- by hand), a ledger of how long runs were alive per month (kept when runs are deleted, so
-- spend never comes back), and per-org GPU prices and monthly budgets.

-- Detected from wandb-metadata.json: a null count means not known yet, 0 means no GPU.
ALTER TABLE runs ADD COLUMN gpu_type text;
ALTER TABLE runs ADD COLUMN gpu_count integer CHECK (gpu_count >= 0);
-- Set through the API; they take precedence over what was detected.
ALTER TABLE runs ADD COLUMN gpu_type_override text;
ALTER TABLE runs ADD COLUMN gpu_count_override integer CHECK (gpu_count_override >= 0);

-- A display label for the org's prices and budgets.
ALTER TABLE orgs ADD COLUMN currency text NOT NULL DEFAULT 'USD';

-- What one GPU of a type costs per hour; types match case-insensitively.
CREATE TABLE gpu_rates (
    org_pk bigint NOT NULL REFERENCES orgs ON DELETE CASCADE,
    gpu_type text NOT NULL,
    hourly_cost double precision NOT NULL CHECK (hourly_cost >= 0)
);
CREATE UNIQUE INDEX gpu_rates_org_type ON gpu_rates (org_pk, lower(gpu_type));

-- The monthly budget from ``month`` onward (until a later row); a null amount is no budget.
CREATE TABLE org_budgets (
    org_pk bigint NOT NULL REFERENCES orgs ON DELETE CASCADE,
    month date NOT NULL CHECK (extract(day FROM month) = 1),
    amount double precision CHECK (amount >= 0),
    PRIMARY KEY (org_pk, month)
);

-- Seconds a run was alive in a UTC month, with the GPUs it had then. Rows outlive their run
-- (and project), so deleting runs doesn't give budget back.
CREATE TABLE gpu_usage (
    pk bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    org_pk bigint NOT NULL REFERENCES orgs ON DELETE CASCADE,
    project_pk bigint REFERENCES projects ON DELETE SET NULL,
    run_pk bigint REFERENCES runs ON DELETE SET NULL,
    run_name text NOT NULL,
    month date NOT NULL CHECK (extract(day FROM month) = 1),
    seconds double precision NOT NULL DEFAULT 0,
    gpu_type text,
    gpu_count integer,
    UNIQUE (run_pk, month)
);
CREATE INDEX gpu_usage_org_month ON gpu_usage (org_pk, month);

-- Existing runs: alive from creation to their last heartbeat, split by month. (Gaps between a
-- preemption and a resume can't be told apart here; from now on they aren't counted.)
INSERT INTO gpu_usage (org_pk, project_pk, run_pk, run_name, month, seconds)
SELECT p.org_pk, p.pk, r.pk, r.name, m.month::date,
       extract(epoch FROM least(r.heartbeat_at AT TIME ZONE 'UTC', m.month + interval '1 month')
                          - greatest(r.created_at AT TIME ZONE 'UTC', m.month))
FROM runs r
JOIN projects p ON p.pk = r.project_pk
CROSS JOIN LATERAL generate_series(
    date_trunc('month', r.created_at AT TIME ZONE 'UTC'),
    date_trunc('month', r.heartbeat_at AT TIME ZONE 'UTC'),
    interval '1 month'
) AS m(month)
WHERE r.heartbeat_at > r.created_at;
