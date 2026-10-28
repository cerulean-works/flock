"""PostgreSQL's ``LISTEN``/``NOTIFY`` for every replica (D79, D84): one listening connection
per process, with reconnects, and callbacks per channel.

What a replica derives from notifications (cached credentials, the S3 proxy's directory, live
dashboard streams) is only correct while it hears every one of them. So whenever the listening
connection is made or lost, every ``on_reset`` callback runs and drops what it derived, and
``listening`` says whether to trust notifications at all: while it is False, nothing may be
cached.
"""

import asyncio
import contextlib
import logging
from collections.abc import Callable

import asyncpg

log = logging.getLogger(__name__)


class Bus:
    """The listening connection, made outside any pool: a pooler in transaction mode (such as
    PgBouncer) drops ``LISTEN``, so ``dsn`` must reach PostgreSQL directly (D83)."""

    # Seconds between reconnect attempts, and between checks that an idle connection is alive
    # (a dead network may never close the socket).
    RETRY = 1.0
    KEEPALIVE = 10.0

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self.listening = False
        # Bumped on every reset: a value loaded under an older generation may have missed a
        # notification, so caches compare it before keeping what they loaded.
        self.generation = 0
        self._channels: dict[str, list[Callable[[str], None]]] = {}
        self._resets: list[Callable[[], None]] = []
        self._task: asyncio.Task | None = None

    def listen(self, channel: str, callback: Callable[[str], None]) -> None:
        """Call ``callback(payload)`` for each notification on ``channel`` (before ``start``)."""
        self._channels.setdefault(channel, []).append(callback)

    def on_reset(self, callback: Callable[[], None]) -> None:
        """Call ``callback()`` whenever the listening connection is made or lost."""
        self._resets.append(callback)

    async def start(self, timeout: float = 10.0) -> None:
        """Start listening; returns once the first connection listens (or failed to)."""
        connected = asyncio.Event()
        self._task = asyncio.create_task(self._run(connected))
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(connected.wait(), timeout)

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    def _reset(self) -> None:
        self.generation += 1
        for callback in self._resets:
            try:
                callback()
            except Exception:
                log.exception("a bus reset callback failed")

    def _dispatch(self, conn: object, pid: int, channel: str, payload: object) -> None:
        for callback in self._channels.get(channel, ()):
            try:
                callback(str(payload))
            except Exception:
                log.exception("a %s listener failed", channel)

    async def _run(self, connected: asyncio.Event) -> None:
        while True:
            closed = asyncio.Event()
            conn = None
            try:
                conn = await asyncpg.connect(self.dsn)
                conn.add_termination_listener(lambda _, closed=closed: closed.set())
                for channel in self._channels:
                    await conn.add_listener(channel, self._dispatch)
                self.listening = True
                self._reset()  # anything derived before now may have missed a notification
                connected.set()
                while not closed.is_set():
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(closed.wait(), self.KEEPALIVE)
                    if not closed.is_set():
                        await conn.execute("SELECT 1", timeout=self.KEEPALIVE)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("listening on %s failed; retrying", ", ".join(self._channels))
                connected.set()
            finally:
                if self.listening:
                    self.listening = False
                    self._reset()
                if conn is not None and not conn.is_closed():
                    with contextlib.suppress(Exception):
                        await asyncio.shield(conn.close(timeout=1))
            await asyncio.sleep(self.RETRY)
