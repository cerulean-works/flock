import asyncio
import time
from typing import Annotated, Any

import numpy as np
import orjson
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response, StreamingResponse

from app.api.src.api.deps import ProjectDep, StoreDep, current_user
from app.api.src.api.runs import RunSelection
from app.api.src.core.live import Hub
from app.api.src.core.store import Project, Store

router = APIRouter(prefix="/api/v1", tags=["dashboard"], dependencies=[Depends(current_user)])


class Snapshot:
    """A page of the runs table with every column and every metric's curve, as one binary blob.

    See Flock docs for the wire format.
    """

    MAGIC = b"FLK2"
    # Total points per snapshot; per-series resolution shrinks as runs × metrics grows.
    POINT_BUDGET = 2_000_000
    MIN_POINTS = 32
    # Run attributes shown before config.* and summary.*; display_name is the row label.
    INFO_COLUMNS = (
        "state",
        "name",
        "created_at",
        "updated_at",
        "tags",
        "group",
        "job_type",
        "notes",
        "host",
        "program",
        "commit",
        "sweep",
        "exitcode",
    )
    TIME_COLUMNS = frozenset({"created_at", "updated_at", "heartbeat_at", "summary._timestamp"})
    DURATION_COLUMNS = frozenset({"summary._runtime"})

    @classmethod
    async def build(
        cls,
        store: Store,
        project: Project,
        selection: RunSelection,
        offset: int,
        limit: int,
        max_points: int,
        system: bool = False,
    ) -> bytes:
        table = project.table
        rows = selection.select(project)
        page_rows = rows[offset : offset + limit]
        runs = [project.runs[r] for r in page_rows.tolist()]
        names = table.column_names()
        columns = [
            *(c for c in cls.INFO_COLUMNS if c in table.columns),
            *(c for c in names if c.startswith("config.")),
            *(c for c in names if c.startswith("summary.")),
        ]
        keys = project.metric_keys("history")
        system_keys = project.metric_keys("system") if system else []
        n_runs, n_keys = len(runs), len(keys) + len(system_keys)

        # ---- table cells: numeric columns as one f64 block, the rest as JSON
        numeric: list[int] = []
        num_blocks: list[np.ndarray] = []
        text: dict[int, list[Any]] = {}
        kinds: list[str] = []
        for i, name in enumerate(columns):
            column = table.columns.get(name)
            if column is not None and column.obj is None:
                numeric.append(i)
                num_blocks.append(column.num[page_rows])
            else:
                text[i] = (
                    [column.get(r) for r in page_rows.tolist()]
                    if column is not None
                    else [None] * n_runs
                )
            # Display type: state, time, duration, num, str, bool or json.
            if name == "state":
                kinds.append("state")
            elif name in cls.TIME_COLUMNS:
                kinds.append("time")
            elif name in cls.DURATION_COLUMNS:
                kinds.append("duration")
            elif column is None or column.n_obj == 0:
                kinds.append("num")
            else:
                kinds.append({"string": "str", "bool": "bool"}.get(column.kind, "json"))
        num = np.concatenate(num_blocks) if num_blocks else np.empty(0)

        # ---- curves: per run, every history metric against _step, then (if requested) every
        # system metric against _runtime
        histories = await store.histories(runs, [*keys, "_step"])
        if system:
            systems = await store.histories(runs, [*system_keys, "_runtime"], "system")
        else:
            systems = [None] * n_runs
        curves = [
            [
                *((hist, key, "_step") for key in keys),
                *((sys, key, "_runtime") for key in system_keys),
            ]
            for hist, sys in zip(histories, systems, strict=True)
        ]
        present = sum(1 for run in curves for h, k, _ in run if h and h.series.get(k) is not None)
        points = min(max_points, max(cls.MIN_POINTS, cls.POINT_BUDGET // max(1, present)))
        meta = np.zeros((n_runs * n_keys, 6), dtype="<f8")
        meta[:, 5] = -1
        xs: list[np.ndarray] = []
        ys: list[np.ndarray] = []
        x_len = y_len = 0
        for r, run_curves in enumerate(curves):
            for m, (hist, key, x_key) in enumerate(run_curves):
                xy = hist.curve(key, x_key) if hist and key in hist.series else None
                if xy is None:
                    continue
                x, y = xy
                n_raw = len(x)
                n = min(n_raw, points)
                # Sample raw indices k_i = (i·(N-1) + (n-1)//2) // (n-1): integer-only, so
                # clients recompute the same x values for evenly spaced steps.
                if n >= n_raw:
                    idx = np.arange(n_raw, dtype=np.int64)
                elif n <= 1:
                    idx = np.zeros(n, dtype=np.int64)
                else:
                    idx = (np.arange(n, dtype=np.int64) * (n_raw - 1) + (n - 1) // 2) // (n - 1)
                slot = meta[r * n_keys + m]
                slot[0], slot[1], slot[2] = y_len, len(idx), n_raw
                x = x.astype(np.float64)
                dx = x[1] - x[0] if n_raw > 1 else 0.0
                if n_raw > 1 and dx > 0 and np.all(np.diff(x) == dx):
                    slot[3], slot[4] = x[0], dx
                else:
                    slot[5] = x_len
                    xs.append(x[idx])
                    x_len += len(idx)
                ys.append(y[idx].astype("<f4"))
                y_len += len(idx)

        header = orjson.dumps(
            {
                "entity": project.entity,
                "project": project.name,
                "now": time.time(),
                "total": len(rows),
                "runs_total": len(project.runs),
                "active": len(project.active),
                "offset": offset,
                "runs": [
                    {
                        "id": run.name,
                        "name": run.display_name or run.name,
                        "state": run.state,
                        "ordinal": run.row,
                        "lines": run.history_line_count,
                        "system_lines": run.events_line_count,
                    }
                    for run in runs
                ],
                "columns": [{"key": n, "kind": k} for n, k in zip(columns, kinds, strict=True)],
                "metrics": keys,
                "system_metrics": system_keys,
                "numeric": numeric,
                "text": {str(i): values for i, values in text.items()},
                "points": points,
                "x_len": x_len,
                "y_len": y_len,
            },
            option=orjson.OPT_SERIALIZE_NUMPY,
        )
        pad = (-(8 + len(header))) % 8
        return b"".join(
            [
                cls.MAGIC,
                np.uint32(len(header)).tobytes(),
                header,
                b"\0" * pad,
                num.astype("<f8").tobytes(),
                meta.tobytes(),
                *(x.astype("<f8").tobytes() for x in xs),
                *(y.tobytes() for y in ys),
            ]
        )


@router.get("/projects/{entity}/{project}/workspace")
async def get_workspace(
    project: ProjectDep,
    store: StoreDep,
    sort: Annotated[str | None, Query(description="e.g. -created_at or summary.loss")] = None,
    filter: Annotated[str | None, Query(description="Mongo-style filter as JSON")] = None,
    running: Annotated[bool, Query(description="only runs that are currently running")] = False,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=0, le=500)] = 100,
    max_points: Annotated[int, Query(ge=Snapshot.MIN_POINTS, le=10_000)] = 500,
    system: Annotated[bool, Query(description="also include system metric curves")] = False,
) -> Response:
    """A page of runs with every table column and every metric's curve, as one binary blob.

    ``filter`` and ``running`` select runs as in ``GET /runs``; ``total`` counts the matches."""
    selection = RunSelection(sort=sort or "-created_at", filter=filter, running=running)
    body = await Snapshot.build(store, project, selection, offset, limit, max_points, system)
    return Response(body, media_type="application/octet-stream")


@router.get("/projects/{entity}/{project}/stream")
async def stream(
    project: ProjectDep,
    request: Request,
    store: StoreDep,
    runs: Annotated[str, Query(description="runs to watch: id:lines[:system_lines],...")] = "",
    system: Annotated[bool, Query(description="also stream system metric samples")] = False,
    ping_seconds: Annotated[
        float, Query(ge=1, le=300, description="keep-alive interval for idle streams")
    ] = 15.0,
) -> StreamingResponse:
    """Server-Sent Events for the given runs.

    Events: ``hello`` (current state of every watched run), ``history`` (new points:
    ``series[key] = {rows, x, y}``), ``system`` (new system metric samples, x is ``_runtime``;
    only with ``system=true``), ``run`` (state, line count and changed ``summary.*`` columns) and
    ``created`` (a new run in the project). Comment lines keep idle connections open, every
    ``ping_seconds``.
    """
    # "id:lines:system_lines,id,..." -> {id: (lines, system_lines)}; a missing count means the
    # stream is watched without replay.
    cursors: dict[str, tuple[int | None, int | None]] = {}
    for part in runs.split(","):
        name, *counts = part.strip().split(":")
        counts = [int(c) if c.isdigit() else None for c in counts[:2]] + [None, None]
        if name:
            cursors[name] = (counts[0], counts[1])
    # Subscribe before computing the replay: anything published from now on is queued, so the
    # replay and the stream overlap rather than leave a gap. Clients drop points they already
    # have by line number.
    sub = store.live.subscribe(project.pk, cursors, system)

    async def events():
        try:
            watched = [project.by_name[n] for n in cursors if n in project.by_name]
            yield Hub.event(
                "hello",
                {
                    "active": len(project.active),
                    "total": len(project.runs),
                    "runs": {
                        run.name: {
                            "state": run.state,
                            "lines": run.history_line_count,
                            **({"system_lines": run.events_line_count} if system else {}),
                        }
                        for run in watched
                    },
                },
            )
            for run in watched:
                replays = [("history", "_step", cursors[run.name][0])]
                if system:
                    replays.append(("system", "_runtime", cursors[run.name][1]))
                for series_stream, x_key, since in replays:
                    if since is None or since >= run.lines(series_stream):
                        continue
                    (hist,) = await store.histories([run], None, series_stream)
                    points = hist.delta(since, x_key)
                    if points:
                        yield Hub.points_event(
                            series_stream, run.name, run.lines(series_stream), points
                        )
            while True:
                try:
                    event = await asyncio.wait_for(sub.queue.get(), ping_seconds)
                except TimeoutError:
                    if await request.is_disconnected():
                        return
                    yield b": ping\n\n"
                    continue
                if event is None:
                    return
                yield event
        finally:
            store.live.unsubscribe(project.pk, sub)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"cache-control": "no-cache", "x-accel-buffering": "no"},
    )
