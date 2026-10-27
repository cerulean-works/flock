import asyncio
import logging
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, Any, ClassVar

import numpy as np
import orjson

from app.api.src.core.history import History
from app.api.src.utils import parse_rows

if TYPE_CHECKING:
    import asyncpg

log = logging.getLogger(__name__)

# Every replica's ``Hub`` is fed by notifications on this channel (D79), sent by writers inside
# their write transactions, so a dashboard on any replica sees a write on any other.
CHANNEL = "flock_live"
# NOTIFY payloads are at most 8000 bytes: larger messages go in ``bus_messages``, and the
# notification names their row as ``@<pk>``.
PAYLOAD_LIMIT = 7900


def points_message(project: int, run: int, name: str, stream: str, start: int, end: int) -> str:
    """New lines ``[start, end)`` of a run's ``stream``: replicas watching the run read them
    and send their points (``Hub.points_event``)."""
    message = {"p": project, "k": run, "r": name, "s": stream, "f": start, "t": end}
    return orjson.dumps(message).decode()


def event_message(project: int, run: str | None, event: str, data: Any) -> str:
    """An event for the subscribers of ``project`` watching ``run`` (None: all of them)."""
    message = {"p": project, "r": run, "e": event, "d": data}
    return orjson.dumps(message, option=orjson.OPT_SERIALIZE_NUMPY).decode()


def close_message(project: int) -> str:
    """End every stream of ``project`` (renamed or deleted), after the events before it."""
    return orjson.dumps({"p": project, "close": True}).decode()


async def publish(conn: asyncpg.Connection, messages: Sequence[str]) -> None:
    """Send ``messages`` on ``CHANNEL``, in order, when ``conn``'s transaction commits."""
    if not messages:
        return
    payloads = []
    for message in messages:
        if len(message.encode()) > PAYLOAD_LIMIT:
            pk = await conn.fetchval(
                "INSERT INTO bus_messages (payload) VALUES ($1) RETURNING pk", message
            )
            message = f"@{pk}"
        payloads.append(message)
    await conn.execute(
        "SELECT pg_notify($1, payload) FROM unnest($2::text[]) AS t(payload)", CHANNEL, payloads
    )


class Subscriber:
    """One dashboard connection: the runs it shows and a bounded queue of encoded events."""

    __slots__ = ("closed", "queue", "runs", "system")

    # Events a subscriber may fall behind by before it is disconnected (and reconnects).
    QUEUE_SIZE = 512

    def __init__(self, runs: Iterable[str], system: bool = False) -> None:
        self.runs = frozenset(runs)
        self.system = system  # also wants system metric samples
        self.queue: asyncio.Queue[bytes | None] = asyncio.Queue(self.QUEUE_SIZE)
        self.closed = False

    def push(self, event: bytes) -> None:
        if self.closed:
            return
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            # Too slow to keep up: end the stream; the client reconnects and replays.
            self.close()

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        while True:  # make room for the end-of-stream marker
            try:
                self.queue.put_nowait(None)
                return
            except asyncio.QueueFull:
                self.queue.get_nowait()


class Hub:
    """Dashboard subscribers per project, and the Server-Sent Events they receive."""

    def __init__(self) -> None:
        self._subscribers: dict[int, set[Subscriber]] = {}  # by project pk

    @property
    def idle(self) -> bool:
        """Whether no dashboard is connected to this replica."""
        return not self._subscribers

    @staticmethod
    def event(name: str, data: Any) -> bytes:
        """Encode one Server-Sent Event; NaN/inf serialize as ``null``."""
        payload = orjson.dumps(data, option=orjson.OPT_SERIALIZE_NUMPY)
        return b"event: " + name.encode() + b"\ndata: " + payload + b"\n\n"

    @staticmethod
    def points_event(
        stream: str,
        run: str,
        lines: int,
        points: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]],
    ) -> bytes:
        """New points of one run's ``stream`` ("history" or "system"), named after it.

        Per metric: the line numbers, x values (``_step`` or ``_runtime``) and values.
        """
        series = {key: {"rows": rows, "x": x, "y": y} for key, (rows, x, y) in points.items()}
        return Hub.event(stream, {"run": run, "lines": lines, "series": series})

    def subscribe(self, project: int, runs: Iterable[str], system: bool = False) -> Subscriber:
        """Subscribe to runs of the project with primary key ``project``."""
        sub = Subscriber(runs, system)
        self._subscribers.setdefault(project, set()).add(sub)
        return sub

    def unsubscribe(self, project: int, sub: Subscriber) -> None:
        subs = self._subscribers.get(project)
        if subs is not None:
            subs.discard(sub)
            if not subs:
                del self._subscribers[project]

    def watching(self, project: int, run: str) -> list[Subscriber]:
        """Subscribers interested in ``run`` (empty in the common no-dashboard case)."""
        subs = self._subscribers.get(project)
        return [s for s in subs if run in s.runs] if subs else []

    def broadcast(self, project: int, event: bytes) -> None:
        for sub in self._subscribers.get(project, ()):
            sub.push(event)

    def close_project(self, project: int) -> None:
        """End every stream of a project (after it was renamed or deleted)."""
        for sub in self._subscribers.pop(project, ()):
            sub.close()

    def close(self) -> None:
        for subs in self._subscribers.values():
            for sub in subs:
                sub.close()


