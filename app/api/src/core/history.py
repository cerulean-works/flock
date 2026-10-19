from collections.abc import Iterable
from typing import Self

import numpy as np

from app.api.src.utils import flatten


class Series:
    """Append-only (row, value) pairs for one metric, backed by doubling numpy buffers."""

    __slots__ = ("_rows", "_vals", "n")

    ROW_DTYPE = np.dtype("<i4")
    VALUE_DTYPE = np.dtype("<f8")

    def __init__(self, rows: np.ndarray | None = None, vals: np.ndarray | None = None) -> None:
        if rows is None or vals is None:
            self._rows = np.empty(16, dtype=self.ROW_DTYPE)
            self._vals = np.empty(16, dtype=self.VALUE_DTYPE)
            self.n = 0
        else:
            # May be read-only views over a compacted blob; ``extend`` copies before writing.
            self._rows = rows
            self._vals = vals
            self.n = len(rows)

    @property
    def rows(self) -> np.ndarray:
        return self._rows[: self.n]

    @property
    def vals(self) -> np.ndarray:
        return self._vals[: self.n]

    def extend(self, rows: list[int], vals: list[float]) -> None:
        need = self.n + len(rows)
        if need > len(self._rows) or not self._rows.flags.writeable:
            cap = max(16, need, 2 * len(self._rows))
            new_rows = np.empty(cap, dtype=self.ROW_DTYPE)
            new_vals = np.empty(cap, dtype=self.VALUE_DTYPE)
            new_rows[: self.n] = self._rows[: self.n]
            new_vals[: self.n] = self._vals[: self.n]
            self._rows, self._vals = new_rows, new_vals
        self._rows[self.n : need] = rows
        self._vals[self.n : need] = vals
        self.n = need

    def encode(self) -> bytes:
        """``rows (int32[n]) || values (float64[n])``, little-endian.

        A series logged on every line from line 0 (rows ``0..n-1``) omits its rows.
        """
        rows = self.rows
        if self.n and rows[0] == 0 and rows[-1] == self.n - 1:  # rows strictly increase
            return self.vals.tobytes()
        return rows.tobytes() + self.vals.tobytes()

    @classmethod
    def decode(cls, blob: bytes, n: int) -> Self:
        if len(blob) == n * cls.VALUE_DTYPE.itemsize:  # dense: rows were omitted
            rows = np.arange(n, dtype=cls.ROW_DTYPE)
            return cls(rows, np.frombuffer(blob, dtype=cls.VALUE_DTYPE, count=n))
        rows = np.frombuffer(blob, dtype=cls.ROW_DTYPE, count=n)
        offset = n * cls.ROW_DTYPE.itemsize
        return cls(rows, np.frombuffer(blob, dtype=cls.VALUE_DTYPE, count=n, offset=offset))


class PointCounter:
    """Total number of history points held by the histories attached to it."""

    __slots__ = ("total",)

    def __init__(self) -> None:
        self.total = 0


