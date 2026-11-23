import asyncio
import dataclasses
import hashlib
import math
import mimetypes
import os
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import cache, partial
from pathlib import Path
from typing import Annotated, Any, Literal

import orjson
from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api.src.api.deps import (
    APIError,
    JSONResponse,
    MemberProjectDep,
    MemberRunDep,
    Name,
    ReadableProjectDep,
    ReadableRunDep,
    StoreDep,
    UserDep,
    ViewerDep,
    check_owner,
    find_run,
    get_readable_project,
    rest_user,
)
from app.api.src.api.home import GpuType
from app.api.src.api.models import with_users
from app.api.src.core import query
from app.api.src.core.accounts import User
from app.api.src.core.errors import NotFound
from app.api.src.core.evals import PairFiles, token_texts
from app.api.src.core.store import RUN_COLUMNS, Project, Run, Store
from flock.eval.diff import check_instrument, diff
from flock.eval.items import CELLS, RANKED, Item, items
from flock.eval.results import WrittenResults

router = APIRouter(prefix="/api/v1", tags=["flock"], dependencies=[Depends(rest_user)])
# Item comparisons (025, D185) run here, never in the default executor: each waits on file reads
# that need the default executor's threads, so sharing it could deadlock. Four at a time also
# bound the files held in memory.
DIFFS = ThreadPoolExecutor(max_workers=4, thread_name_prefix="diff")
# Reads that answer outsiders too, on public projects (D112): they need no credential.
public = APIRouter(prefix="/api/v1", tags=["flock"])


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


