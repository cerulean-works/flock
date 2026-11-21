"""The model registry's routes (014): models, versions, tags, the primary version, upload
sessions and the trash, in an org's flock-models bucket."""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from app.api.src.api.datasets import ProjectQuery, scope_of
from app.api.src.api.deps import (
    AdminOrgDep,
    APIError,
    JSONResponse,
    OrgDep,
    StoreDep,
    UserDep,
    rest_user,
)
from app.api.src.core.datasets import Scope
from app.api.src.core.models import Incomplete, Models, TagTaken, Taken
from app.api.src.core.store import Store

router = APIRouter(
    prefix="/api/v1/orgs/{org}/models", tags=["models"], dependencies=[Depends(rest_user)]
)

# Fields of answers that hold a user's pk, shown as the user.
USER_FIELDS = ("created_by", "deleted_by", "primary_set_by", "moved_by", "user")
RefQuery = Annotated[
    str | None,
    Query(max_length=128, description="a version number, a tag or primary (default: primary)"),
]


class FileBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=800)
    size: int = Field(ge=0)


class UploadBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    files: list[FileBody] = Field(min_length=1, max_length=Models.MAX_FILES)
    metadata: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list, max_length=100)
    primary: bool = Field(False, description="make the new version primary when it's committed")
    run: str | None = Field(
        None, max_length=512, description="the run that made it, entity/project/id"
    )
    step: int | None = Field(None, ge=0)
    dedupe_key: str | None = Field(None, min_length=1, max_length=256)


class HashBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=800)
    sha256: str = Field(min_length=64, max_length=64)


class CommitBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    files: list[HashBody] = Field(min_length=1, max_length=Models.MAX_FILES)


class VersionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)


class TagBody(VersionBody):
    move: bool = Field(False, description="move the tag if it's on another version")


class RenameBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(
        None, min_length=1, max_length=255, description="the new path (default: the same)"
    )
    project: str | None = Field(
        None, max_length=128, description="the scope to move to: a project, or null for global"
    )


def models(store: Store) -> Models:
    store.s3.require()
    return store.models


async def with_users(store: Store, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """``items`` with each user pk in ``USER_FIELDS`` (also in their ``tags``) as the user."""
    nested = [t for item in items for t in item.get("tags", ()) if isinstance(t, dict)]
    pks = [entry.get(f) for entry in items + nested for f in USER_FIELDS]
    briefs = await store.accounts.briefs(p for p in pks if isinstance(p, int))
    for entry in items + nested:
        for field in USER_FIELDS:
            if field in entry:
                pk = entry[field]
                entry[field] = briefs.get(pk) if isinstance(pk, int) else None
    return items


async def with_user(store: Store, item: dict[str, Any]) -> dict[str, Any]:
    return (await with_users(store, [item]))[0]


# ---------------------------------------------------------------------- the trash (D109)
# Under `-/`, which no model name can start with.


@router.get("/-/trash")
async def trash(org: AdminOrgDep, store: StoreDep, project: ProjectQuery = None) -> Response:
    """The org's deleted models, and deleted versions of live models, newest first (admins),
    each with its ``project`` (null: global); with ``project``, only that project's. Each
    waits 14 days before the sweep removes it."""
    scope = await scope_of(store, org, project) if project is not None else None
    entries = await models(store).trash_entries(org, scope)
    return JSONResponse({"entries": await with_users(store, entries)})


@router.post("/-/trash/versions/{pk}/restore")
async def restore_version(org: AdminOrgDep, pk: int, store: StoreDep) -> Response:
    """Restore a deleted version, with its number (admins)."""
    return JSONResponse(await with_user(store, await models(store).restore_version(org, pk)))


@router.delete("/-/trash/versions/{pk}", status_code=204)
async def delete_version_now(org: AdminOrgDep, pk: int, store: StoreDep) -> Response:
    """Delete a deleted version for good (admins)."""
    await models(store).delete_version_now(org, pk)
    return Response(status_code=204)


@router.post("/-/trash/models/{pk}/restore")
async def restore_model(org: AdminOrgDep, pk: int, store: StoreDep) -> Response:
    """Restore a deleted model with the versions deleted with it (admins). 409 with ``taken``
    when a model has its name now."""
    try:
        info = await models(store).restore_model(org, pk)
    except Taken as e:
        raise APIError(409, e.message, taken=True) from None
    return JSONResponse(await with_user(store, info))


@router.delete("/-/trash/models/{pk}", status_code=204)
async def delete_model_now(org: AdminOrgDep, pk: int, store: StoreDep) -> Response:
    """Delete a deleted model for good (admins)."""
    await models(store).delete_model_now(org, pk)
    return Response(status_code=204)


# ---------------------------------------------------------------------- folders (D158)


class FolderBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=255, description="the folder's path")


@router.post("/-/folders", status_code=201)
async def create_folder(
    org: OrgDep, body: FolderBody, user: UserDep, store: StoreDep, project: ProjectQuery = None
) -> Response:
    """Make an empty folder, which stays until it's deleted. 409 with ``taken`` when the path
    is a folder already, a model, live or deleted, or under a model."""
    scope = await scope_of(store, org, project)
    try:
        made = await models(store).create_folder(org, user, body.path, scope)
    except Taken as e:
        raise APIError(409, e.message, taken=True) from None
    return JSONResponse(made, status_code=201)


