"""The Datasets tab's routes (D94): the Lance datasets in an org's flock-datasets bucket."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.api.src.api.deps import (
    AdminOrgDep,
    APIError,
    JSONResponse,
    OrgDep,
    StoreDep,
    UserDep,
    ViewerDep,
    rest_user,
)
from app.api.src.core import forks
from app.api.src.core.datasets import Datasets, TagTaken, Taken, TooLarge
from app.api.src.core.forks import Job
from app.api.src.core.store import Org, Store

router = APIRouter(
    prefix="/api/v1/orgs/{org}/datasets", tags=["datasets"], dependencies=[Depends(rest_user)]
)
# The dataset page's reads, which answer outsiders too for public datasets (D115).
public = APIRouter(prefix="/api/v1/orgs/{org}/datasets", tags=["datasets"])

DatasetPath = Annotated[str, Query(min_length=1, max_length=1024, description="the dataset's path")]
VersionQuery = Annotated[
    str | None,
    Query(max_length=128, description="a version number or a tag (default: the latest)"),
]


class PathBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=1024)


class RenameBody(PathBody):
    to: str = Field(min_length=1, max_length=512, description="the new path")


ManyPaths = Annotated[
    list[Annotated[str, Field(min_length=1, max_length=1024)]], Field(min_length=1, max_length=1000)
]


class DeleteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str | None = Field(None, min_length=1, max_length=1024)
    paths: ManyPaths | None = None

    @model_validator(mode="after")
    def check_one(self) -> DeleteBody:
        if (self.path is None) == (self.paths is None):
            raise ValueError("give either path or paths")
        return self


class MoveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paths: ManyPaths
    to: str = Field(max_length=512, description='the folder to move them into, "" for the top')


class RestoreBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to: str | None = Field(
        None, min_length=1, max_length=512, description="restore here instead of the old path"
    )


class TagBody(PathBody):
    version: int = Field(ge=0)
    move: bool = Field(False, description="move the tag if it's on another version")


class ForkBody(PathBody):
    version: str | int | None = Field(
        None, description="a version number or tag (default: the latest)"
    )
    to: str = Field(min_length=1, max_length=512, description="where the fork goes")


class BlendSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=1024)
    version: str | int | None = None
    rows: int | None = Field(None, ge=0)
    fraction: float | None = Field(None, ge=0, le=1)


class BlendColumn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(max_length=256)
    type: str = Field(max_length=2048)
    nullable: bool = True
    from_: dict[str, dict[str, Any]] = Field(alias="from")


class BlendBody(BaseModel):
    """A blend (023, D174, D175): a row count for each source, or a fraction of each with the
    ``total``; and a column map, or none for the automatic one."""

    model_config = ConfigDict(extra="forbid")

    sources: list[BlendSource] = Field(min_length=1, max_length=16)
    total: int | None = Field(None, ge=1)
    seed: int | None = Field(None, ge=0, le=forks.MAX_SEED)
    columns: list[BlendColumn] | None = Field(None, max_length=1000)
    replace: bool = False
    to: str | None = Field(None, max_length=512)

    def request(self) -> forks.Request:
        return forks.request_from(self.model_dump(by_alias=True))


async def job_info(store: Store, job: Job) -> dict[str, Any]:
    info = job.info()
    user = await store.accounts.user(job.started_by) if job.started_by else None
    return {**info, "started_by": store.accounts.brief(user) if user is not None else None}


async def entry_info(store: Store, entry: dict[str, Any]) -> dict[str, Any]:
    user = await store.accounts.user(entry["deleted_by"])
    return {**entry, "deleted_by": store.accounts.brief(user) if user is not None else None}


def datasets(store: Store) -> Datasets:
    store.s3.require()
    return store.datasets


async def get_readable_org(org: str, path: DatasetPath, store: StoreDep, viewer: ViewerDep) -> Org:
    """The org, for reading the dataset at ``path``: any of its members, and outsiders only when that exact path is public. Others get the same 404 as for a missing dataset."""
    found = await store.find_org(org)
    if found is not None and store.accounts.role(viewer, found):
        return found
    if found is None or not await store.datasets.is_public(found, path):
        raise APIError(404, f"dataset {path} not found")
    return found


ReadableOrgDep = Annotated[Org, Depends(get_readable_org)]


@router.get("/at")
async def at(
    org: OrgDep,
    store: StoreDep,
    path: str = "",
    cursor: str | None = None,
    details: Annotated[
        bool, Query(description="also each dataset's data size, and its version in any naming")
    ] = False,
) -> Response:
    """A folder of flock-datasets, one level (100 entries a page): its datasets (with their
    latest version), folders and files. ``{kind: "dataset"}`` when ``path`` is a dataset. With
    ``details``, each dataset has ``size`` (its data files' bytes, as ``info``'s ``data_size``,
    null if it can't be read) and its version even with Lance's older manifest naming. The
    org's flock-datasets is created here if it's missing."""
    return JSONResponse(await datasets(store).at(org, path, cursor, details))


@public.get("/info")
async def info(
    org: ReadableOrgDep,
    store: StoreDep,
    viewer: ViewerDep,
    path: DatasetPath,
    version: VersionQuery = None,
) -> Response:
    """The dataset at ``version``: its numbers, schema, details and tags, with the version
    resolved (and the latest), and whether it's ``public``."""
    found = await datasets(store).info(org, path, version)
    # An outsider only gets here for a public dataset (``get_readable_org`` checked).
    member = bool(store.accounts.role(viewer, org))
    public = not member or await store.datasets.is_public(org, path)
    origin = await store.forks.origin(org, path)
    if origin is not None and not member:
        origin.pop("created_by", None)
    # Who reads it is only for its org (D179): the paths may be private.
    dependents = await store.forks.dependents(org, path) if member else None
    return JSONResponse({**found, "public": public, "origin": origin, "dependents": dependents})


@public.get("/rows")
async def rows(
    org: ReadableOrgDep,
    store: StoreDep,
    path: DatasetPath,
    version: VersionQuery = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int | None, Query(ge=1, le=Datasets.MAX_ROW_PAGE)] = None,
    columns: Annotated[
        list[str] | None, Query(description="the columns to read (default: all)")
    ] = None,
) -> Response:
    """One page of rows, its cells rendered for display: 50 rows, or 10 when a column holds
    binary data."""
    return JSONResponse(await datasets(store).rows(org, path, version, offset, limit, columns))