class History:
    """All numeric history series of one run.

    A history is either *complete* (every series is in memory, as for running runs) or
    partially loaded: the keys of a compacted run are known, and their series are fetched by
    the store in batches (``Store.histories``) before they are read.
    """

    __slots__ = ("_counter", "_pending", "n_rows", "points", "series")

    def __init__(self, n_rows: int = 0, pending: Iterable[str] = ()) -> None:
        self.n_rows = n_rows
        self.series: dict[str, Series] = {}
        self._pending: set[str] = set(pending)
        self.points = 0
        self._counter: PointCounter | None = None

    def attach(self, counter: PointCounter) -> None:
        self._counter = counter
        counter.total += self.points

    def detach(self) -> None:
        if self._counter is not None:
            self._counter.total -= self.points
            self._counter = None

    def _add_points(self, count: int) -> None:
        self.points += count
        if self._counter is not None:
            self._counter.total += count

    @property
    def complete(self) -> bool:
        return not self._pending

    def keys(self) -> list[str]:
        return sorted(self.series.keys() | self._pending)

    def missing(self, keys: Iterable[str] | None = None) -> set[str]:
        """Keys that exist but are not loaded yet (all of them when ``keys`` is None)."""
        if keys is None:
            return set(self._pending)
        return self._pending.intersection(keys)

    def get(self, key: str) -> Series | None:
        if key in self._pending:
            raise LookupError(f"history series {key!r} has not been loaded")
        return self.series.get(key)

    def preload(self, items: Iterable[tuple[str, Series]], requested: Iterable[str]) -> None:
        """Install series fetched from the database for the ``requested`` keys."""
        for key, series in items:
            if key in self._pending:
                self._pending.discard(key)
                self.series[key] = series
                self._add_points(series.n)
        # Requested keys the database had no blob for don't exist; stop expecting them.
        self._pending.difference_update(requested)

    def append_rows(self, start_row: int, rows: Iterable[dict]) -> None:
        """Append parsed history lines, the first of which is line ``start_row``."""
        if self._pending:
            raise LookupError("cannot append to a partially loaded history")
        pending: dict[str, tuple[list[int], list[float]]] = {}
        row = start_row
        for data in rows:
            for key, value in flatten(data).items():
                # Only plottable scalars (bools as 0/1) become series.
                if not isinstance(value, (int, float)):
                    continue
                bucket = pending.get(key)
                if bucket is None:
                    bucket = pending[key] = ([], [])
                bucket[0].append(row)
                bucket[1].append(float(value))
            row += 1
        for key, (idx, vals) in pending.items():
            series = self.series.get(key)
            if series is None:
                series = self.series[key] = Series()
            series.extend(idx, vals)
            self._add_points(len(idx))
        self.n_rows = max(self.n_rows, row)

    def x_at(self, x_key: str, rows: np.ndarray) -> np.ndarray:
        """Values of ``x_key`` at history ``rows`` (NaN where it wasn't logged)."""
        xs = self.get(x_key)
        if xs is None or len(rows) == 0:
            return np.full(len(rows), np.nan)
        if xs.n == self.n_rows and xs.n > 0 and int(xs.rows[-1]) == xs.n - 1:
            return xs.vals[rows]  # dense x (``_step``, ``_runtime``, ...): row == position
        dense = np.full(max(self.n_rows, int(rows[-1]) + 1), np.nan)
        dense[xs.rows] = xs.vals
        return dense[rows]

    def curve(
        self, key: str, x_key: str = "_step", max_points: int = 0
    ) -> tuple[np.ndarray, np.ndarray] | None:
        """``(x, y)`` arrays for metric ``key`` plotted against ``x_key``."""
        ys = self.get(key)
        if ys is None or ys.n == 0 or self.get(x_key) is None:
            return None
        y = ys.vals
        x = self.x_at(x_key, ys.rows)
        keep = ~np.isnan(x)
        if not keep.all():
            x, y = x[keep], y[keep]
        if 0 < max_points < len(x):  # evenly spaced points, keeping both endpoints
            idx = np.unique(np.linspace(0, len(x) - 1, max_points).round().astype(np.int64))
            x, y = x[idx], y[idx]
        if x_key == "_step":
            x = x.astype(np.int64)
        return x, y

    # wandb bookkeeping logged on every line, which dashboards can use as x axes.
    TIME_KEYS = ("_runtime", "_timestamp")

    def delta(
        self, from_row: int, x_key: str = "_step"
    ) -> dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]]:
        """Points logged at or after line ``from_row``: ``{key: (rows, x, y)}`` per metric, and
        for ``TIME_KEYS`` other than ``x_key``.

        Used to stream new points to dashboards and to replay what a dashboard missed between
        rendering a snapshot (which records each run's line count) and subscribing.
        """
        out = {}
        for key in self.keys():
            # Other wandb bookkeeping (_step, _wandb.runtime, ...) is not a metric.
            if key.startswith("_") and (key not in self.TIME_KEYS or key == x_key):
                continue
            series = self.get(key)
            if series is None or series.n == 0:
                continue
            start = int(np.searchsorted(series.rows, from_row))
            if start >= series.n:
                continue
            rows = series.rows[start:]
            y = series.vals[start:]
            x = self.x_at(x_key, rows)
            keep = ~np.isnan(x)
            if not keep.all():
                rows, x, y = rows[keep], x[keep], y[keep]
            if len(rows):
                out[key] = (rows, x.astype(np.int64) if x_key == "_step" else x, y)
        return out
