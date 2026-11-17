import argparse
import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

import asyncpg

log = logging.getLogger(__name__)

# The first line of a migration that runs outside a transaction (D149).
NO_TRANSACTION = "-- flock: no-transaction"


class SchemaError(RuntimeError):
    pass


@dataclass(frozen=True)
class Migration:
    """One SQL file: an expand file ``NNNN_name.sql`` or a contract file
    ``NNNN_name.contract.sql``, numbered in one sequence (D146)."""

    path: Path

    @property
    def number(self) -> int:
        return int(self.path.name[:4])

    @property
    def version(self) -> str:
        """What ``schema_migrations`` records: the file name without ``.sql``."""
        return self.path.name.removesuffix(".sql")

    @property
    def contract(self) -> bool:
        return self.version.endswith(".contract")

    def sql(self) -> str:
        return self.path.read_text()

    @property
    def transaction(self) -> bool:
        lines = self.sql().splitlines()
        return not lines or lines[0].strip() != NO_TRANSACTION


class Migrations:
    """The SQL files in ``directory``, applied in name order. Expand files are applied on
    startup, and contract files once every running server knows them (D146, D147)."""

    # Advisory lock key serializing concurrent runs (e.g. several containers starting at once).
    LOCK = 0x7472_6163_6B65_72  # Stable migration lock; keep the key across releases.
    # Held while a contract is checked and applied, and shared by an instance registering, so
    # a contract never runs while a server that doesn't know it is starting (D147).
    INSTANCES_LOCK = LOCK + 1
    # Seconds without a heartbeat after which an instance counts as gone (D147).
    GONE = 60.0

    def __init__(
        self,
        directory: Path = Path(__file__).parent / "migrations",
        lock_timeout: float = 5.0,
        attempts: int = 10,
        retry_delay: float = 3.0,
    ) -> None:
        self.directory = directory
        # A file waiting longer than lock_timeout for a lock is rolled back and tried again
        # after retry_delay, up to attempts times (D149).
        self.lock_timeout = lock_timeout
        self.attempts = attempts
        self.retry_delay = retry_delay

    def files(self) -> list[Path]:
        return sorted(self.directory.glob("*.sql"))

    def migrations(self) -> list[Migration]:
        return [Migration(path) for path in self.files()]

    @property
    def last(self) -> int:
        """The highest migration number of this release, which its servers know (D147)."""
        return max((migration.number for migration in self.migrations()), default=0)

    async def applied(self, conn: asyncpg.Connection) -> set[str]:
        if not await conn.fetchval("SELECT to_regclass('schema_migrations') IS NOT NULL"):
            return set()
        return {row[0] for row in await conn.fetch("SELECT version FROM schema_migrations")}

    async def pending(self, conn: asyncpg.Connection) -> list[str]:
        """The expand migrations not applied yet. Contracts don't count, since they wait."""
        applied = await self.applied(conn)
        return [m.version for m in self.migrations() if not m.contract and m.version not in applied]

    async def check(self, conn: asyncpg.Connection) -> None:
        """Refuse a database with a contract applied that this release doesn't know (D148).
        Expand migrations it doesn't know are fine, since the release before them still works
        on what they leave."""
        newer = sorted(
            version
            for version in await self.applied(conn)
            if version.endswith(".contract") and int(version[:4]) > self.last
        )
        if newer:
            names = ", ".join(version.removesuffix(".contract") for version in newer)
            raise SchemaError(
                f"database has contract migrations this release doesn't know ({names}): "
                f"deploy a release from {newer[-1][:4]} or later"
            )

    async def apply(self, dsn: str) -> list[str]:
        """Apply pending expand migrations; returns the versions applied."""
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute("SELECT pg_advisory_lock($1)", self.LOCK)
            try:
                # The runner's own bookkeeping, which must exist before any migration runs.
                await conn.execute(
                    "CREATE TABLE IF NOT EXISTS schema_migrations ("
                    "version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
                )
                await self.check(conn)
                applied = await self.applied(conn)
                done = []
                for migration in self.migrations():
                    if migration.contract or migration.version in applied:
                        continue
                    await self._run(conn, migration)
                    done.append(migration.version)
                return done
            finally:
                await conn.execute("SELECT pg_advisory_unlock($1)", self.LOCK)
        finally:
            await conn.close()

    async def apply_contracts(
        self, dsn: str, delay: float, force: bool = False, wait: bool = True
    ) -> list[str]:
        """Apply the contract migrations that are due (D147), in order, stopping at the first
        that isn't or at an expand migration not applied yet; returns the versions applied.
        ``force`` applies them without waiting for old servers or ``delay``, and without
        ``wait`` nothing is done while another process holds the migration lock."""
        if not any(migration.contract for migration in self.migrations()):
            return []
        conn = await asyncpg.connect(dsn)
        try:
            if wait:
                await conn.execute("SELECT pg_advisory_lock($1)", self.LOCK)
            elif not await conn.fetchval("SELECT pg_try_advisory_lock($1)", self.LOCK):
                return []
            try:
                applied = await self.applied(conn)
                done = []
                for migration in self.migrations():
                    if migration.version in applied:
                        continue
                    if not migration.contract:
                        log.info("contract migrations wait for %s to be applied", migration.version)
                        break
                    await conn.execute("SELECT pg_advisory_lock($1)", self.INSTANCES_LOCK)
                    try:
                        if not force and not await self._due(conn, migration.number, delay):
                            break
                        await self._run(conn, migration)
                        await conn.execute(
                            "DELETE FROM flock_contract_waits WHERE number = $1", migration.number
                        )
                    finally:
                        await conn.execute("SELECT pg_advisory_unlock($1)", self.INSTANCES_LOCK)
                    done.append(migration.version)
                return done
            finally:
                await conn.execute("SELECT pg_advisory_unlock($1)", self.LOCK)
        finally:
            await conn.close()

    async def _due(self, conn: asyncpg.Connection, number: int, delay: float) -> bool:
        """Whether contract ``number`` is due: every live instance knows it, and has for
        ``delay`` seconds.

        Instances that don't know it leave no trace once they're gone, so how long the first
        condition has held is kept in ``flock_contract_waits``: a check that finds it true
        starts the clock if it isn't running, and one that finds it false stops it. An
        instance that doesn't know the contract stops the clock itself when it registers
        (``Instance.register``), so one that comes and goes between two checks still restarts
        the delay. The clock starts at the first check after the last old instance went away,
        up to a check interval late, which only makes the wait a little longer."""
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM flock_instances WHERE seen_at < now() - make_interval(secs => $1)",
                self.GONE,
            )
            if await conn.fetchval(
                "SELECT EXISTS (SELECT FROM flock_instances WHERE knows::int < $1)", number
            ):
                await conn.execute("DELETE FROM flock_contract_waits WHERE number = $1", number)
                return False
            return await conn.fetchval(
                "INSERT INTO flock_contract_waits (number) VALUES ($1) "
                "ON CONFLICT (number) DO UPDATE SET number = excluded.number "
                "RETURNING since <= now() - make_interval(secs => $2)",
                number,
                delay,
            )

    async def _run(self, conn: asyncpg.Connection, migration: Migration) -> None:
        """Apply one file and record it, trying again while it times out on a lock (D149)."""
        sql = migration.sql()
        timeout = f"'{round(self.lock_timeout * 1000)}ms'"
        record = "INSERT INTO schema_migrations (version) VALUES ($1)"
        attempt = 1
        while True:
            log.info("applying migration %s", migration.path.name)
            try:
                if migration.transaction:
                    async with conn.transaction():
                        await conn.execute(f"SET LOCAL lock_timeout = {timeout}")
                        await conn.execute(sql)
                        await conn.execute(record, migration.version)
                else:
                    # Such a file holds one statement written to be run again, since a failure
                    # partway can't be rolled back.
                    await conn.execute(f"SET lock_timeout = {timeout}")
                    try:
                        await conn.execute(sql)
                    finally:
                        await conn.execute("RESET lock_timeout")
                    await conn.execute(record, migration.version)
                return
            except asyncpg.LockNotAvailableError:
                if attempt >= self.attempts:
                    raise
                log.warning(
                    "migration %s timed out waiting for a lock (attempt %d of %d), trying again",
                    migration.path.name,
                    attempt,
                    self.attempts,
                )
                attempt += 1
                await asyncio.sleep(self.retry_delay)


