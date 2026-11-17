"""This process's row in ``flock_instances`` (D147), which contract migrations wait for."""

import asyncio
import contextlib
import logging
import uuid

import asyncpg

from app.api.src.db.migrate import Migrations

log = logging.getLogger(__name__)


class Instance:
    """An API process or S3 proxy worker, and the highest migration its release ships. It
    registers on start, updates ``seen_at`` every ``heartbeat`` seconds and deletes its row on
    close. A process killed outright stops updating it and counts as gone after
    ``Migrations.GONE`` seconds."""

    HEARTBEAT = 15.0

    def __init__(
        self,
        pool: asyncpg.Pool,
        service: str,
        migrations: Migrations | None = None,
        heartbeat: float = HEARTBEAT,
    ) -> None:
        self.id = uuid.uuid4()
        self.pool = pool
        self.service = service
        self.migrations = migrations or Migrations()
        self.knows = f"{self.migrations.last:04d}"
        self.heartbeat = heartbeat
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        await self.register()
        self._task = asyncio.get_running_loop().create_task(self._beat())

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        try:
            await self.pool.execute("DELETE FROM flock_instances WHERE id = $1", self.id)
        except Exception:
            log.exception("could not remove this instance from flock_instances")

    async def register(self) -> None:
        """Add this process's row (or bring it back), and refuse a database with a contract
        applied that this release doesn't know (D148)."""
        async with self.pool.acquire() as conn, conn.transaction():
            # Waits while a contract is checked and applied, so either that contract sees this
            # row and waits, or this sees the applied contract and refuses.
            await conn.execute("SELECT pg_advisory_xact_lock_shared($1)", Migrations.INSTANCES_LOCK)
            await self.migrations.check(conn)
            # Contracts this release doesn't know wait again from when it goes (D147).
            await conn.execute(
                "DELETE FROM flock_contract_waits WHERE number > $1", self.migrations.last
            )
            await conn.execute(
                "INSERT INTO flock_instances (id, service, knows) VALUES ($1, $2, $3) "
                "ON CONFLICT (id) DO UPDATE SET seen_at = now()",
                self.id,
                self.service,
                self.knows,
            )

    async def beat(self) -> None:
        """Update ``seen_at``. A process that missed its heartbeats for ``GONE`` seconds (one
        that was paused) has counted as gone, so it registers again, which restarts the wait
        of contracts it doesn't know."""
        fresh = await self.pool.fetchval(
            "UPDATE flock_instances SET seen_at = now() "
            "WHERE id = $1 AND seen_at >= now() - make_interval(secs => $2) RETURNING true",
            self.id,
            Migrations.GONE,
        )
        if not fresh:
            await self.register()

    async def _beat(self) -> None:
        while True:
            await asyncio.sleep(self.heartbeat)
            try:
                await self.beat()
            except Exception:
                log.exception("instance heartbeat failed")
