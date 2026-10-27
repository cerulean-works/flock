from typing import Annotated, Any, Literal

import orjson
from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api.src.api.deps import (
    APIError,
    JSONResponse,
    Name,
    ProjectDep,
    RunDep,
    StoreDep,
    UserDep,
    check_owner,
    rest_user,
)
from app.api.src.api.home import GpuType
from app.api.src.core import query
from app.api.src.core.errors import NotFound
from app.api.src.core.store import RUN_COLUMNS, Project, Run, Store

router = APIRouter(prefix="/api/v1", tags=["flock"], dependencies=[Depends(rest_user)])


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

    def selection(self) -> query.Selection:
        # "-key" sorts descending, "key" or "+key" ascending.
        keys = tuple(
            (item.key, item.desc)
            if isinstance(item, SortKey)
            else (item[1:], True)
            if item.startswith("-")
            else (item.lstrip("+"), False)
            for item in self.sort
        )
        return query.Selection(self.filter, keys, self.running)

    async def select(
        self, store: Store, project: Project, offset: int = 0, limit: int | None = None
    ) -> tuple[list[Run], int]:
        """Runs matching the selection, ordered by ``sort`` (default newest first), from
        ``offset`` and at most ``limit``, and how many match in all."""
        records, total = await self.selection().runs(
            store.db.pool, project.pk, offset, limit, RUN_COLUMNS
        )
        return [Run.load(record, project) for record in records], total


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


class Selection(RunSelection):
    """Runs to change: the ``runs`` named, or every run matching ``filter``/``running``, or
    ``all`` of them. An empty selection is an error, so a bare body never selects everything."""

    model_config = ConfigDict(extra="forbid")

    runs: list[str] = Field(default_factory=list, description="explicit run ids")
    all: bool = Field(False, description="every run in the project")
    dry_run: bool = Field(False, description="only report the runs that would change")

    async def targets(self, store: Store, project: Project) -> list[Run]:
        if self.runs:
            found = await store.project_runs(project, self.runs)
            missing = [name for name in self.runs if name not in found]
            if missing:
                raise APIError(404, "runs not found", ids=missing)
            return [found[name] for name in dict.fromkeys(self.runs)]
        if self.filter is None and not self.running and not self.all:
            raise APIError(400, "select runs with runs, filter, running or all")
        return (await self.select(store, project))[0]


class RunFields(BaseModel):
    """Editable run attributes; only the fields given change, and ``null`` clears one.

    ``gpu_type`` and ``gpu_count`` override the GPUs detected from the run's wandb metadata;
    ``null`` goes back to the detected ones."""

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = None
    notes: str | None = None
    tags: list[str] | None = None
    group: str | None = None
    job_type: str | None = None
    description: str | None = None
    gpu_type: GpuType | None = None
    gpu_count: int | None = Field(None, ge=0, le=1024)


class RunUpdate(Selection):
    set: RunFields = Field(default_factory=RunFields, description="attributes to set")
    add_tags: list[str] = Field(default_factory=list)
    remove_tags: list[str] = Field(default_factory=list)


class RunDelete(Selection):
    force: bool = Field(False, description="also delete active runs")


class RunMove(Selection):
    to: Name = Field(description="target project, in the same entity")
    create: bool = Field(False, description="create the target project if it doesn't exist")


@router.get("/projects/{entity}/{project}/columns")
async def list_columns(
    project: ProjectDep,
    store: StoreDep,
    q: Annotated[str, Query(description="case-insensitive substring")] = "",
    prefix: Annotated[str, Query(description="e.g. summary.")] = "",
) -> Response:
    """Run-table columns with their value type and number of runs that have them."""
    q = q.lower()
    out = []
    for column in await query.columns(store.db.pool, project.pk):
        if prefix and not column.name.startswith(prefix):
            continue
        if q and q not in column.name.lower():
            continue
        out.append({"name": column.name, "type": column.kind, "count": column.runs})
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
    *,
    store: StoreDep,
) -> Response:
    """Filter, sort and page the runs table (see the POST variant for JSON bodies)."""
    params = {"filter": filter, "sort": sort, "columns": columns}
    runs_query = RunsQuery(
        **{k: v for k, v in params.items() if v is not None},
        limit=limit,
        offset=offset,
        all_ids=all_ids,
        running=running,
    )
    return await query_runs(project, runs_query, store)


@router.post("/projects/{entity}/{project}/runs/query")
async def query_runs(project: ProjectDep, body: RunsQuery, store: StoreDep) -> Response:
    """Filter, sort and page the runs table.

    Returns ``total`` matches, the page's run ``ids`` and the requested ``columns`` as
    column-oriented ``data`` (one list per column, aligned with ``ids``).
    """
    selection = body.selection()
    if body.all_ids:
        records, total = await selection.runs(store.db.pool, project.pk)
        page = records[body.offset : body.offset + body.limit]
    else:
        page, total = await selection.runs(store.db.pool, project.pk, body.offset, body.limit)
    names = body.columns
    if any(ch in name for name in names for ch in "*?["):
        found = [column.name for column in await query.columns(store.db.pool, project.pk)]
        names = query.select_columns(found, names)
    else:
        names = query.select_columns((), names)
    data = await query.cells(store.db.pool, project.pk, [record[0] for record in page], names)
    out: dict[str, Any] = {
        "total": total,
        "offset": body.offset,
        "limit": body.limit,
        "ids": [record[1] for record in page],
        "columns": names,
        "data": data,
    }
    if body.all_ids:
        out["all_ids"] = [record[1] for record in records]
    return JSONResponse(out)


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