@router.delete("/-/folders", status_code=204)
async def delete_folder(
    org: OrgDep,
    store: StoreDep,
    path: Annotated[str, Query(min_length=1, max_length=255)],
    project: ProjectQuery = None,
) -> Response:
    """Delete an empty folder, with the empty folders in it. 409 while a model is under it."""
    await models(store).delete_folder(org, path, await scope_of(store, org, project))
    return Response(status_code=204)


# ---------------------------------------------------------------------- models and versions


@router.get("")
async def list_models(
    org: OrgDep,
    store: StoreDep,
    folder: Annotated[str, Query(max_length=255, description="the folder to list")] = "",
    project: ProjectQuery = None,
    cursor: Annotated[str | None, Query(max_length=300)] = None,
) -> Response:
    """One level of a folder of models (D158): its models, with their latest and primary
    version numbers, then its folders with how many models are under each, 100 a page.
    ``{kind: "model"}`` when ``folder`` is a model. The org's flock-models is created here if
    it's missing."""
    scope = await scope_of(store, org, project)
    await models(store).bucket(org, ensure=True)
    found = await models(store).list_folder(org, folder, scope, cursor)
    if found["kind"] == "folder":
        found["models"] = await with_users(store, found["models"])
    return JSONResponse(found)


def both(method: str, path: str, **options: Any) -> Any:
    """Register a handler at the one-segment route ``/{name}{path}`` (global models, as before
    022) and at ``/{name:path}/-{path}``, where the model's name may hold folders (D163)."""
    route = getattr(router, method)

    def register(handler: Any) -> Any:
        route(f"/{{name}}{path}", **options)(handler)
        return route(f"/{{name:path}}/-{path}", **options)(handler)

    return register


async def scope(store: Store, org: Any, project: str | None) -> Scope:
    return await scope_of(store, org, project)


@both("get", "")
async def get_model(
    org: OrgDep, name: str, store: StoreDep, project: ProjectQuery = None
) -> Response:
    """A model: its primary version (and who made it primary), its latest and its tags."""
    found = await models(store).model_info(org, name, await scope(store, org, project))
    return JSONResponse(await with_user(store, found))


@both("patch", "")
async def rename_model(
    org: OrgDep, name: str, body: RenameBody, store: StoreDep, project: ProjectQuery = None
) -> Response:
    """Rename a model, move it to another folder, or to another scope with ``project`` (null:
    global, D166). Only the registry changes: its files stay in its folder (``prefix``), and
    the old name stops resolving. 409 with ``taken`` when the new path is a model, live or
    deleted, a folder of models or under a model, and 409 while an upload to the model is
    open."""
    source = await scope(store, org, project)
    target = await scope(store, org, body.project) if "project" in body.model_fields_set else None
    try:
        info = await models(store).rename(org, name, body.name or name, source, target)
    except Taken as e:
        raise APIError(409, e.message, taken=True) from None
    return JSONResponse(await with_user(store, info))


@both("delete", "")
async def delete_model(
    org: OrgDep, name: str, user: UserDep, store: StoreDep, project: ProjectQuery = None
) -> Response:
    """Delete a model with every version in it. Admins can restore it for 14 days."""
    found = await models(store).delete_model(org, name, user, await scope(store, org, project))
    return JSONResponse(await with_user(store, found))


@both("get", "/resolve")
async def resolve(
    org: OrgDep, name: str, store: StoreDep, ref: RefQuery = None, project: ProjectQuery = None
) -> Response:
    """The version ``ref`` names (no ``ref``: the primary), with its files and their sha256:
    what a download needs. Use its ``number`` from then on."""
    found = await models(store).version(org, name, ref, await scope(store, org, project))
    return JSONResponse(await with_user(store, found))


@both("get", "/versions")
async def versions(
    org: OrgDep,
    name: str,
    store: StoreDep,
    before: Annotated[int | None, Query(ge=1, description="the cursor of the next page")] = None,
    checkpoints: Annotated[bool, Query(description="only versions with checkpoint tags")] = False,
    project: ProjectQuery = None,
) -> Response:
    """Live versions, newest first, 100 a page."""
    found = await scope(store, org, project)
    page = await models(store).versions(org, name, before, checkpoints, found)
    page["versions"] = await with_users(store, page["versions"])
    return JSONResponse(page)


@both("get", "/versions/{number}")
async def get_version(
    org: OrgDep, name: str, number: int, store: StoreDep, project: ProjectQuery = None
) -> Response:
    """A version with its files."""
    found = await models(store).version(org, name, str(number), await scope(store, org, project))
    return JSONResponse(await with_user(store, found))