@public.get("/cell")
async def cell(
    org: ReadableOrgDep,
    store: StoreDep,
    path: DatasetPath,
    row: Annotated[int, Query(ge=0)],
    column: str,
    version: VersionQuery = None,
) -> Response:
    """One cell in full (up to 1 MiB)."""
    return JSONResponse(await datasets(store).cell(org, path, version, row, column))


@public.get("/versions")
async def versions(
    org: ReadableOrgDep,
    store: StoreDep,
    path: DatasetPath,
    cursor: Annotated[int | None, Query(ge=0)] = None,
) -> Response:
    """The dataset's versions, newest first, 50 a page, with their operations and tags."""
    return JSONResponse(await datasets(store).versions(org, path, cursor))


@public.get("/readme")
async def readme(org: ReadableOrgDep, store: StoreDep, path: DatasetPath) -> Response:
    """The README.md in the dataset's folder (its first 1 MiB), or 404."""
    return JSONResponse(await datasets(store).readme(org, path))


@router.put("/public")
async def make_public(
    org: AdminOrgDep, path: DatasetPath, user: UserDep, store: StoreDep
) -> Response:
    """Grant public access to exactly ``path`` (admins): anyone with its URL can read its page, at any version (D115). Its folder and the bucket's objects stay members-only."""
    await datasets(store).set_public(org, path, True, user)
    return JSONResponse({"path": path, "public": True})


@router.delete("/public", status_code=204)
async def make_private(
    org: AdminOrgDep, path: DatasetPath, user: UserDep, store: StoreDep
) -> Response:
    """Make the dataset at ``path`` private (admins), even before anything is written there."""
    await datasets(store).set_public(org, path, False, user)
    return Response(status_code=204)


@router.post("/delete")
async def delete(org: OrgDep, body: DeleteBody, user: UserDep, store: StoreDep) -> Response:
    """Move datasets or folders to the trash, where org admins can restore them for 14 days. A
    folder is one entry, with everything in it. With ``path``, answers its trash entry. With
    ``paths``, each goes in turn, and one that fails doesn't stop the rest: ``results`` says,
    for each, whether it was ``deleted`` and its trash ``entry``, or the ``error``. 409 with
    ``command`` (one line per path) when they're over the limits together, before anything
    moves."""
    try:
        if body.path is not None:
            entry = await datasets(store).delete(org, body.path, user)
            return JSONResponse(await entry_info(store, entry))
        assert body.paths is not None
        results = await datasets(store).delete_many(org, body.paths, user)
    except TooLarge as e:
        raise APIError(409, e.message, command=e.command) from None
    for result in results:
        if result["entry"] is not None:
            result["entry"] = await entry_info(store, result["entry"])
    return JSONResponse({"results": results})


@router.get("/trash")
async def trash(org: AdminOrgDep, store: StoreDep) -> Response:
    """The org's deleted datasets, newest first (admins)."""
    entries = await datasets(store).trash_entries(org)
    return JSONResponse({"entries": [await entry_info(store, e) for e in entries]})


@router.post("/trash/{pk}/restore")
async def restore(org: AdminOrgDep, pk: int, body: RestoreBody, store: StoreDep) -> Response:
    """Restore a deleted dataset to its path, or to ``to`` (admins). 409 with ``taken`` when
    something now exists there."""
    try:
        return JSONResponse(await datasets(store).restore(org, pk, body.to))
    except Taken as e:
        raise APIError(409, e.message, taken=True) from None


