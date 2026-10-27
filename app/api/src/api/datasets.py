"""The Datasets tab's routes (D94): the Lance datasets in an org's flock-datasets bucket."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from app.api.src.api.deps import (
    AdminOrgDep,
    APIError,
    JSONResponse,
    OrgDep,
    StoreDep,
    UserDep,
    rest_user,
)
from app.api.src.core.datasets import Datasets, TagTaken, Taken, TooLarge
from app.api.src.core.store import Store

router = APIRouter(
    prefix="/api/v1/orgs/{org}/datasets", tags=["datasets"], dependencies=[Depends(rest_user)]
)

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


class RestoreBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to: str | None = Field(
        None, min_length=1, max_length=512, description="restore here instead of the old path"
    )


class TagBody(PathBody):
    version: int = Field(ge=0)
    move: bool = Field(False, description="move the tag if it's on another version")


def entry_info(store: Store, entry: dict[str, Any]) -> dict[str, Any]:
    user = store.accounts.users.get(entry["deleted_by"]) if entry["deleted_by"] else None
    return {**entry, "deleted_by": store.accounts.brief(user) if user is not None else None}


def datasets(store: Store) -> Datasets:
    store.s3.require()
    return store.datasets


@router.get("/at")
async def at(org: OrgDep, store: StoreDep, path: str = "", cursor: str | None = None) -> Response:
    """A folder of flock-datasets, one level (100 entries a page): its datasets (with their
    latest version), folders and files. ``{kind: "dataset"}`` when ``path`` is a dataset. The
    org's flock-datasets is created here if it's missing."""
    return JSONResponse(await datasets(store).at(org, path, cursor))


@router.get("/info")
async def info(
    org: OrgDep, store: StoreDep, path: DatasetPath, version: VersionQuery = None
) -> Response:
    """The dataset at ``version``: its numbers, schema, details and tags, with the version
    resolved (and the latest)."""
    return JSONResponse(await datasets(store).info(org, path, version))


@router.get("/rows")
async def rows(
    org: OrgDep,
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


@router.get("/cell")
async def cell(
    org: OrgDep,
    store: StoreDep,
    path: DatasetPath,
    row: Annotated[int, Query(ge=0)],
    column: str,
    version: VersionQuery = None,
) -> Response:
    """One cell in full (up to 1 MiB)."""
    return JSONResponse(await datasets(store).cell(org, path, version, row, column))


@router.get("/versions")
async def versions(
    org: OrgDep,
    store: StoreDep,
    path: DatasetPath,
    cursor: Annotated[int | None, Query(ge=0)] = None,
) -> Response:
    """The dataset's versions, newest first, 50 a page, with their operations and tags."""
    return JSONResponse(await datasets(store).versions(org, path, cursor))


@router.get("/readme")
async def readme(org: OrgDep, store: StoreDep, path: DatasetPath) -> Response:
    """The README.md in the dataset's folder (its first 1 MiB), or 404."""
    return JSONResponse(await datasets(store).readme(org, path))


@router.post("/delete")
async def delete(org: OrgDep, body: PathBody, user: UserDep, store: StoreDep) -> Response:
    """Move a dataset to the trash, where org admins can restore it for 14 days."""
    try:
        entry = await datasets(store).delete(org, body.path, user)
    except TooLarge as e:
        raise APIError(409, e.message, command=e.command) from None
    return JSONResponse(entry_info(store, entry))


@router.get("/trash")
async def trash(org: AdminOrgDep, store: StoreDep) -> Response:
    """The org's deleted datasets, newest first (admins)."""
    entries = await datasets(store).trash_entries(org)
    return JSONResponse({"entries": [entry_info(store, e) for e in entries]})


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
    """Move a dataset to the path ``to``, with all its versions and tags."""
    try:
        return JSONResponse(await datasets(store).rename(org, body.path, body.to))
    except Taken as e:
        raise APIError(409, e.message, taken=True) from None
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
