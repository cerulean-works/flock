import asyncio
from collections.abc import Iterable
from typing import Any

import numpy as np
import orjson


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
        self._subscribers: dict[tuple[str, str], set[Subscriber]] = {}

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

    def subscribe(
        self, entity: str, project: str, runs: Iterable[str], system: bool = False
    ) -> Subscriber:
        sub = Subscriber(runs, system)
        self._subscribers.setdefault((entity, project), set()).add(sub)
        return sub

    def unsubscribe(self, entity: str, project: str, sub: Subscriber) -> None:
        subs = self._subscribers.get((entity, project))
        if subs is not None:
            subs.discard(sub)
            if not subs:
                del self._subscribers[(entity, project)]

    def watching(self, entity: str, project: str, run: str) -> list[Subscriber]:
        """Subscribers interested in ``run`` (empty in the common no-dashboard case)."""
        subs = self._subscribers.get((entity, project))
        return [s for s in subs if run in s.runs] if subs else []

    def broadcast(self, entity: str, project: str, event: bytes) -> None:
        for sub in self._subscribers.get((entity, project), ()):
            sub.push(event)

    def close(self) -> None:
        for subs in self._subscribers.values():
            for sub in subs:
                sub.close()
