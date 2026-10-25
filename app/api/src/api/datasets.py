import datetime as dt
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from app.api.src.api.deps import (
    APIError,
    JSONResponse,
    Name,
    StoreDep,
    UserDep,
    current_user,
    hub_enabled,
    visible_org,
)
from app.api.src.core.datasets import Dataset
from app.api.src.core.store import Store

Visibility = Literal["private", "internal"]


def hub_writable(store: StoreDep) -> None:
    """Dataset writes go through the S3 proxy (D49), so they need to know where it is."""
    if not store.settings.hub_proxy_url:
        raise APIError(503, "dataset writes need FLOCK_HUB_PROXY_URL, which is not set")


router = APIRouter(
    prefix="/api/v1",
    tags=["datasets"],
    dependencies=[Depends(current_user), Depends(hub_enabled)],
)
Writes = [Depends(hub_writable)]


class DatasetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name
    description: str = ""
    visibility: Visibility = "private"


class DatasetUpdate(BaseModel):
    """Fields to change. Renaming and visibility are for org admins."""

    model_config = ConfigDict(extra="forbid")

    name: Name | None = None
    description: str | None = None
    visibility: Visibility | None = None


class MessageBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: Annotated[str, Field(min_length=1, max_length=10_000)]


class ReadmeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str
    message: str | None = None


class TagBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Annotated[int, Field(ge=1)]


class RestoreBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Annotated[int, Field(ge=1)]
    message: str | None = None


class PruneBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    older_than_days: Annotated[float, Field(ge=0)]


