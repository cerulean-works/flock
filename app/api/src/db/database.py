from typing import Any

import asyncpg

from app.api.src.db import backfill
from app.api.src.db.migrate import Migrations


class SchemaError(RuntimeError):
    pass


class Database:
    """The connection pool. Any number of servers share a database (D83): nothing is held in
    memory, so there is no server lock."""

    def __init__(self, dsn: str, min_size: int = 2, max_size: int = 16) -> None:
        self.dsn = dsn
        self.min_size = min_size
        self.max_size = max_size
        self._pool: asyncpg.Pool | None = None

    @property
    def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("database is not open")
        return self._pool

    async def open(self, migrate: bool = True) -> None:
        """Connect; ``migrate`` applies pending migrations (and their backfill) first."""
        conn = await asyncpg.connect(self.dsn)
        try:
            if migrate:
                await Migrations().apply(self.dsn)
                await backfill.backfill(self.dsn)
            pending = await Migrations().pending(conn)
            if pending:
                raise SchemaError(
                    f"database has unapplied migrations ({', '.join(pending)}) and "
                    "FLOCK_MIGRATE_ON_STARTUP is off"
                )
            if await backfill.pending(conn):
                raise SchemaError(
                    "database migrations wait for their backfill and FLOCK_MIGRATE_ON_STARTUP "
                    "is off: run python -m app.api.src.db.migrate"
                )
        finally:
            await conn.close()
        self._pool = await asyncpg.create_pool(
            self.dsn, min_size=self.min_size, max_size=self.max_size
        )

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def fetch(self, query: str, *args: Any) -> list[asyncpg.Record]:
        return await self.pool.fetch(query, *args)

    async def fetchrow(self, query: str, *args: Any) -> asyncpg.Record | None:
        return await self.pool.fetchrow(query, *args)

    async def fetchval(self, query: str, *args: Any) -> Any:
        return await self.pool.fetchval(query, *args)

    async def execute(self, query: str, *args: Any) -> str:
        return await self.pool.execute(query, *args)
