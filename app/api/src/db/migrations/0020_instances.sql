-- The processes using the database (020): contract migrations wait for the oldest (D147).

-- Every API process and S3 proxy worker, with the highest migration number its release ships
-- (`knows`, as four digits). Each updates `seen_at` every 15 seconds and deletes its row when it
-- stops. A row not seen for 60 seconds belongs to a process that died, and counts as gone.
CREATE TABLE flock_instances (
    id uuid PRIMARY KEY,
    service text NOT NULL CHECK (service IN ('api', 's3')),
    knows text NOT NULL,
    started_at timestamptz NOT NULL DEFAULT now(),
    seen_at timestamptz NOT NULL DEFAULT now()
);

-- Since when every live instance has known contract migration `number`, as last seen by a
-- check. A row is removed when an instance that doesn't know it shows up, so the contract delay
-- counts from when the last such instance went away.
CREATE TABLE flock_contract_waits (
    number integer PRIMARY KEY,
    since timestamptz NOT NULL DEFAULT now()
);