@router.delete("/trash/{pk}", status_code=204)
async def delete_now(org: AdminOrgDep, pk: int, store: StoreDep) -> Response:
    """Delete a deleted dataset for good (admins)."""
    await datasets(store).delete_now(org, pk)
    return Response(status_code=204)


@router.post("/rename")
async def rename(org: OrgDep, body: RenameBody, store: StoreDep) -> Response:
    """Move a dataset to the path ``to``, with all its versions and tags, or a folder with
    everything in it."""
    try:
        return JSONResponse(await datasets(store).rename(org, body.path, body.to))
    except Taken as e:
        raise APIError(409, e.message, taken=True) from None
    except TooLarge as e:
        raise APIError(409, e.message, command=e.command) from None


@router.post("/move")
async def move(org: OrgDep, body: MoveBody, store: StoreDep) -> Response:
    """Move datasets and folders into the folder ``to``, each keeping its last name:
    ``eval/mmlu`` moved to ``archive`` becomes ``archive/mmlu``. Each moves in turn, and one
    that fails doesn't stop the rest: ``results`` says, for each, where it goes (``to``),
    whether it ``moved``, and if not the ``error`` (``taken`` when something already exists
    there). 409 with ``command`` (one line per path) when they're over the limits together,
    before anything moves."""
    try:
        return JSONResponse({"results": await datasets(store).move(org, body.paths, body.to)})
    except TooLarge as e:
        raise APIError(409, e.message, command=e.command) from None


@router.put("/tags/{tag}")
async def put_tag(org: OrgDep, tag: str, body: TagBody, store: StoreDep) -> Response:
    """Tag ``version``. A tag on another version moves only with ``move``; otherwise 409 says
    which version it's on."""
    try:
        out = await datasets(store).set_tag(org, body.path, tag, body.version, body.move)
    except TagTaken as e:
        raise APIError(409, e.message, version=e.version) from None
    return JSONResponse(out)


@router.delete("/tags/{tag}", status_code=204)
async def delete_tag(org: OrgDep, tag: str, path: DatasetPath, store: StoreDep) -> Response:
    """Remove a tag. The version it was on stays."""
    await datasets(store).delete_tag(org, path, tag)
    return Response(status_code=204)


# ---------------------------------------------------------------------- forks and blends (023)


@router.post("/fork", status_code=201)
async def fork(org: OrgDep, body: ForkBody, user: UserDep, store: StoreDep) -> Response:
    """Fork ``path`` at ``version`` (default: the latest) to ``to``: a new dataset reading the
    source's files in place, edited without touching it (D171). The source version is pinned
    (D173). 409 when something exists at ``to``."""
    datasets(store)
    version = None if body.version in (None, "") else str(body.version)
    try:
        made = await store.forks.fork(org, user, body.path, version, body.to)
    except Taken as e:
        raise APIError(409, e.message, taken=True) from None
    return JSONResponse(made, status_code=201)


@router.post("/blend/preview")
async def blend_preview(org: OrgDep, body: BlendBody, store: StoreDep) -> Response:
    """What a blend would take from each source, its column map (the automatic one when
    ``columns`` is left out), the source columns no output uses, what will be written, and
    every problem. Writes nothing."""
    datasets(store)
    return JSONResponse(await store.forks.preview(org, body.request()))


@router.post("/blend", status_code=202)
async def blend(org: OrgDep, body: BlendBody, user: UserDep, store: StoreDep) -> Response:
    """Start building a blend at ``to`` with ``seed`` (D174). Answers its job. 422 naming
    every problem when it can't be made as asked; 409 when something exists at ``to``."""
    datasets(store)
    try:
        job = await store.forks.start_blend(org, user, body.request())
    except Taken as e:
        raise APIError(409, e.message, taken=True) from None
    return JSONResponse(await job_info(store, job), status_code=202)


@router.post("/independent", status_code=202)
async def independent(org: OrgDep, body: PathBody, user: UserDep, store: StoreDep) -> Response:
    """Start rewriting a fork or blend into its own files, releasing its sources (D178). Its
    earlier versions are removed. 409 with ``command`` over the size limit."""
    datasets(store)
    try:
        job = await store.forks.start_independent(org, user, body.path)
    except forks.TooLarge as e:
        raise APIError(409, e.message, command=e.command) from None
    return JSONResponse(await job_info(store, job), status_code=202)


@router.get("/jobs")
async def jobs(org: OrgDep, store: StoreDep) -> Response:
    """The org's blends and datasets made independent in the last week, newest first."""
    found = await store.forks.recent(org)
    return JSONResponse({"jobs": [await job_info(store, j) for j in found]})


@router.get("/jobs/{pk}")
async def job(org: OrgDep, pk: int, store: StoreDep) -> Response:
    return JSONResponse(await job_info(store, await store.forks.get(org, pk)))


@router.delete("/jobs/{pk}")
async def cancel_job(org: OrgDep, pk: int, user: UserDep, store: StoreDep) -> Response:
    """Cancel a running blend (who started it, or an admin). What it wrote is removed."""
    return JSONResponse(await job_info(store, await store.forks.cancel(org, pk, user)))
