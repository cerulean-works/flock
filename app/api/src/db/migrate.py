import logging
from pathlib import Path

import asyncpg

log = logging.getLogger(__name__)


class Migrations:
    """The SQL files in ``directory``, applied in name order (``NNNN_description.sql``)."""

    # Advisory lock key serializing concurrent runs (e.g. several containers starting at once).
    LOCK = 0x7472_6163_6B65_72  # Stable migration lock; keep the key across releases.

    def __init__(self, directory: Path = Path(__file__).parent / "migrations") -> None:
        self.directory = directory

    def files(self) -> list[Path]:
        return sorted(self.directory.glob("*.sql"))

    async def applied(self, conn: asyncpg.Connection) -> set[str]:
        if not await conn.fetchval("SELECT to_regclass('schema_migrations') IS NOT NULL"):
            return set()
        return {row[0] for row in await conn.fetch("SELECT version FROM schema_migrations")}

    async def pending(self, conn: asyncpg.Connection) -> list[str]:
        applied = await self.applied(conn)
        return [path.stem for path in self.files() if path.stem not in applied]

    async def apply(self, dsn: str) -> list[str]:
        """Apply pending migrations, each in its own transaction; returns the versions applied."""
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute("SELECT pg_advisory_lock($1)", self.LOCK)
            try:
                # The runner's own bookkeeping, which must exist before any migration runs.
                await conn.execute(
                    "CREATE TABLE IF NOT EXISTS schema_migrations ("
                    "version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
                )
                applied = await self.applied(conn)
                done = []
                for path in self.files():
                    if path.stem in applied:
                        continue
                    log.info("applying migration %s", path.name)
                    async with conn.transaction():
                        await conn.execute(path.read_text())
                        await conn.execute(
                            "INSERT INTO schema_migrations (version) VALUES ($1)", path.stem
                        )
                    done.append(path.stem)
                return done
            finally:
                await conn.execute("SELECT pg_advisory_unlock($1)", self.LOCK)
        finally:
            await conn.close()