async def main(argv: list[str] | None = None, migrations: Migrations | None = None) -> None:
    """Apply pending migrations, then the backfill they need (D83's migration Job:
    ``python -m app.api.src.db.migrate``, with ``FLOCK_*`` settings from the environment).
    With ``--contract``, apply the contract migrations that are due instead (D147)."""
    from app.api.src.db.backfill import backfill
    from app.api.src.settings import Settings

    parser = argparse.ArgumentParser(prog="python -m app.api.src.db.migrate")
    parser.add_argument(
        "--contract",
        action="store_true",
        help="apply the contract migrations that are due, instead of the expand migrations",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="with --contract, apply them without waiting for old servers or the contract delay",
    )
    args = parser.parse_args(argv)
    if args.force and not args.contract:
        parser.error("--force only goes with --contract")
    settings = Settings.load()
    logging.basicConfig(
        level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    migrations = migrations or Migrations()
    if args.contract:
        applied = await migrations.apply_contracts(
            settings.database_url, settings.contract_delay, force=args.force
        )
        log.info("applied %d contract migrations", len(applied))
        return
    applied = await migrations.apply(settings.database_url)
    log.info("applied %d migrations", len(applied))
    await backfill(settings.database_url)


if __name__ == "__main__":
    asyncio.run(main())