@router.post("/projects/{entity}/{project}/history/query")
async def query_history(project: ProjectDep, body: HistoryQuery, store: StoreDep) -> Response:
    """Curves of ``keys`` for the runs chosen by ``runs`` or by ``filter``/``running``/``sort``.

    Each run carries ``series[key] = {"x": [...], "y": [...]}``; non-finite values are null.
    ``stream=system`` returns system metrics (``system.gpu.0.gpu``, ...) against ``_runtime``.
    """
    if not body.keys:
        raise APIError(400, "keys is required")
    if body.runs:
        found = await store.project_runs(project, body.runs)
        selected = [found[n] for n in body.runs if n in found]
    else:
        selected = (await body.select(store, project, limit=body.limit))[0]
    x = body.x_axis
    histories = await store.histories(selected, [*body.keys, x], body.stream)
    out = []
    for run, hist in zip(selected, histories, strict=True):
        series = {}
        for key in body.keys:
            xy = hist.curve(key, x, body.max_points)
            if xy is not None:
                series[key] = {"x": xy[0], "y": xy[1]}
        out.append(
            {"id": run.name, "display_name": run.display_name, "state": run.state, "series": series}
        )
    return JSONResponse({"stream": body.stream, "x": x, "keys": body.keys, "runs": out})


# ---------------------------------------------------------------------- run changes


@router.patch("/projects/{entity}/{project}/runs/{run}")
async def update_run(run: RunDep, body: RunFields, store: StoreDep, user: UserDep) -> Response:
    """Edit a run's display name, notes, tags, group, job type, description or GPUs."""
    check_owner(store, user, run.project, [run])
    await store.edit_runs(run.project, [run], body.model_dump(include=body.model_fields_set))
    edited = await store.project_run(run.project, run.name)
    if edited is None:
        raise APIError(404, f"run {run.name} not found")
    return await get_run(edited, store)


@router.delete("/projects/{entity}/{project}/runs/{run}", status_code=204)
async def delete_run(
    run: RunDep,
    store: StoreDep,
    user: UserDep,
    force: Annotated[bool, Query(description="delete it even while it is running")] = False,
) -> Response:
    """Delete a run with its history, logs and files. There is no undo."""
    check_owner(store, user, run.project, [run])
    await store.delete_runs(run.project, [run], force)
    return Response(status_code=204)


def changed(runs: list[str], dry_run: bool = False) -> Response:
    body: dict[str, Any] = {"ids": runs, "count": len(runs)}
    if dry_run:
        body["dry_run"] = True
    return JSONResponse(body)


@router.post("/projects/{entity}/{project}/runs/update")
async def update_runs(
    project: ProjectDep, body: RunUpdate, store: StoreDep, user: UserDep
) -> Response:
    """Edit many runs at once: set attributes, and add or remove tags.

    Members can only select runs they logged (403 lists the others); admins any run.
    """
    runs = await body.targets(store, project)
    check_owner(store, user, project, runs)
    if body.dry_run:
        return changed([run.name for run in runs], dry_run=True)
    values = body.set.model_dump(include=body.set.model_fields_set)
    edited = await store.edit_runs(project, runs, values, body.add_tags, body.remove_tags)
    return changed([run.name for run in edited])


@router.post("/projects/{entity}/{project}/runs/delete")
async def delete_runs(
    project: ProjectDep, body: RunDelete, store: StoreDep, user: UserDep
) -> Response:
    """Delete many runs. Refused (409, listing them) if any is active, unless ``force``, and
    (403) if a member selects runs they didn't log."""
    runs = await body.targets(store, project)
    check_owner(store, user, project, runs)
    if body.dry_run:
        return changed([run.name for run in runs], dry_run=True)
    return changed(await store.delete_runs(project, runs, body.force))


@router.post("/projects/{entity}/{project}/runs/move")
async def move_runs(project: ProjectDep, body: RunMove, store: StoreDep, user: UserDep) -> Response:
    """Move finished runs to another project of the same entity, with everything they logged.

    Refused (409, listing them) if any run is active or the target has a run with the same id,
    and (403) if a member selects runs they didn't log.
    """
    runs = await body.targets(store, project)
    check_owner(store, user, project, runs)
    target = await store.project(project.entity, body.to)
    if target is None and not body.create:
        raise APIError(404, f"project {project.entity}/{body.to} not found")
    if body.dry_run:
        return changed([run.name for run in runs], dry_run=True)
    if target is None:
        target = await store.ensure_project(project.entity, body.to)
    try:
        return changed(await store.move_runs(project, runs, target))
    except NotFound as exc:
        raise APIError(404, str(exc)) from exc


# ---------------------------------------------------------------------- single run


@router.get("/projects/{entity}/{project}/runs/{run}")
async def get_run(run: RunDep, store: StoreDep) -> Response:
    """Run attributes, unwrapped config, summary, the names of its logged/system metrics and
    its metric definitions (``metric_defs``: step metrics and hidden metrics)."""
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
            "project": run.project.name,
            **run.attributes(),
            "gpu_detected": {"type": run.gpu_type_detected, "count": run.gpu_count_detected},
            "runtime": run.runtime,
            "cost": await store.budgets.run_cost(run),
            "config": config,
            "summary": summary,
            "metric_defs": run.metric_defs,
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