class Relay:
    """Feeds a replica's ``Hub`` from ``CHANNEL`` (D79): every write on every replica, in the
    order they committed.

    Points are read from the lines a notification names, once per notification, and only
    when a subscriber here watches the run. Messages are handled one at a time, so a run's
    events reach dashboards in order. When the listening connection is made or lost, every
    stream ends (``reset``): dashboards reconnect and replay what they missed (D7).
    """

    # Raw lines and the x axis of the points of each series stream.
    STREAMS: ClassVar[dict[str, tuple[str, str]]] = {
        "history": ("history", "_step"),
        "system": ("events", "_runtime"),
    }

    def __init__(self, hub: Hub, pool: Any) -> None:
        self.hub = hub
        self.pool = pool  # an asyncpg pool once the database is open
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._task: asyncio.Task | None = None

    def receive(self, payload: str) -> None:
        if self.hub.idle:
            return  # nothing here to deliver to
        self._queue.put_nowait(payload)

    def reset(self) -> None:
        while not self._queue.empty():
            self._queue.get_nowait()
        self.hub.close()

    def start(self, pool: asyncpg.Pool) -> None:
        self.pool = pool
        self._task = asyncio.get_running_loop().create_task(self._run())

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _run(self) -> None:
        while True:
            payload = await self._queue.get()
            try:
                await self.deliver(payload)
            except Exception:
                log.exception("failed to deliver a live event")

    async def deliver(self, payload: str) -> None:
        if payload.startswith("@"):
            payload = await self.pool.fetchval(
                "SELECT payload FROM bus_messages WHERE pk = $1", int(payload[1:])
            )
            if payload is None:
                return  # cleaned up already: dashboards that missed it reconnect in time
        message = orjson.loads(payload)
        project = message["p"]
        if message.get("close"):
            self.hub.close_project(project)
        elif "s" in message:
            await self._points(project, message)
        elif message["r"] is None:
            self.hub.broadcast(project, Hub.event(message["e"], message["d"]))
        else:
            subs = self.hub.watching(project, message["r"])
            if subs:
                event = Hub.event(message["e"], message["d"])
                for sub in subs:
                    sub.push(event)

    async def _points(self, project: int, message: dict[str, Any]) -> None:
        stream, name, start, end = message["s"], message["r"], message["f"], message["t"]
        subs = self.hub.watching(project, name)
        if stream != "history":
            subs = [sub for sub in subs if sub.system]
        if not subs:
            return
        table, x_key = self.STREAMS[stream]
        records = await self.pool.fetch(
            f"SELECT line, data FROM {table} WHERE run_pk = $1 AND line >= $2 AND line < $3 "
            "ORDER BY line",
            message["k"],
            start,
            end,
        )
        hist = History()
        first, batch = None, []
        for line, data in records:
            if first is not None and line != first + len(batch):
                hist.append_rows(first, parse_rows(batch))
                first, batch = None, []
            if first is None:
                first = line
            batch.append(data)
        if first is not None:
            hist.append_rows(first, parse_rows(batch))
        points = hist.delta(start, x_key)
        if points:
            event = Hub.points_event(stream, name, end, points)
            for sub in subs:
                sub.push(event)
