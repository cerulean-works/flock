from typing import Any

import asyncpg

from src.db.migrate import Migrations


class SchemaError(RuntimeError):
    pass


class Database:
    # Advisory lock held for the server's lifetime: the server keeps its working set in memory,
    # so two servers writing the same database would silently diverge.
    SERVER_LOCK = 0x7472_6163_6B65_73  # "trackes"

    def __init__(self, dsn: str, min_size: int = 2, max_size: int = 16) -> None:
        self.dsn = dsn
        self.min_size = min_size
        self.max_size = max_size
        self._pool: asyncpg.Pool | None = None
        self._lock_conn: asyncpg.Connection | None = None

    @property
    def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("database is not open")
        return self._pool

    async def open(self, migrate: bool = True) -> None:
        """Connect, taking the server lock; ``migrate`` applies pending migrations first."""
        conn = await asyncpg.connect(self.dsn)
        try:
            if not await conn.fetchval("SELECT pg_try_advisory_lock($1)", self.SERVER_LOCK):
                raise SchemaError("another tracker server is already using this database")
            if migrate:
                await Migrations().apply(self.dsn)
            pending = await Migrations().pending(conn)
            if pending:
                raise SchemaError(
                    f"database has unapplied migrations ({', '.join(pending)}) and "
                    "TRACKER_MIGRATE_ON_STARTUP is off"
                )
        except BaseException:
            await conn.close()
            raise
        self._lock_conn = conn
        self._pool = await asyncpg.create_pool(
            self.dsn, min_size=self.min_size, max_size=self.max_size
        )

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
        if self._lock_conn is not None:
            await self._lock_conn.close()  # releases the server lock
            self._lock_conn = None

    async def fetch(self, query: str, *args: Any) -> list[asyncpg.Record]:
        return await self.pool.fetch(query, *args)

    async def fetchval(self, query: str, *args: Any) -> Any:
        return await self.pool.fetchval(query, *args)

    async def execute(self, query: str, *args: Any) -> str:
        return await self.pool.execute(query, *args)
