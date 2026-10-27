"""Backfill what migration 0013 (012, D77/D78) leaves to Python: the run table's values, the
columns of every project, each run's metrics and metric definitions, and the series chunks of
streams that weren't compacted.

It resumes where it stopped, by run pk, so it is safe to interrupt and run again.
``python -m app.api.src.db.migrate`` runs it after the migrations (the migration Job does), and
a server applying migrations on startup runs it before serving; run it alone with:

    cd app/api
    uv run --package flock-app-api python -m app.api.scripts.backfill \\
        --database-url postgresql://flock:flock@127.0.0.1:5432/flock
"""

import argparse
import asyncio
import os

from app.api.src.db.backfill import backfill


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.partition("\n\n")[0])
    parser.add_argument(
        "--database-url",
        default=os.environ.get(
            "FLOCK_DATABASE_URL", "postgresql://flock:flock@127.0.0.1:5432/flock"
        ),
        help="PostgreSQL URL (default: FLOCK_DATABASE_URL)",
    )
    args = parser.parse_args()
    done = asyncio.run(backfill(args.database_url, report=print))
    print(f"backfilled {done} runs" if done else "nothing to backfill")


if __name__ == "__main__":
    main()