@public.get("/projects/{entity}/{project}/columns")
async def list_columns(
    project: ReadableProjectDep,
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


@public.get("/projects/{entity}/{project}/runs")
async def get_runs(
    project: ReadableProjectDep,
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


@public.post("/projects/{entity}/{project}/runs/query")
async def query_runs(project: ReadableProjectDep, body: RunsQuery, store: StoreDep) -> Response:
    """Filter, sort and page the runs table.

    Returns ``total`` matches, the page's run ``ids`` and the requested ``columns`` as
    column-oriented ``data`` (one list per column, aligned with ``ids``).
    """
    selection = body.selection()
    pool = store.db.pool
    names = body.columns
    patterns = any(ch in name for name in names for ch in "*?[")
    # The project's columns (to expand patterns) are read alongside the runs.
    selected, found = await asyncio.gather(
        selection.runs(pool, project.pk, *(() if body.all_ids else (body.offset, body.limit))),
        query.columns(pool, project.pk) if patterns else _nothing(),
    )
    if body.all_ids:
        records, total = selected
        page = records[body.offset : body.offset + body.limit]
    else:
        page, total = selected
    names = query.select_columns([column.name for column in found], names)
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


async def _nothing() -> list[query.Column]:
    return []


# ---------------------------------------------------------------------- curves


@public.get("/projects/{entity}/{project}/history")
async def get_history(
    project: ReadableProjectDep,
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


@public.post("/projects/{entity}/{project}/history/query")
async def query_history(
    project: ReadableProjectDep, body: HistoryQuery, store: StoreDep
) -> Response:
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
async def update_run(
    run: MemberRunDep, body: RunFields, store: StoreDep, user: UserDep
) -> Response:
    """Edit a run's display name, notes, tags, group, job type, description or GPUs."""
    check_owner(store, user, run.project, [run])
    await store.edit_runs(run.project, [run], body.model_dump(include=body.model_fields_set))
    edited = await store.project_run(run.project, run.name)
    if edited is None:
        raise APIError(404, f"run {run.name} not found")
    return await get_run(edited, store, user)


@router.delete("/projects/{entity}/{project}/runs/{run}", status_code=204)
async def delete_run(
    run: MemberRunDep,
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
    project: MemberProjectDep, body: RunUpdate, store: StoreDep, user: UserDep
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
    project: MemberProjectDep, body: RunDelete, store: StoreDep, user: UserDep
) -> Response:
    """Delete many runs. Refused (409, listing them) if any is active, unless ``force``, and
    (403) if a member selects runs they didn't log."""
    runs = await body.targets(store, project)
    check_owner(store, user, project, runs)
    if body.dry_run:
        return changed([run.name for run in runs], dry_run=True)
    return changed(await store.delete_runs(project, runs, body.force))


@router.post("/projects/{entity}/{project}/runs/move")
async def move_runs(
    project: MemberProjectDep, body: RunMove, store: StoreDep, user: UserDep
) -> Response:
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
        target = await store.ensure_project(project.entity, body.to, creator=user.pk)
    try:
        return changed(await store.move_runs(project, runs, target))
    except NotFound as exc:
        raise APIError(404, str(exc)) from exc


# ---------------------------------------------------------------------- single run


@public.get("/projects/{entity}/{project}/runs/{run}")
async def get_run(run: ReadableRunDep, store: StoreDep, viewer: ViewerDep) -> Response:
    """Run attributes, unwrapped config, summary, the names of its logged/system metrics and
    its metric definitions (``metric_defs``: step metrics and hidden metrics).

    ``owner_user`` is the owner as a user. Outsiders reading a public project get it without
    email, and ``cost`` null (D113)."""
    outsider = store.accounts.access(viewer, run.project) == "public"
    owners = await store.accounts.briefs([run.owner], outsider)
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
            "cost": None if outsider else await store.budgets.run_cost(run),
            "owner_user": owners.get(run.owner) if run.owner is not None else None,
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


@public.get("/projects/{entity}/{project}/runs/{run}/history")
async def get_run_history(
    run: ReadableRunDep,
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


@public.get("/projects/{entity}/{project}/runs/{run}/logs")
async def get_run_logs(
    run: ReadableRunDep,
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


@router.get("/projects/{entity}/{project}/runs/{run}/models")
async def get_run_models(run: MemberRunDep, store: StoreDep) -> Response:
    """The model versions the run made (``made``) and those it scored as an eval run
    (``scored``), each with its eval runs (``evals``), by model name and number."""
    found = await store.models.run_versions(run)
    return JSONResponse({key: await with_users(store, items) for key, items in found.items()})


# A run file in this folder is listed and served to the org's members only, whatever the
# project's visibility (D181): an eval's rows from a private dataset, for example.
PRIVATE_FOLDER = "private"


def members_only(name: str) -> bool:
    """Whether the run file ``name`` is under ``private/``, which outsiders reading a public project never see (D181)."""
    # Checked on the validated parts, as D152 does for the metadata file, so `./private/x` and
    # `private//x` count too. In any case: a local volume may be case-insensitive and serve
    # `Private/x` from the same folder.
    parts = Store.file_parts(name)
    return parts is not None and parts[0].casefold() == PRIVATE_FOLDER


@public.get("/projects/{entity}/{project}/runs/{run}/files")
async def list_run_files(run: ReadableRunDep, store: StoreDep, viewer: ViewerDep) -> Response:
    """Files uploaded by the run (config.yaml, wandb-metadata.json, requirements.txt, ...). Outsiders reading a public project don't see those under ``private/``."""
    public = store.accounts.access(viewer, run.project) == "public"
    files = []
    for name, size, updated_at in await store.list_files(run):
        # Not even its name, size or time: a name can say which private dataset a run read.
        if public and members_only(name):
            continue
        files.append({"name": name, "size": size, "updated_at": updated_at})
    return JSONResponse({"files": files})


# Who ran it, in the wandb SDK's machine description: the account's email (once the SDK has
# looked it up) and the OS user. Outsiders get the file without them (D113).
PRIVATE_METADATA = ("email", "username")


@public.get("/projects/{entity}/{project}/runs/{run}/files/{name:path}")
async def get_run_file(
    run: ReadableRunDep, name: str, store: StoreDep, viewer: ViewerDep
) -> Response:
    """A file the run uploaded. Outsiders reading a public project get 404 for a file under ``private/``, and ``wandb-metadata.json`` without ``email`` and ``username`` (404 if it isn't a JSON object)."""
    # Before storage is opened.
    public = store.accounts.access(viewer, run.project) == "public"
    if public and members_only(name):
        raise APIError(404, "file not found")

    parts = store.file_parts(name)
    if parts == (Store.METADATA_FILE,) and public:
        data = await store.read_file(run, name)
        try:
            metadata = orjson.loads(data) if data is not None else None
        except orjson.JSONDecodeError:
            metadata = None
        if not isinstance(metadata, dict):
            raise APIError(404, "file not found")
        return JSONResponse({k: v for k, v in metadata.items() if k not in PRIVATE_METADATA})
    found = await store.files.get(run.pk, name) if parts else None
    if found is None:
        raise APIError(404, "file not found")
    if isinstance(found, Path):
        return FileResponse(found)
    chunks, size = found  # from the s3 backend (D82), served as FileResponse would
    media_type = mimetypes.guess_type(name)[0] or "text/plain"
    return StreamingResponse(chunks, media_type=media_type, headers={"content-length": str(size)})


async def eval_readers(
    run: Run, other: str | None, store: Store, viewer: User | None
) -> list[tuple[str, Callable[[str], str | None], Callable[[str], bytes | None]]]:
    """The run, and ``other`` (``entity/project/id``) if given, as eval reads tracker runs: each one's ``run:`` source and readers of its files as text and as bytes, all sharing one ``PairFiles`` budget, the arguments of ``WrittenResults.read``. The other run must be readable too; either one unknown or unreadable is the same 404."""
    # The readers are called from a thread of `DIFFS` and read each file through this loop.
    files = PairFiles(store, asyncio.get_running_loop())
    runs = [(run, f"{run.entity}/{run.project.name}/{run.name}")]
    if other is not None:
        parts = other.split("/")
        if len(parts) != 3 or not all(parts):
            raise APIError(404, f"run {other} not found")
        entity, project, name = parts
        readable = await get_readable_project(entity, project, store, viewer)
        runs.append((await find_run(store, readable, name), other))

    def read(of: Run, public: bool, name: str) -> str | None:
        # A private dataset's rows, under `private/` (D181), are read for the run's org members
        # only. To anyone else its task has no predictions, so no count, id or text of it leaves.
        return files.read(of, name, readable=not (public and members_only(name)))

    def read_bytes(of: Run, public: bool, name: str) -> bytes | None:
        return files.read_bytes(of, name, readable=not (public and members_only(name)))

    # As tracker runs (`run:`), so a private dataset's task is read from `private/`.
    readers = []
    for of, source in runs:
        public = store.accounts.access(viewer, of.project) == "public"
        readers.append(
            (f"run:{source}", partial(read, of, public), partial(read_bytes, of, public))
        )
    return readers


@public.get("/projects/{entity}/{project}/runs/{run}/diff")
async def diff_runs(
    run: ReadableRunDep,
    store: StoreDep,
    viewer: ViewerDep,
    other: Annotated[str, Query(alias="with", description="the other run, as entity/project/id")],
) -> Response:
    """The run compared doc by doc with another eval run, as ``flock eval diff --json`` prints
    it: eval's ``ItemDiff`` (025, D185). The other run must be readable too; either one unknown
    or unreadable is the same 404. 404 if either has no ``results.json``, and 409 with eval's
    reason if the diff refuses the pair."""
    loop = asyncio.get_running_loop()
    # The diff runs in a thread of its own pool and reads a file only when a task needs it, each
    # through this loop, so two runs' hundreds of files never travel to the client.
    pair = await eval_readers(run, other, store, viewer)

    def compare() -> dict[str, Any]:
        a, b = (WrittenResults.read(*reader) for reader in pair)
        return diff(a, b).to_dict()

    try:
        return JSONResponse(await loop.run_in_executor(DIFFS, compare))
    except FileNotFoundError as exc:
        raise APIError(404, str(exc)) from exc
    except ValueError as exc:  # another instrument, or files that don't match their results
        raise APIError(409, str(exc)) from exc


def _with_margins(
    found: list[Item], sort: Literal["margin", "doc"]
) -> list[tuple[Item, list[float | None]]]:
    """Each item with each run's margin, its gold choice's score minus its best wrong choice's in the primary metric's units, in doc order or with ``sort="margin"`` the most telling first (D198): for two runs the largest gap between their margins, for one run the largest margin either way."""
    # `share` is a softmax of the metric's scores, so the log of a ratio of shares is a gap
    # between scores. `acc_greedy` ranks no choices, and a doc with one choice has no wrong one:
    # no margin. A share that underflowed is floored.
    ranked = []
    for item in found:
        margins: list[float | None] = []
        for answer in item.runs:
            wrong = [s for k, s in enumerate(answer.share or []) if k != item.gold]
            if answer.share is None or not wrong:
                margins.append(None)
                continue
            gold = max(answer.share[item.gold], 1e-300)
            margins.append(math.log(gold) - math.log(max(max(wrong), 1e-300)))
        ranked.append((item, margins))
    if sort == "margin":

        def rank(entry: tuple[Item, list[float | None]]) -> tuple[int, float]:
            # One run: the surest first, right or wrong. Two: the biggest gap first. Items
            # without a margin go last, in doc order (the sort is stable).
            _, margins = entry
            known = [m for m in margins if m is not None]
            if len(known) < len(margins):
                return (1, 0.0)
            gap = known[0] if len(known) == 1 else known[0] - known[1]
            return (0, -abs(gap))

        ranked.sort(key=rank)
    return ranked


# What an items list keeps: eval's own values (`ONE_RUN` and `CELLS`), one run's or a cell of two.
ItemKeep = Literal["wrong", "right", "all", "only_a", "only_b", "both_wrong", "both_right"]


@public.get("/projects/{entity}/{project}/runs/{run}/items")
async def run_items(
    run: ReadableRunDep,
    store: StoreDep,
    viewer: ViewerDep,
    task: Annotated[str, Query(description="the task whose docs are listed")],
    keep: Annotated[
        ItemKeep, Query(description="one run's docs by outcome, or a cell of two runs' diff")
    ],
    other: Annotated[
        str | None, Query(alias="with", description="the other run, as entity/project/id")
    ] = None,
    sort: Annotated[
        Literal["margin", "doc"],
        Query(description="the biggest margin gap first (two runs), or the surest first (one)"),
    ] = "margin",
    offset: Annotated[int, Query(ge=0, le=1 << 31)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Response:
    """A page of one task's docs, each a short summary of eval's ``Item``: its id, the part of its question that varies and its gold choice, clipped, and per run whether it was right, its pick, its margin and the gold choice's probability. ``total`` counts them all. A row's text leaves out the whole lines every question of the list shares at either end (027, 028). A doc's whole text is ``.../item``.

    By default the items most likely to tell something come first (D198): for two runs, the largest gap between their margins, for one run the largest margin either way. A run's margin is its gold choice's score minus the best wrong choice's, in the primary metric's own units. ``sort=doc`` keeps the dataset's order.

    One run keeps its ``wrong``, ``right`` or ``all`` docs; with ``with=``, two runs keep a cell of their diff. Access is the diff's: a private dataset's task has no predictions to anyone but the org's members. 404 if a run has no ``results.json``, and 409 with eval's reason when the items can't be read (another instrument, a run before schema 11, which holds no text)."""
    loop = asyncio.get_running_loop()
    readers = await eval_readers(run, other, store, viewer)

    def read() -> bytes:
        runs = [WrittenResults.read(*reader) for reader in readers]
        ranked = _with_margins(items(runs, task, keep), sort)

        # The whole lines every question shares at both ends (a wrapper such as `Question:` and
        # `Answer:`) are left out of the rows, which show what varies; a doc opens whole.
        questions = [item.question for item, _ in ranked if item.question is not None]
        head = tail = ""
        # Docs whose choices each have their own context (Winogrande) have no question to share.
        shared = len(questions) == len(ranked)
        # Nothing varies when every question is the same, or when there is one.
        shared = shared and len(set(questions)) > 1
        if shared:
            common = os.path.commonprefix(questions)
            head = common[: common.rfind("\n") + 1]
            rests = [q[len(head) :] for q in questions]
            # The common end is the common start of the reversed texts.
            common = os.path.commonprefix([r[::-1] for r in rests])[::-1]
            tail = common[common.find("\n") :] if "\n" in common else ""
        # A row left with nothing to show keeps every question whole.
        if shared and any(not r[: len(r) - len(tail)].strip() for r in rests):
            head = tail = ""

        def clip(text: str) -> str:
            # Enough for two lines of a wide column; the whole text comes when a row opens.
            return text if len(text) <= 240 else text[:240].rstrip() + "…"

        rows = []
        for item, margins in ranked[offset : offset + limit]:
            # A choice as a reader names it: Winogrande's choices share their ending, and the
            # option is in each choice's own context.
            named = [c.strip() for c in item.contexts or item.choices]
            if item.question is None:
                text = " / ".join(named)
            else:
                text = item.question[len(head) : len(item.question) - len(tail)].strip()
            answers = []
            for answer, margin in zip(item.runs, margins, strict=True):
                pick = None if answer.pick is None else clip(named[answer.pick])
                answers.append(
                    {
                        "correct": answer.correct,
                        "pick": answer.pick,
                        "pick_text": pick,
                        "margin": margin,
                        "gold_prob": answer.prob[item.gold],
                    }
                )
            rows.append(
                {
                    "id": item.id,
                    "text": clip(text),
                    "gold": item.gold,
                    "gold_text": clip(named[item.gold]),
                    "runs": answers,
                }
            )
        page = {"total": len(ranked), "offset": offset, "items": rows}
        return orjson.dumps(page)  # off the event loop

    try:
        return Response(await loop.run_in_executor(DIFFS, read), media_type="application/json")
    except FileNotFoundError as exc:
        raise APIError(404, str(exc)) from exc
    except ValueError as exc:  # eval's refusal, which says how to get the items where it can
        raise APIError(409, str(exc)) from exc


def _accuracy_suites(a: WrittenResults, b: WrittenResults) -> dict[str, list[str]]:
    """The suites both runs recorded under one hash, scored by their tasks' own metrics, that hold a task of their own, each with every task under it, nested suites' too, that both scored under one hash and that an accuracy ranking choices is the primary metric of: what the compare page draws a square for (029). A suite that only groups others gets none: its groups have theirs."""
    check_instrument(a, b)
    suites = {s.suite: s for s in a.suites}
    hashes_b = {s.suite: s.suite_hash for s in b.suites}
    tasks_b = {t.task: t.task_hash for t in b.tasks}
    # `acc_greedy` ranks no choices, so its docs have no margin to place.
    ranked = set()
    for t in a.tasks:
        if t.primary_metric in RANKED and tasks_b.get(t.task) == t.task_hash:
            ranked.add(t.task)
    found = {}
    for suite in a.suites:
        # A suite scored by a metric of its own (`mmlu:bpb`) isn't about its tasks' accuracy.
        if hashes_b.get(suite.suite) != suite.suite_hash or suite.metric is not None:
            continue
        if all(member in suites for member in suite.members):
            continue
        # Each suite opened once: an uploaded file may nest suites in a loop, or share one twice.
        tasks, todo, opened = [], list(suite.members), {suite.suite}
        while todo:
            name = todo.pop(0)
            if name in suites:
                if name not in opened:
                    opened.add(name)
                    todo.extend(suites[name].members)
            elif name in ranked and name not in tasks:
                tasks.append(name)
        if tasks:
            found[suite.suite] = tasks
    return found


# The suite squares of a pair (029): reading every accuracy task of two runs takes tens of seconds,
# so it runs on a worker of its own, never holding up the lists' and docs' reads in `DIFFS`, and
# its answer is kept for the last few pairs, keyed by both runs' files (names, sizes and times)
# and whether the viewer reads each as an outsider: any upload is a new key, and an answer
# missing a task a run is still uploading isn't kept.
SQUARES = ThreadPoolExecutor(max_workers=1, thread_name_prefix="squares")
SQUARE_ANSWERS: OrderedDict[tuple[str, ...], bytes] = OrderedDict()
SQUARES_RUNNING: dict[tuple[str, ...], asyncio.Future[tuple[bytes, bool]]] = {}


@public.get("/projects/{entity}/{project}/runs/{run}/items/suites")
async def run_items_suites(
    run: ReadableRunDep,
    store: StoreDep,
    viewer: ViewerDep,
    other: Annotated[str, Query(alias="with", description="the other run, as entity/project/id")],
) -> Response:
    """The compare page's row of suite squares for two runs (029): each suite both runs recorded alike that holds a task of its own, with its tasks (every accuracy task under it both scored under one hash), the number of docs in each cell of their diff, and up to 800 of its docs as ``[A's margin, B's margin, cell, task, place, id]`` (the cell an index into eval's ``CELLS``, the task one into ``tasks``, the place the doc's in that task's cell in the list's default order), taken evenly through each cell in the list's default order, margins rounded to 3 decimals.

    Each task is computed once for all its suites. A task a run has no predictions for, or whose predictions the viewer can't read (a private dataset's, outside the organization), is left out of its suites. The answer is kept for the last 8 pairs. 404 as ``.../items`` for a run unknown, unreadable or without results; 409 with eval's reason otherwise."""
    loop = asyncio.get_running_loop()
    readers = await eval_readers(run, other, store, viewer)
    # Whether the viewer reads each run as an outsider, who gets no private dataset's predictions,
    # as `eval_readers` decides it; it is in the key, so a change of membership is a new key.
    entity, project, name = other.split("/")
    other_project = await get_readable_project(entity, project, store, viewer)
    pair = [run, await find_run(store, other_project, name)]
    public = [store.accounts.access(viewer, r.project) == "public" for r in pair]
    # Every file of both runs, by name, size and time: any upload, a predictions file replaced
    # under the same results file too, is a new key. Read here, so a kept answer is served
    # without waiting behind another pair's computation on `SQUARES`.
    listing = hashlib.sha256()
    for of in pair:
        listing.update(f"{of.pk}\n".encode())
        for file in await store.list_files(of):
            listing.update(f"{file}\n".encode())
    key = (listing.hexdigest(), *(str(p) for p in public))
    if key in SQUARE_ANSWERS:
        SQUARE_ANSWERS.move_to_end(key)
        return Response(SQUARE_ANSWERS[key], media_type="application/json")

    def read() -> tuple[bytes, bool]:
        runs = [WrittenResults.read(source, text) for source, text, _ in readers]
        suites = _accuracy_suites(*runs)
        # The tasks an outsider can't read; any other task without predictions is a run still
        # uploading, and an answer missing it isn't kept.
        private = set()
        for written, outsider in zip(runs, public, strict=True):
            if outsider:
                private |= {t.task for t in written.tasks if t.dataset.private}
        whole = True
        # Each task's docs by cell, in the list's default order: id and both margins.
        cells_of: dict[str, list[list[tuple[str, list[float | None]]]]] = {}
        for task in dict.fromkeys(t for tasks in suites.values() for t in tasks):
            # A task's files read once for its four cells, so they come from the same rows, and
            # let go before the next task.
            scoped = [dataclasses.replace(r, read_file=cache(r.read_file)) for r in runs]
            if any(r.predictions(task) is None for r in scoped):
                whole = whole and task in private
                continue
            cells_of[task] = [
                [
                    (item.id, pair)
                    for item, pair in _with_margins(items(scoped, task, keep), "margin")
                ]
                for keep in CELLS
            ]
        rows = []
        for suite, tasks in suites.items():
            shown = [task for task in tasks if task in cells_of]
            # Each doc with where the list has it: its task's place in `shown` and its own in the
            # cell's default order, so a dot opens its doc.
            cells: list[list[tuple[int, int, str, list[float | None]]]] = [[], [], [], []]
            for t, task in enumerate(shown):
                for k, docs_in_cell in enumerate(cells_of[task]):
                    for i, (doc_id, pair) in enumerate(docs_in_cell):
                        cells[k].append((t, i, doc_id, pair))
            docs = sum(len(pairs) for pairs in cells)
            if not docs:
                continue
            # Each cell's share of 800 dots, taken evenly through it: never more than 800 in all.
            dots = []
            for k, pairs in enumerate(cells):
                n = len(pairs) if docs <= 800 else len(pairs) * 800 // docs
                for j in range(n):
                    t, i, doc_id, (a, b) = pairs[j * len(pairs) // n]
                    if a is not None and b is not None:
                        dots.append([round(a, 3), round(b, 3), k, t, i, doc_id])
            counts = {keep: len(pairs) for keep, pairs in zip(CELLS, cells, strict=True)}
            rows.append({"suite": suite, "tasks": shown, "counts": counts, "dots": dots})
        return orjson.dumps({"suites": rows}), whole

    # Two first visits to one pair share one computation, rather than both queueing for the
    # worker; shielded, so one visitor leaving doesn't stop the other's.
    running = SQUARES_RUNNING.get(key)
    if running is None:
        running = loop.run_in_executor(SQUARES, read)
        SQUARES_RUNNING[key] = running
        running.add_done_callback(lambda _: SQUARES_RUNNING.pop(key, None))
    try:
        answer, whole = await asyncio.shield(running)
    except FileNotFoundError as exc:
        raise APIError(404, str(exc)) from exc
    except ValueError as exc:
        raise APIError(409, str(exc)) from exc
    # Kept from the event loop only, so one thread ever touches the kept answers.
    if whole:
        SQUARE_ANSWERS[key] = answer
        while len(SQUARE_ANSWERS) > 8:
            SQUARE_ANSWERS.popitem(last=False)
    return Response(answer, media_type="application/json")


@public.get("/projects/{entity}/{project}/runs/{run}/item")
async def run_item(
    run: ReadableRunDep,
    store: StoreDep,
    viewer: ViewerDep,
    doc: Annotated[str, Query(description="the doc's id")],
    task: Annotated[str, Query(description="the task the doc is in")],
    keep: Annotated[
        ItemKeep, Query(description="the list the doc is in: two runs are read by a cell")
    ],
    other: Annotated[
        str | None, Query(alias="with", description="the other run, as entity/project/id")
    ] = None,
) -> Response:
    """One doc with its whole text and each run's answer, eval's ``Item`` as ``flock eval items --json`` prints it: what a row of ``.../items`` opens to (028). The id is a query parameter, so any id reaches it as written.

    Read and refused as ``.../items`` is, with 404 too if ``keep`` holds no doc ``doc``."""
    loop = asyncio.get_running_loop()
    readers = await eval_readers(run, other, store, viewer)

    def read() -> bytes | None:
        runs = [WrittenResults.read(*reader) for reader in readers]
        found = next((item for item in items(runs, task, keep) if item.id == doc), None)
        return None if found is None else orjson.dumps(found.to_dict())

    try:
        found = await loop.run_in_executor(DIFFS, read)
    except FileNotFoundError as exc:
        raise APIError(404, str(exc)) from exc
    except ValueError as exc:
        raise APIError(409, str(exc)) from exc
    if found is None:
        raise APIError(404, f"{task} has no doc {doc} among its {keep} docs")
    return Response(found, media_type="application/json")


@public.get("/projects/{entity}/{project}/runs/{run}/item/tokens")
async def run_item_tokens(
    run: ReadableRunDep,
    store: StoreDep,
    viewer: ViewerDep,
    doc: Annotated[str, Query(description="the doc's id")],
    task: Annotated[str, Query(description="the task the doc is in")],
    keep: Annotated[
        ItemKeep, Query(description="the list the doc is in: two runs are read by a cell")
    ],
    other: Annotated[
        str | None, Query(alias="with", description="the other run, as entity/project/id")
    ] = None,
) -> Response:
    """Each run's per-token values over each choice of one doc, from its sidecar (schema 12, ``predictions/<task>.tokens.parquet``): log-probability, rank and entropy per token, and each token's text from the run's tokenizer. What an opened row of ``.../items`` shows as heat (030).

    Per run, in order: ``choices`` (one entry per choice, its ``text`` pieces, ``logprob``, ``rank`` and ``entropy``) and ``note``. A run without values for the task (written before schema 12, or without them) has ``choices`` null and says why in ``note``; a run whose tokenizer can't be fetched, or doesn't give back the choices' text, has its values with ``text`` null, and ``note`` says why. Read and refused as ``.../item`` is."""
    loop = asyncio.get_running_loop()
    readers = await eval_readers(run, other, store, viewer)

    def read() -> bytes | None:
        runs = [WrittenResults.read(*reader) for reader in readers]
        found = next((item for item in items(runs, task, keep) if item.id == doc), None)
        if found is None:
            return None
        answers: list[dict[str, Any]] = []
        for written in runs:
            # Only each choice's continuation: context and greedy rows come by flags (045.D3)
            # and aren't shown.
            # A sidecar is an uploaded file: one Parquet can't read leaves this run without
            # values, never the other run without its own.
            try:
                rows = written.tokens(task, [doc]) or []
            # (pyarrow's errors are these: `ArrowInvalid` a ValueError, `ArrowTypeError` a
            # TypeError, its I/O errors OSErrors.)
            except ValueError, TypeError, KeyError, OSError:
                rows = []
            by_choice = {r.get("choice"): r for r in rows if r.get("part") == "continuation"}
            # A file that isn't the writer's (a choice or a column missing, arrays of other
            # lengths or of other than numbers) counts as none.
            fields = ("token_ids", "logprob", "rank", "entropy")
            whole = True
            for k in range(len(found.choices)):
                row = by_choice.get(k, {})
                lists = [row[f] for f in fields if isinstance(row.get(f), list)]
                if len(lists) < len(fields) or len({len(a) for a in lists}) != 1:
                    whole = False
                    continue
                for values in lists:
                    if not all(isinstance(v, int | float) for v in values):
                        whole = False
            if not whole:
                note = f"{written.source} has no per-token values for {task}"
                answers.append({"choices": None, "note": note})
                continue
            values = [by_choice[k] for k in range(len(found.choices))]

            # Schema 12 names the tokenizer (else the model's repo stands in); it is at the
            # model's revision when it is the model's own repo.
            name, _, revision = written.provenance.model.partition("@")
            tokenizer = written.provenance.tokenizer or name
            note = None
            try:
                ids = [v["token_ids"] for v in values]
                at = revision if tokenizer == name and revision else None
                texts = token_texts(tokenizer, at, ids, found.choices)
            except ValueError as exc:
                texts, note = [None] * len(values), str(exc)
            choices = []
            for text, v in zip(texts, values, strict=True):
                choices.append(
                    {
                        "text": text,
                        "logprob": v["logprob"],
                        "rank": v["rank"],
                        "entropy": v["entropy"],
                    }
                )
            answers.append({"choices": choices, "note": note})
        return orjson.dumps({"runs": answers})

    try:
        found = await loop.run_in_executor(DIFFS, read)
    except FileNotFoundError as exc:
        raise APIError(404, str(exc)) from exc
    except ValueError as exc:
        raise APIError(409, str(exc)) from exc
    if found is None:
        raise APIError(404, f"{task} has no doc {doc} among its {keep} docs")
    return Response(found, media_type="application/json")
