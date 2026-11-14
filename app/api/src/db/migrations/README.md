# Migrations

Each file here is one numbered step, applied once and recorded in `schema_migrations`. Numbers
form one sequence with no gaps. Start a file with a comment that says what it does and which
design doc it comes from, as the existing ones do.

During a deploy the new release starts while the old one still serves, on the same database. So
a migration has two kinds of file (design doc 020, D146).

## Expand files

`NNNN_name.sql` only adds things: tables, nullable columns or columns with a default, indexes,
functions, triggers, and data copied into new columns. The release before it must keep working
on the schema it leaves. Every API server applies pending expand files on startup, before it
serves.

A test (`tests/app/test_expand_files.py`) fails when an expand file drops a table or column,
renames anything, changes a column's type, sets `NOT NULL`, or adds a `NOT NULL` column without a
default. Those belong in a contract file.

## Contract files

`NNNN_name.contract.sql` removes what an earlier expand replaced. It drops a column, a table or a
sync trigger, or adds `NOT NULL` once every row is filled. Flock applies it by itself once every
running API server and S3 proxy worker knows it, and that has held for `FLOCK_CONTRACT_DELAY`
(30 minutes by default). Until then the previous release can still be rolled back to.

A release refuses to start on a database where a contract it doesn't know was applied.

With `FLOCK_MIGRATE_ON_STARTUP` off, run `python -m app.api.src.db.migrate --contract` now and
then (a cron job, say) to apply the contracts that are due. Add `--force` to apply them without
waiting, only when nothing else uses the database.

## Renaming a column

Renaming `projects.name` to `display_name` takes two files, usually in two releases.

1. `0021_project_display_name.sql` adds `display_name`, copies `name` into it, and adds a
   trigger that keeps both in step on write. The code of this release reads and writes
   `display_name`. The release before keeps using `name`, and the trigger keeps
   `display_name` current for it.
2. `0022_drop_project_name.contract.sql` drops the trigger and `name`. It can ship in the same
   release if nothing still writes `name`, since it waits for the old release to go anyway.

Data copies too slow for one transaction go in the backfill (`db/backfill.py`) instead.

## Locks

Each file runs with a 5 second lock timeout. If it waits longer for a table that a long query
holds, it's rolled back and tried again a few seconds later, up to 10 times, and then the server
fails to start. This keeps an `ALTER TABLE` from queueing every other query behind it.

## Without a transaction

A file whose first line is `-- flock: no-transaction` runs outside a transaction. Use it for
`CREATE INDEX CONCURRENTLY`, since a plain `CREATE INDEX` on a big table blocks writes while it
builds. Such a file holds one statement, written to be run again (`IF NOT EXISTS`), because a
failure partway can't be rolled back.

```sql
-- flock: no-transaction
-- Runs by project, for the run list (021).
CREATE INDEX CONCURRENTLY IF NOT EXISTS runs_project ON runs (project_pk, created_at);
```

## Breaking on purpose

If a statement must run at expand time (a column nothing ever read, say), put a
`-- flock: allow-breaking <reason>` line right above it. Say in the PR that the old release may
see errors for a short time during the deploy.
