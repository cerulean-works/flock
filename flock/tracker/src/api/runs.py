from typing import Annotated, Any, Literal

import numpy as np
import orjson
from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field, field_validator

from src.api.deps import (
    APIError,
    JSONResponse,
    ProjectDep,
    RunDep,
    StoreDep,
    current_user,
)
from src.core.store import Project

router = APIRouter(prefix="/api/v1", tags=["tracker"], dependencies=[Depends(current_user)])


class SortKey(BaseModel):
    key: str
    desc: bool = False


class RunSelection(BaseModel):
    filter: Any = Field(
        None,
        description='Mongo-style filter, e.g. {"$and": [{"config.lr": {"$lt": 0.01}}, '
        '{"state": "running"}]}. Columns: run attributes, config.*, summary.*',
    )
    sort: list[str | SortKey] = Field(
        default_factory=list, description='"-summary.loss,created_at" or [{"key", "desc"}]'
    )
    running: bool = Field(False, description="only runs that are currently running")

    @field_validator("filter", mode="before")
    @classmethod
    def _parse_filter(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        # Query strings carry the filter as JSON. APIError (not ValueError) escapes pydantic.
        try:
            return orjson.loads(value) if value else None
        except orjson.JSONDecodeError as exc:
            raise APIError(400, "filter is not valid JSON") from exc

    @field_validator("sort", "columns", "keys", "runs", mode="before", check_fields=False)
    @classmethod
    def _split_names(cls, value: Any) -> Any:
        """Accept ``"a,b"`` wherever a list of names is expected (query strings)."""
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    def select(self, project: Project, limit: int | None = None) -> np.ndarray:
        """Table rows matching the selection, ordered by ``sort`` (default newest first)."""
        table = project.table
        if self.running:
            mask = np.zeros(table.n, dtype=bool)
            mask[[run.row for run in project.active.values()]] = True
            if self.filter is not None:
                mask = table.filter(self.filter, mask)
            rows = np.flatnonzero(mask)
        elif self.filter is not None:
            rows = np.flatnonzero(table.filter(self.filter))
        else:
            rows = np.arange(table.n)
        # "-key" sorts descending, "key" or "+key" ascending.
        keys = [
            (item.key, item.desc)
            if isinstance(item, SortKey)
            else (item[1:], True)
            if item.startswith("-")
            else (item.lstrip("+"), False)
            for item in self.sort
        ]
        rows = table.sort(rows, keys) if keys else rows[::-1]
        return rows if limit is None else rows[:limit]


class RunsQuery(RunSelection):
    columns: list[str] = Field(
        default_factory=lambda: [
            "display_name",
            "state",
            "created_at",
            "updated_at",
            "tags",
            "group",
        ],
        description="column names or globs such as config.*",
    )
    limit: int = Field(100, ge=0, le=100_000)
    offset: int = Field(0, ge=0)
    all_ids: bool = Field(False, description="also return the ids of every matching run")


class HistoryQuery(RunSelection):
    keys: list[str] = Field(description="metrics to return, e.g. loss,val_loss")
    runs: list[str] = Field(
        default_factory=list, description="explicit run ids; overrides filter/running/sort"
    )
    limit: int = Field(50, ge=0, le=100_000, description="max runs (default: newest 50)")
    stream: Literal["history", "system"] = Field(
        "history", description="logged metrics, or system metrics (CPU, GPU, memory, ...)"
    )
    x: str | None = Field(None, description="x-axis metric (default: _step, or _runtime)")
    max_points: int = Field(0, ge=0, description="downsample each curve to this many points")

    @property
    def x_axis(self) -> str:
        # System samples are taken on a timer and carry no _step.
        return self.x or ("_step" if self.stream == "history" else "_runtime")


# ---------------------------------------------------------------------- projects


@router.get("/projects")
async def list_projects(store: StoreDep) -> Response:
    """All projects with their run counts."""
    return JSONResponse(
        {
            "projects": [
                {
                    "entity": p.entity,
                    "name": p.name,
                    "run_count": len(p.runs),
                    "active_run_count": len(p.active),
                    "column_count": len(p.table.columns),
                }
                for p in store.projects.values()
            ]
        }
    )


@router.get("/projects/{entity}/{project}/columns")
async def list_columns(
    project: ProjectDep,
    q: Annotated[str, Query(description="case-insensitive substring")] = "",
    prefix: Annotated[str, Query(description="e.g. summary.")] = "",
) -> Response:
    """Run-table columns with their value type and number of runs that have them."""
    table = project.table
    q = q.lower()
    out = []
    for name in table.column_names():
        if prefix and not name.startswith(prefix):
            continue
        if q and q not in name.lower():
            continue
        column = table.columns[name]
        out.append({"name": name, "type": column.kind, "count": column.n_num + column.n_obj})
    return JSONResponse({"columns": out})


# ---------------------------------------------------------------------- runs table


@router.get("/projects/{entity}/{project}/runs")
async def get_runs(
    project: ProjectDep,
    filter: Annotated[str | None, Query(description="Mongo-style filter as JSON")] = None,
    sort: Annotated[str | None, Query(description="e.g. -summary.loss,created_at")] = None,
    columns: Annotated[str | None, Query(description="comma separated names/globs")] = None,
    limit: Annotated[int, Query(ge=0, le=100_000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    all_ids: bool = False,
    running: bool = False,
) -> Response:
    """Filter, sort and page the runs table (see the POST variant for JSON bodies)."""
    params = {"filter": filter, "sort": sort, "columns": columns}
    query = RunsQuery(
        **{k: v for k, v in params.items() if v is not None},
        limit=limit,
        offset=offset,
        all_ids=all_ids,
        running=running,
    )
    return await query_runs(project, query)


@router.post("/projects/{entity}/{project}/runs")
async def query_runs(project: ProjectDep, query: RunsQuery) -> Response:
    """Filter, sort and page the runs table.

    Returns ``total`` matches, the page's run ``ids`` and the requested ``columns`` as
    column-oriented ``data`` (one list per column, aligned with ``ids``).
    """
    rows = query.select(project)
    page = rows[query.offset : query.offset + query.limit]
    table = project.table
    names = table.select_columns(query.columns)
    runs = project.runs
    data: dict[str, Any] = {}
    for name in names:
        column = table.columns.get(name)
        data[name] = column.values(page) if column is not None else [None] * len(page)
    body: dict[str, Any] = {
        "total": len(rows),
        "offset": query.offset,
        "limit": query.limit,
        "ids": [runs[r].name for r in page.tolist()],
        "columns": names,
        "data": data,
    }
    if query.all_ids:
        body["all_ids"] = [runs[r].name for r in rows.tolist()]
    return JSONResponse(body)


# ---------------------------------------------------------------------- curves


@router.get("/projects/{entity}/{project}/history")
async def get_history(
    project: ProjectDep,
    store: StoreDep,
    keys: Annotated[str, Query(description="comma separated metrics, e.g. loss,val_loss")],
    runs: Annotated[str | None, Query(description="comma separated run ids")] = None,
    filter: Annotated[str | None, Query(description="Mongo-style filter as JSON")] = None,
    sort: Annotated[str | None, Query()] = None,
    running: bool = False,
    limit: Annotated[int, Query(ge=0, le=100_000)] = 50,
    stream: Literal["history", "system"] = "history",
    x: Annotated[str | None, Query(description="default: _step, or _runtime")] = None,
    max_points: Annotated[int, Query(ge=0)] = 0,
) -> Response:
    """Curves of ``keys`` for many runs: the newest 50 by default, or ``running=true``."""
    params = {"runs": runs, "filter": filter, "sort": sort}
    query = HistoryQuery(
        **{k: v for k, v in params.items() if v is not None},
        keys=keys,  # split into a list by the model's validator
        running=running,
        limit=limit,
        stream=stream,
        x=x,
        max_points=max_points,
    )
    return await query_history(project, query, store)


@router.post("/projects/{entity}/{project}/history")
async def query_history(project: ProjectDep, query: HistoryQuery, store: StoreDep) -> Response:
    """Curves of ``keys`` for the runs chosen by ``runs`` or by ``filter``/``running``/``sort``.

    Each run carries ``series[key] = {"x": [...], "y": [...]}``; non-finite values are null.
    ``stream=system`` returns system metrics (``system.gpu.0.gpu``, ...) against ``_runtime``.
    """
    if not query.keys:
        raise APIError(400, "keys is required")
    if query.runs:
        selected = [project.by_name[n] for n in query.runs if n in project.by_name]
    else:
        selected = [project.runs[r] for r in query.select(project, query.limit).tolist()]
    x = query.x_axis
    histories = await store.histories(selected, [*query.keys, x], query.stream)
    out = []
    for run, hist in zip(selected, histories, strict=True):
        series = {}
        for key in query.keys:
            xy = hist.curve(key, x, query.max_points)
            if xy is not None:
                series[key] = {"x": xy[0], "y": xy[1]}
        out.append(
            {"id": run.name, "display_name": run.display_name, "state": run.state, "series": series}
        )
    return JSONResponse({"stream": query.stream, "x": x, "keys": query.keys, "runs": out})


# ---------------------------------------------------------------------- single run


@router.get("/projects/{entity}/{project}/runs/{run}")
async def get_run(run: RunDep, store: StoreDep) -> Response:
    """Run attributes, unwrapped config, summary and the names of its logged/system metrics."""
    summary = await store.read_summary(run)
    summary.pop("_wandb", None)
    (hist,) = await store.histories([run], ())
    (system,) = await store.histories([run], (), "system")
    # wandb stores config entries as {"value": v, "desc": ...}; return the bare values.
    config = {
        key: item["value"] if isinstance(item, dict) and "value" in item else item
        for key, item in (await store.read_config(run)).items()
        if key != "_wandb"
    }
    return JSONResponse(
        {
            "id": run.name,
            "entity": run.entity,
            "project": run.project,
            **run.attributes(),
            "config": config,
            "summary": summary,
            "history_keys": hist.keys(),
            "system_keys": system.keys(),
            "history_line_count": run.history_line_count,
            "system_line_count": run.events_line_count,
            "log_line_count": run.log_line_count,
        }
    )


@router.get("/projects/{entity}/{project}/runs/{run}/history")
async def get_run_history(
    run: RunDep,
    store: StoreDep,
    keys: Annotated[str | None, Query(description="defaults to every metric")] = None,
    stream: Literal["history", "system"] = "history",
    x: Annotated[str | None, Query(description="default: _step, or _runtime")] = None,
    max_points: Annotated[int, Query(ge=0)] = 0,
) -> Response:
    """Curves of one run's logged metrics, or of its system metrics with ``stream=system``."""
    x = x or ("_step" if stream == "history" else "_runtime")  # system samples have no _step
    names = [key.strip() for key in (keys or "").split(",") if key.strip()]
    (hist,) = await store.histories([run], [*names, x] if names else None, stream)
    series = {}
    for key in names or hist.keys():
        xy = hist.curve(key, x, max_points)
        if xy is not None:
            series[key] = {"x": xy[0], "y": xy[1]}
    return JSONResponse({"id": run.name, "stream": stream, "x": x, "series": series})


@router.get("/projects/{entity}/{project}/runs/{run}/logs")
async def get_run_logs(
    run: RunDep,
    store: StoreDep,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=0, le=100_000)] = 1000,
) -> Response:
    """Console output lines (as captured by wandb, prefixed with a timestamp)."""
    lines = await store.read_lines("logs", run, offset, limit)
    return JSONResponse(
        {
            "total": run.log_line_count,
            "lines": [{"line": line, "text": text} for line, text in lines],
        }
    )


@router.get("/projects/{entity}/{project}/runs/{run}/files")
async def list_run_files(run: RunDep, store: StoreDep) -> Response:
    """Files uploaded by the run (config.yaml, wandb-metadata.json, requirements.txt, ...)."""
    return JSONResponse(
        {
            "files": [
                {"name": name, "size": size, "updated_at": updated_at}
                for name, size, updated_at in await store.list_files(run)
            ]
        }
    )


@router.get("/projects/{entity}/{project}/runs/{run}/files/{name:path}")
async def get_run_file(run: RunDep, name: str, store: StoreDep) -> FileResponse:
    path = store.file_path(run, name)
    if path is None or not path.is_file():
        raise APIError(404, "file not found")
    return FileResponse(path)