@both("delete", "/versions/{number}")
async def delete_version(
    org: OrgDep,
    name: str,
    number: int,
    user: UserDep,
    store: StoreDep,
    project: ProjectQuery = None,
) -> Response:
    """Delete a version (409 for the primary, or one with a tag other than
    ``checkpoint-{step}``). Admins can restore it for 14 days."""
    found = await scope(store, org, project)
    info = await models(store).delete_version(org, name, number, user, found)
    return JSONResponse(await with_user(store, info))


@both("put", "/primary")
async def set_primary(
    org: OrgDep,
    name: str,
    body: VersionBody,
    user: UserDep,
    store: StoreDep,
    project: ProjectQuery = None,
) -> Response:
    """Make ``version`` the primary: what loads when no version is given. ``previous`` is the
    number of the one it replaced."""
    found = await scope(store, org, project)
    info = await models(store).set_primary(org, name, body.version, user, found)
    return JSONResponse(await with_user(store, info))


@both("put", "/tags/{tag}")
async def put_tag(
    org: OrgDep,
    name: str,
    tag: str,
    body: TagBody,
    user: UserDep,
    store: StoreDep,
    project: ProjectQuery = None,
) -> Response:
    """Tag ``version``. A tag on another version moves only with ``move``, and checkpoint tags
    never do: otherwise 409 says which version it's on."""
    found = await scope(store, org, project)
    try:
        out = await models(store).set_tag(org, name, tag, body.version, body.move, user, found)
    except TagTaken as e:
        raise APIError(409, e.message, version=e.version) from None
    return JSONResponse(out)


@both("delete", "/tags/{tag}", status_code=204)
async def delete_tag(
    org: OrgDep, name: str, tag: str, store: StoreDep, project: ProjectQuery = None
) -> Response:
    """Remove a tag. The version it was on stays."""
    await models(store).delete_tag(org, name, tag, await scope(store, org, project))
    return Response(status_code=204)


# ---------------------------------------------------------------------- uploads (D100)


@both("post", "/uploads")
async def open_upload(
    org: OrgDep,
    name: str,
    body: UploadBody,
    user: UserDep,
    store: StoreDep,
    project: ProjectQuery = None,
) -> Response:
    """Open an upload of a new version, creating the model if it's new (in ``project``, if
    given): 201 with the folder to write the files to (``prefix`` of ``bucket``) and the part
    size. 200 with the version instead when a live one has ``dedupe_key``. 409 with ``taken``
    when a new model's path is a folder of models or under a model."""
    found = await scope(store, org, project)
    try:
        opened, info = await models(store).open_upload(
            org,
            name,
            user,
            [f.model_dump() for f in body.files],
            body.metadata,
            body.tags,
            body.primary,
            body.run,
            body.step,
            body.dedupe_key,
            found,
        )
    except TagTaken as e:
        raise APIError(409, e.message, version=e.version) from None
    except Taken as e:
        raise APIError(409, e.message, taken=True) from None
    return JSONResponse(await with_user(store, info), status_code=201 if opened else 200)


@both("get", "/uploads")
async def list_uploads(
    org: OrgDep,
    name: str,
    user: UserDep,
    store: StoreDep,
    state: Literal["open", "committed", "aborted", "expired"] = "open",
    project: ProjectQuery = None,
) -> Response:
    """Your uploads of the model in ``state``, oldest first."""
    found = await models(store).uploads(org, name, user, state, await scope(store, org, project))
    return JSONResponse({"uploads": await with_users(store, found)})


@both("get", "/uploads/{ulid}")
async def get_upload(
    org: OrgDep, name: str, ulid: str, store: StoreDep, project: ProjectQuery = None
) -> Response:
    """An upload's state and declared files."""
    found = await models(store).get_upload(org, name, ulid, await scope(store, org, project))
    return JSONResponse(await with_user(store, found))


@both("post", "/uploads/{ulid}/commit")
async def commit(
    org: OrgDep,
    name: str,
    ulid: str,
    body: CommitBody,
    user: UserDep,
    store: StoreDep,
    project: ProjectQuery = None,
) -> Response:
    """Make the upload a version, given each file's sha256. 409 with the paths when the
    objects aren't the declared files (``missing``, ``extra``, ``resized``, ``uploading``) or a
    sha256 doesn't match the object's ``x-amz-meta-sha256`` (``mismatched``); 422 when a shard
    isn't safetensors. The upload stays open after either. Committing again answers the same
    version."""
    hashes = {f.path: f.sha256 for f in body.files}
    if len(hashes) != len(body.files):
        raise APIError(422, "a file is given twice")
    found = await scope(store, org, project)
    try:
        info = await models(store).commit(org, name, ulid, user, hashes, found)
    except Incomplete as e:
        raise APIError(409, e.message, **e.paths) from None
    except TagTaken as e:
        raise APIError(409, e.message, version=e.version) from None
    return JSONResponse(await with_user(store, info))


@both("delete", "/uploads/{ulid}", status_code=204)
async def abort_upload(
    org: OrgDep, name: str, ulid: str, user: UserDep, store: StoreDep, project: ProjectQuery = None
) -> Response:
    """Abort an open upload (its opener, or an admin) and remove what it wrote."""
    await models(store).abort(org, name, ulid, user, await scope(store, org, project))
    return Response(status_code=204)