# Lance keeps a tag as the file _refs/tags/<name>.json.
Tag = Annotated[str, Path(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")]


def get_dataset(org: str, name: str, store: StoreDep, user: UserDep) -> Dataset:
    """A dataset the user can read; others get the same 404 as unknown datasets."""
    found_org = store.orgs.get(org)
    found = store.datasets.get(found_org, name) if found_org is not None else None
    if found is None or not store.datasets.can_read(user, found):
        raise APIError(404, f"dataset {org}/{name} not found")
    return found


DatasetDep = Annotated[Dataset, Depends(get_dataset)]


def get_member_dataset(dataset: DatasetDep, store: StoreDep, user: UserDep) -> Dataset:
    if store.accounts.role(user, dataset.org) is None:
        raise APIError(403, f"only members of {dataset.org.name} can do this")
    return dataset


MemberDatasetDep = Annotated[Dataset, Depends(get_member_dataset)]


def get_admin_dataset(dataset: MemberDatasetDep, store: StoreDep, user: UserDep) -> Dataset:
    if store.accounts.role(user, dataset.org) != "admin":
        raise APIError(403, f"only admins of {dataset.org.name} can do this")
    return dataset


AdminDatasetDep = Annotated[Dataset, Depends(get_admin_dataset)]


async def info(store: Store, dataset: Dataset, user: UserDep) -> dict:
    return store.datasets.info(dataset, user, await store.datasets.latest_one(dataset))


@router.get("/datasets/{org}")
async def list_datasets(org: str, store: StoreDep, user: UserDep) -> Response:
    """An org's datasets: all of them for its members, the internal ones for everyone else."""
    found = store.orgs.get(org)
    datasets = [
        d
        for d in (store.datasets.of_org(found) if found is not None else [])
        if store.datasets.can_read(user, d)
    ]
    if found is None or (store.accounts.role(user, found) is None and not datasets):
        raise APIError(404, f"organization {org} not found")
    latest = await store.datasets.latest(datasets)
    return JSONResponse(
        {"datasets": [store.datasets.info(d, user, latest.get(d.pk)) for d in datasets]}
    )


@router.post("/datasets/{org}", status_code=201)
async def create_dataset(org: str, body: DatasetCreate, store: StoreDep, user: UserDep) -> Response:
    """Create an empty dataset; its first version is its first Lance write."""
    found = visible_org(org, store, user)
    dataset = await store.datasets.create(found, body.name, user, body.description, body.visibility)
    return JSONResponse(store.datasets.info(dataset, user, None), status_code=201)


@router.get("/datasets/{org}/{name}")
async def get_dataset_info(dataset: DatasetDep, store: StoreDep, user: UserDep) -> Response:
    return JSONResponse(await info(store, dataset, user))


@router.patch("/datasets/{org}/{name}")
async def update_dataset(
    dataset: MemberDatasetDep, body: DatasetUpdate, store: StoreDep, user: UserDep
) -> Response:
    """Change the description (members), or rename or change visibility (org admins).
    Clients address the dataset by name, so a rename moves its URI."""
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if ("name" in fields or "visibility" in fields) and store.accounts.role(
        user, dataset.org
    ) != "admin":
        raise APIError(403, f"only admins of {dataset.org.name} can rename or change visibility")
    await store.datasets.update(dataset, fields)
    return JSONResponse(await info(store, dataset, user))


@router.delete("/datasets/{org}/{name}", status_code=204)
async def delete_dataset(dataset: AdminDatasetDep, store: StoreDep) -> Response:
    """Delete a dataset with every version (org admins only). The name is free at once; the
    data is deleted in the background. There is no undo."""
    await store.datasets.delete(dataset)
    return Response(status_code=204)


@router.get("/datasets/{org}/{name}/versions")
async def list_versions(
    dataset: DatasetDep,
    store: StoreDep,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> Response:
    """The version history, newest first, including pruned versions."""
    commits, tags = await store.datasets.history(dataset)
    page = commits[offset : offset + limit]
    return JSONResponse(
        {
            "versions": [store.datasets.commit_info(c, tags.get(c.version, [])) for c in page],
            "total": len(commits),
        }
    )


@router.get("/datasets/{org}/{name}/versions/{version}")
async def get_version(dataset: DatasetDep, version: int, store: StoreDep) -> Response:
    """A version with its schema, Hugging Face features and table metadata."""
    commit, tags = await store.datasets.commit(dataset, version)
    details = await store.datasets.details(dataset, version)
    return JSONResponse({**store.datasets.commit_info(commit, tags), **details})


@router.patch("/datasets/{org}/{name}/versions/{version}")
async def set_version_message(
    dataset: MemberDatasetDep, version: int, body: MessageBody, store: StoreDep, user: UserDep
) -> Response:
    """Set the message of a version that has none, for edits that can't carry one (such as
    deletes). Only its author or an org admin can, and only once."""
    commit, tags = await store.datasets.set_message(dataset, version, user, body.message)
    return JSONResponse(store.datasets.commit_info(commit, tags))


@router.get("/datasets/{org}/{name}/rows")
async def list_rows(
    dataset: DatasetDep,
    store: StoreDep,
    version: Annotated[int | None, Query(ge=1, description="the latest if left out")] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    columns: Annotated[str | None, Query(description="comma-separated column names")] = None,
) -> Response:
    """A page of rows. Long strings are cut (listed in ``truncated``), binary values show
    their size, times are ISO 8601 strings."""
    names = [c.strip() for c in columns.split(",") if c.strip()] if columns else None
    try:
        page = await store.datasets.rows(dataset, version, offset, limit, names)
    except ValueError as exc:
        raise APIError(400, str(exc)) from exc
    return JSONResponse(page)


@router.get("/datasets/{org}/{name}/readme")
async def get_readme(
    dataset: DatasetDep,
    store: StoreDep,
    version: Annotated[int | None, Query(ge=1, description="the latest if left out")] = None,
) -> Response:
    return JSONResponse(await store.datasets.readme(dataset, version))


@router.put("/datasets/{org}/{name}/readme", dependencies=Writes)
async def set_readme(
    dataset: MemberDatasetDep, body: ReadmeBody, store: StoreDep, user: UserDep
) -> Response:
    """Replace the card; this makes a new version."""
    version = await store.datasets.set_readme(dataset, user, body.content, body.message)
    return JSONResponse({"version": version})


@router.put("/datasets/{org}/{name}/tags/{tag}", dependencies=Writes)
async def set_tag(
    dataset: MemberDatasetDep, tag: Tag, body: TagBody, store: StoreDep, user: UserDep
) -> Response:
    """Point a tag at a version, creating or moving it. Tags don't make versions."""
    await store.datasets.set_tag(dataset, user, tag, body.version)
    return JSONResponse({"tags": await store.datasets.tags(dataset)})


@router.delete("/datasets/{org}/{name}/tags/{tag}", dependencies=Writes)
async def delete_tag(
    dataset: MemberDatasetDep, tag: Tag, store: StoreDep, user: UserDep
) -> Response:
    await store.datasets.delete_tag(dataset, user, tag)
    return JSONResponse({"tags": await store.datasets.tags(dataset)})


@router.post("/datasets/{org}/{name}/restore", dependencies=Writes)
async def restore(
    dataset: MemberDatasetDep, body: RestoreBody, store: StoreDep, user: UserDep
) -> Response:
    """Make an earlier version the latest again, as a new version."""
    version = await store.datasets.restore(dataset, user, body.version, body.message)
    return JSONResponse({"version": version})


@router.post("/datasets/{org}/{name}/optimize", dependencies=Writes)
async def optimize(dataset: MemberDatasetDep, store: StoreDep, user: UserDep) -> Response:
    """Compact small fragments into a new version (none if there is nothing to compact)."""
    return JSONResponse({"version": await store.datasets.optimize(dataset, user)})


@router.post("/datasets/{org}/{name}/prune", dependencies=Writes)
async def prune(
    dataset: AdminDatasetDep, body: PruneBody, store: StoreDep, user: UserDep
) -> Response:
    """Delete versions older than ``older_than_days`` and the files only they use (org
    admins only). The latest and tagged versions stay; history keeps the pruned ones."""
    removed, size = await store.datasets.prune(
        dataset, user, dt.timedelta(days=body.older_than_days)
    )
    return JSONResponse({"versions_removed": removed, "bytes_removed": size})
