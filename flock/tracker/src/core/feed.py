"""The home feed: what happened across projects, derived from runs and projects (there is no
activity log). Each project's candidates come from its run table's columns, vectorized."""

import base64
import binascii
from collections.abc import Collection, Iterable
from functools import reduce
from typing import TYPE_CHECKING, Any

import numpy as np
import orjson

if TYPE_CHECKING:
    from src.core.store import Project, Run, Store

# Items at the same instant are ordered project, then run start, then run end.
RANKS = {"project_created": 0, "run_started": 1}
ENDED = {"finished": "run_finished", "failed": "run_failed", "crashed": "run_crashed"}
KINDS = (*RANKS, *ENDED.values())

# (at, rank, pk): items are listed in decreasing key order; cursors are the last key served.
Key = tuple[float, int, int]


def encode_cursor(key: Key) -> str:
    return base64.urlsafe_b64encode(orjson.dumps(list(key))).decode().rstrip("=")


def decode_cursor(cursor: str) -> Key:
    try:
        at, rank, pk = orjson.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        return float(at), int(rank), int(pk)
    except (ValueError, TypeError, binascii.Error) as e:
        raise ValueError("invalid cursor") from e


class Feed:
    def __init__(self, store: Store) -> None:
        self.store = store

    def page(
        self,
        projects: Iterable[Project],
        kinds: Collection[str] = KINDS,
        owner: int | None = None,
        cursor: Key | None = None,
        limit: int = 30,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Items of ``projects`` (only runs of ``owner``, if given) older than ``cursor``,
        newest first; returns them and the cursor of the next page (None on the last)."""
        found: list[tuple[Key, str, Project, Run | None]] = []
        states = [state for state, kind in ENDED.items() if kind in kinds]
        for project in projects:
            if "project_created" in kinds and owner is None:
                key = (project.created_at, 0, project.pk)
                if cursor is None or key < cursor:
                    found.append((key, "project_created", project, None))
            runs = project.runs
            n = len(runs)
            if not n:
                continue
            columns = project.table.columns
            mask = np.ones(n, dtype=bool)
            if owner is not None:
                column = columns.get("owner")
                if column is None:
                    continue
                mask = column.present[:n] & (column.num[:n] == owner)
            if "run_started" in kinds:
                times = columns["created_at"].num[:n]
                for row in self._latest(times, mask, 1, runs, cursor, limit + 1):
                    key = (float(times[row]), 1, runs[row].pk)
                    found.append((key, "run_started", project, runs[row]))
            if states:
                state = columns["state"].obj
                if state is None:
                    continue
                state = state[:n]
                ended = mask & reduce(np.logical_or, (state == s for s in states))
                times = columns["heartbeat_at"].num[:n]
                for row in self._latest(times, ended, 2, runs, cursor, limit + 1):
                    run = runs[row]
                    found.append(((float(times[row]), 2, run.pk), ENDED[run.state], project, run))
        found.sort(key=lambda item: item[0], reverse=True)
        page = found[:limit]
        items = [self._item(*entry) for entry in page]
        more = len(found) > limit
        return items, encode_cursor(page[-1][0]) if more and page else None

    @staticmethod
    def _latest(
        times: np.ndarray,
        mask: np.ndarray,
        rank: int,
        runs: list[Run],
        cursor: Key | None,
        limit: int,
    ) -> list[int]:
        """Rows (under ``mask``) whose key is below ``cursor``: the ``limit`` latest, plus any
        tied with the last of them (their final order depends on the run pk)."""
        valid = mask & ~np.isnan(times)
        if cursor is not None:
            at, cursor_rank, cursor_pk = cursor
            before = times < at
            for row in np.flatnonzero(valid & (times == at)).tolist():
                before[row] = (rank, runs[row].pk) < (cursor_rank, cursor_pk)
            valid &= before
        rows = np.flatnonzero(valid)
        if len(rows) > limit:
            cut = np.partition(times[rows], len(rows) - limit)[len(rows) - limit]
            rows = rows[times[rows] >= cut]
        return rows.tolist()

    def _item(self, key: Key, kind: str, project: Project, run: Run | None) -> dict[str, Any]:
        item: dict[str, Any] = {
            "id": f"{kind}:{key[2]}",
            "kind": kind,
            "at": key[0],
            "org": project.entity,
            "project": project.name,
        }
        if run is None:
            item["actor"] = None
            item["description"] = project.description
            return item
        item["actor"] = self.store.budgets.owner(run.owner)
        item["run"] = {
            "id": run.name,
            "display_name": run.display_name,
            "state": run.state,
            "gpu_type": run.gpu_type,
            "gpu_count": run.gpu_count,
            "runtime": round(run.runtime, 3),
            "tags": run.tags,
            "group": run.group,
            "job_type": run.job_type,
        }
        return item
