-- Command-line sign-ins (`flock login`) are sessions too. A CLI session keeps its token's
-- SHA-256 in `refresh_hash`, which is never rotated; `name` is the machine's hostname.
-- `last_used_at` and `expires_at` follow its use, written at most once an hour.

ALTER TABLE sessions
    ADD COLUMN kind text NOT NULL DEFAULT 'web' CHECK (kind IN ('web', 'cli')),
    ADD COLUMN name text,
    ADD COLUMN last_used_at timestamptz;
