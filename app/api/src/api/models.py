"""The model registry's routes (014): models, versions, tags, the primary version, upload
sessions and the trash, in an org's flock-models bucket."""

from typing import Annotated, Any, Literal

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
async def trash(org: AdminOrgDep, store: StoreDep) -> Response:
    """The org's deleted models, and deleted versions of live models, newest first (admins).
    Each waits 14 days before the sweep removes it."""
    entries = await models(store).trash_entries(org)
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


# ---------------------------------------------------------------------- models and versions


@router.get("")
async def list_models(org: OrgDep, store: StoreDep) -> Response:
    """The org's models by name, with their latest and primary version numbers. The org's
    flock-models is created here if it's missing."""
    await models(store).bucket(org, ensure=True)
    return JSONResponse({"models": await with_users(store, await models(store).list_models(org))})


@router.get("/{name}")
async def get_model(org: OrgDep, name: str, store: StoreDep) -> Response:
    """A model: its primary version (and who made it primary), its latest and its tags."""
    return JSONResponse(await with_user(store, await models(store).model_info(org, name)))


@router.delete("/{name}")
async def delete_model(org: OrgDep, name: str, user: UserDep, store: StoreDep) -> Response:
    """Delete a model with every version in it. Admins can restore it for 14 days."""
    return JSONResponse(await with_user(store, await models(store).delete_model(org, name, user)))


@router.get("/{name}/resolve")
async def resolve(org: OrgDep, name: str, store: StoreDep, ref: RefQuery = None) -> Response:
    """The version ``ref`` names (no ``ref``: the primary), with its files and their sha256:
    what a download needs. Use its ``number`` from then on."""
    return JSONResponse(await with_user(store, await models(store).version(org, name, ref)))


@router.get("/{name}/versions")
async def versions(
    org: OrgDep,
    name: str,
    store: StoreDep,
    before: Annotated[int | None, Query(ge=1, description="the cursor of the next page")] = None,
    checkpoints: Annotated[bool, Query(description="only versions with checkpoint tags")] = False,
) -> Response:
    """Live versions, newest first, 100 a page."""
    page = await models(store).versions(org, name, before, checkpoints)
    page["versions"] = await with_users(store, page["versions"])
    return JSONResponse(page)


@router.get("/{name}/versions/{number}")
async def get_version(org: OrgDep, name: str, number: int, store: StoreDep) -> Response:
    """A version with its files and eval runs."""
    return JSONResponse(await with_user(store, await models(store).version(org, name, str(number))))


@router.delete("/{name}/versions/{number}")
async def delete_version(
    org: OrgDep, name: str, number: int, user: UserDep, store: StoreDep
) -> Response:
    """Delete a version (409 for the primary, or one with a tag other than
    ``checkpoint-{step}``). Admins can restore it for 14 days."""
    info = await models(store).delete_version(org, name, number, user)
    return JSONResponse(await with_user(store, info))


@router.put("/{name}/primary")
async def set_primary(
    org: OrgDep, name: str, body: VersionBody, user: UserDep, store: StoreDep
) -> Response:
    """Make ``version`` the primary: what loads when no version is given. ``previous`` is the
    number of the one it replaced."""
    info = await models(store).set_primary(org, name, body.version, user)
    return JSONResponse(await with_user(store, info))


@router.put("/{name}/tags/{tag}")
async def put_tag(
    org: OrgDep, name: str, tag: str, body: TagBody, user: UserDep, store: StoreDep
) -> Response:
    """Tag ``version``. A tag on another version moves only with ``move``, and checkpoint tags
    never do: otherwise 409 says which version it's on."""
    try:
        out = await models(store).set_tag(org, name, tag, body.version, body.move, user)
    except TagTaken as e:
        raise APIError(409, e.message, version=e.version) from None
    return JSONResponse(out)


@router.delete("/{name}/tags/{tag}", status_code=204)
async def delete_tag(org: OrgDep, name: str, tag: str, store: StoreDep) -> Response:
    """Remove a tag. The version it was on stays."""
    await models(store).delete_tag(org, name, tag)
    return Response(status_code=204)


# ---------------------------------------------------------------------- uploads (D100)


@router.post("/{name}/uploads")
async def open_upload(
    org: OrgDep, name: str, body: UploadBody, user: UserDep, store: StoreDep
) -> Response:
    """Open an upload of a new version, creating the model if it's new: 201 with the folder
    to write the files to (``prefix`` of ``bucket``) and the part size. 200 with the version
    instead when a live one has ``dedupe_key``."""
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
        )
    except TagTaken as e:
        raise APIError(409, e.message, version=e.version) from None
    return JSONResponse(await with_user(store, info), status_code=201 if opened else 200)


@router.get("/{name}/uploads")
async def list_uploads(
    org: OrgDep,
    name: str,
    user: UserDep,
    store: StoreDep,
    state: Literal["open", "committed", "aborted", "expired"] = "open",
) -> Response:
    """Your uploads of the model in ``state``, oldest first."""
    found = await models(store).uploads(org, name, user, state)
    return JSONResponse({"uploads": await with_users(store, found)})


@router.get("/{name}/uploads/{ulid}")
async def get_upload(org: OrgDep, name: str, ulid: str, store: StoreDep) -> Response:
    """An upload's state and declared files."""
    return JSONResponse(await with_user(store, await models(store).get_upload(org, name, ulid)))


@router.post("/{name}/uploads/{ulid}/commit")
async def commit(
    org: OrgDep, name: str, ulid: str, body: CommitBody, user: UserDep, store: StoreDep
) -> Response:
    """Make the upload a version, given each file's sha256. 409 with the paths when the
    objects aren't the declared files (``missing``, ``extra``, ``resized``, ``uploading``) or a
    sha256 doesn't match the object's ``x-amz-meta-sha256`` (``mismatched``); 422 when a shard
    isn't safetensors. The upload stays open after either. Committing again answers the same
    version."""
    hashes = {f.path: f.sha256 for f in body.files}
    if len(hashes) != len(body.files):
        raise APIError(422, "a file is given twice")
    try:
        info = await models(store).commit(org, name, ulid, user, hashes)
    except Incomplete as e:
        raise APIError(409, e.message, **e.paths) from None
    except TagTaken as e:
        raise APIError(409, e.message, version=e.version) from None
    return JSONResponse(await with_user(store, info))


@router.delete("/{name}/uploads/{ulid}", status_code=204)
async def abort_upload(
    org: OrgDep, name: str, ulid: str, user: UserDep, store: StoreDep
) -> Response:
    """Abort an open upload (its opener, or an admin) and remove what it wrote."""
    await models(store).abort(org, name, ulid, user)
    return Response(status_code=204)
