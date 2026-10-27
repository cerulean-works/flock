from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from app.api.src.api.deps import (
    AdminOrgDep,
    APIError,
    JSONResponse,
    OrgDep,
    SessionUserDep,
    StoreDep,
    UserDep,
    rest_user,
    visible_org,
)
from app.api.src.core.accounts import User
from app.api.src.core.s3 import S3, Bucket, Key, TooMany
from app.api.src.core.store import Org, Store

router = APIRouter(prefix="/api/v1", tags=["s3"], dependencies=[Depends(rest_user)])

KeyName = Annotated[str, Field(min_length=1, max_length=128)]


class CredentialsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    org: str | None = Field(
        None, description="the org whose buckets the key covers (default: yours)"
    )
    ttl: float | None = Field(
        None, gt=0, description="seconds the key lives, at most the org's ephemeral lifetime"
    )
    read_only: bool = False


class KeyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: KeyName
    org: str | None = Field(
        None, description="the org whose buckets the key covers (default: yours)"
    )
    read_only: bool = False
    ttl: float | None = Field(
        gt=0, description="seconds the key lives, at most the org's maximum; null: forever"
    )


class BucketCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(max_length=63)


async def key_org(store: Store, user: User, name: str | None) -> Org:
    """The org ``name`` (the user's default org when None), which the user must belong to."""
    if name is not None:
        return await visible_org(name, store, user)
    org = await store.accounts.default_org(user)
    if org is None:
        raise APIError(404, "you don't belong to any organization")
    return org


def via(request: Request) -> dict[str, Any]:
    """How a request was made, as bucket events record it: the dashboard (a browser session)
    or the CLI (with its session and machine). Events from before D72 may say ``"api"``, with
    the API key that made them."""
    session = request.state.session  # the router refuses wandb keys, which have none
    if session.kind == "cli":
        return {"via": "cli", "session": session.pk, "machine": session.name}
    return {"via": "ui", "session": session.pk}


async def key_info(store: Store, key: Key) -> dict[str, Any]:
    org = await store.org_by_pk(key.org)
    return {
        "id": key.pk,
        "name": key.name,
        "access_key_id": key.access_key_id,
        "org": org.name if org is not None else None,
        "read_only": key.read_only,
        "ephemeral": key.ephemeral,
        # The CLI sign-in (or, before D72, the wandb key) an ephemeral key was issued for,
        # and its name.
        "session_id": key.session,
        "api_key_id": key.api_key,
        "source": key.source,
        "created_at": key.created_at,
        "expires_at": await store.s3.expires_at(key),
        "last_used_at": key.last_used_at,
    }


async def with_secret(store: Store, key: Key, secret: str) -> dict[str, Any]:
    config = store.s3.config() or {}
    return {
        **(await key_info(store, key)),
        "secret_access_key": secret,
        "endpoint": config.get("endpoint"),
        "region": config.get("region"),
    }


async def bucket_info(store: Store, bucket: Bucket) -> dict[str, Any]:
    creator = await store.accounts.user(bucket.created_by)
    info = {
        "name": bucket.name,
        "upstream_name": bucket.upstream_name,
        "created_at": bucket.created_at,
        "created_by": store.accounts.brief(creator) if creator is not None else None,
    }
    if bucket.deleted_at is not None:  # still being emptied (listed with ``deleting``)
        info["deleted_at"] = bucket.deleted_at
    return info


# ---------------------------------------------------------------------- credentials


@router.post("/s3/credentials")
async def s3_credentials(
    body: CredentialsRequest, request: Request, user: UserDep, store: StoreDep
) -> Response:
    """Exchange the request's credential (a CLI token or browser session) for an ephemeral S3
    key. wandb keys can't be exchanged (D72).

    It covers ``org``'s buckets and lives ``ttl`` seconds, or the org's ephemeral lifetime if
    that is shorter. It's revoked with the credential that asked for it. Each call makes a new
    key. 404 when this server has no S3.
    """
    store.s3.require()
    org = await key_org(store, user, body.org)
    session = request.state.session
    # The key is named after the machine of a CLI sign-in.
    name = (session.name if session.kind == "cli" else None) or "browser"
    key, secret = await store.s3.issue_ephemeral(
        user, org, name, body.ttl, body.read_only, session=session.pk
    )
    config = store.s3.config() or {}
    return JSONResponse(
        {
            "endpoint": config.get("endpoint"),
            "region": config.get("region"),
            "org": org.name,
            "access_key_id": key.access_key_id,
            "secret_access_key": secret,
            "read_only": key.read_only,
            "expires_at": key.expires_at,
        }
    )


@router.get("/me/s3-keys")
async def list_s3_keys(user: UserDep, store: StoreDep) -> Response:
    """Your S3 keys that still work: long-lived ones, and the ephemeral ones your CLI
    sign-ins (and, before D72, your wandb keys) were issued."""
    store.s3.require()
    keys = await store.s3.keys(user)
    return JSONResponse({"keys": [await key_info(store, key) for key in keys]})


@router.post("/me/s3-keys", status_code=201)
async def create_s3_key(body: KeyCreate, user: SessionUserDep, store: StoreDep) -> Response:
    """A long-lived S3 key for ``org``, living ``ttl`` seconds (null: forever) within the org's
    maximum key lifetime. Its secret is shown only now. Only a signed-in browser can create one,
    so a leaked CLI token can't mint keys that outlive the ephemeral lifetime."""
    store.s3.require()
    org = await key_org(store, user, body.org)
    key, secret = await store.s3.create_key(user, org, body.name, body.read_only, body.ttl)
    return JSONResponse(await with_secret(store, key, secret), status_code=201)


@router.delete("/me/s3-keys/{key_id}", status_code=204)
async def delete_s3_key(key_id: int, user: UserDep, store: StoreDep) -> Response:
    """Revoke one of your S3 keys: the proxy refuses it from its next request."""
    store.s3.require()
    await store.s3.delete_key(user, key_id)
    return Response(status_code=204)


# ---------------------------------------------------------------------- buckets


@router.get("/orgs/{org}/buckets")
async def list_buckets(org: OrgDep, store: StoreDep, deleting: bool = False) -> Response:
    """The organization's S3 buckets, by name. With ``deleting``, buckets deleted but still
    being emptied are listed too, with their ``deleted_at``: their names can't be used yet."""
    store.s3.require()
    buckets = await store.s3.buckets(org, deleting)
    return JSONResponse(
        {
            "buckets": [await bucket_info(store, bucket) for bucket in buckets],
            "name_limit": store.s3.name_limit,
        }
    )


@router.post("/orgs/{org}/buckets", status_code=201)
async def create_bucket(
    org: OrgDep, body: BucketCreate, request: Request, user: UserDep, store: StoreDep
) -> Response:
    """Create a bucket (any member). Names are lowercase letters, digits, hyphens and dots,
    unique in the organization; see ``name_limit`` in the listing for their length."""
    bucket = await store.s3.create_bucket(org, body.name, user, via(request))
    return JSONResponse(await bucket_info(store, bucket), status_code=201)


@router.get("/orgs/{org}/buckets/{name}")
async def get_bucket(org: OrgDep, name: str, store: StoreDep) -> Response:
    """A bucket, with its history: who created it, and how."""
    store.s3.require()
    bucket = await store.s3.bucket(org, name)
    events = await store.s3.events(org, name)
    actors = await store.accounts.briefs(event["user"] for event in events)
    for event in events:
        event["user"] = actors.get(event["user"]) if event["user"] else None
    return JSONResponse({**await bucket_info(store, bucket), "events": events})


@router.delete("/orgs/{org}/buckets/{name}", status_code=204)
async def delete_bucket(
    org: AdminOrgDep, name: str, request: Request, user: UserDep, store: StoreDep
) -> Response:
    """Delete a bucket and everything in it (admins). Its name is free again once the S3
    server has removed it, which happens in the background."""
    await store.s3.delete_bucket(org, name, user, via(request))
    return Response(status_code=204)


# ---------------------------------------------------------------------- objects


class FolderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prefix: str = Field(description="the folder's key, ending in /")


class ObjectsDelete(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keys: list[str] = Field(default_factory=list, max_length=S3.FOLDER_DELETE_LIMIT)
    prefixes: list[str] = Field(
        default_factory=list, max_length=100, description="folders to delete everything under"
    )


class PresignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: Literal["get", "put", "multipart", "parts", "complete", "abort"]
    key: str
    disposition: Literal["inline", "attachment"] = "attachment"
    link: bool = Field(False, description="a GET URL valid for an hour, to share")
    content_type: str | None = Field(
        None, max_length=255, description="the upload's type, or the type a preview is shown as"
    )
    size: int | None = Field(None, ge=0, description="the upload's size in bytes")
    upload_id: str | None = Field(None, min_length=1, max_length=1024)
    parts: list[Annotated[int, Field(ge=1, le=10_000)]] = Field(
        default_factory=list, max_length=100
    )


@router.get("/orgs/{org}/buckets/{name}/objects")
async def list_objects(
    org: OrgDep,
    name: str,
    store: StoreDep,
    prefix: str = "",
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = S3.OBJECT_PAGE,
) -> Response:
    """One level of the bucket under ``prefix``: its folders (keys up to their next ``/``) and
    objects, ``limit`` entries a page. ``cursor`` is the next page's, null on the last."""
    store.s3.require()
    return JSONResponse(await store.s3.list_objects(org, name, prefix, cursor, limit))


@router.get("/orgs/{org}/buckets/{name}/object")
async def head_object(org: OrgDep, name: str, key: str, store: StoreDep) -> Response:
    """An object's size, last modification, ETag, content type and user metadata."""
    store.s3.require()
    return JSONResponse(await store.s3.head_object(org, name, key))


@router.post("/orgs/{org}/buckets/{name}/objects/delete")
async def delete_objects(org: OrgDep, name: str, body: ObjectsDelete, store: StoreDep) -> Response:
    """Delete objects (``keys``) and everything under folders (``prefixes``); any member can.
    More than 10,000 objects in all is refused with 409, naming the CLI command to use."""
    store.s3.require()
    try:
        deleted = await store.s3.delete_objects(org, name, body.keys, body.prefixes)
    except TooMany as e:
        raise APIError(409, e.message, limit=e.limit, command=e.command) from None
    return JSONResponse({"deleted": deleted})


@router.post("/orgs/{org}/buckets/{name}/folders", status_code=201)
async def create_folder(org: OrgDep, name: str, body: FolderCreate, store: StoreDep) -> Response:
    """An empty folder: the empty object ``prefix`` (ending in ``/``), as S3 consoles make."""
    store.s3.require()
    await store.s3.create_folder(org, name, body.prefix)
    return JSONResponse({"prefix": body.prefix}, status_code=201)


@router.post("/orgs/{org}/buckets/{name}/presign")
async def presign(
    org: OrgDep, name: str, body: PresignRequest, request: Request, user: UserDep, store: StoreDep
) -> Response:
    """Presigned URLs to the S3 proxy, so the dashboard moves object bytes directly.

    They're signed with the caller's browser key: an ephemeral key of the session and org,
    reused while it has 15 minutes left. ``get`` and ``put`` return a ``url`` lasting 15
    minutes (an hour for a ``get`` with ``link``). ``multipart`` starts an upload of ``size``
    bytes and returns its ``upload_id``, ``part_size`` and ``part_count``; ``parts`` signs up to
    100 of its parts; ``complete`` signs its completion; ``abort`` cancels it.
    """
    store.s3.require()
    if body.method in ("parts", "complete", "abort") and not body.upload_id:
        raise APIError(422, f"{body.method} needs the upload_id")
    out = await store.s3.presign(user, org, name, body.model_dump(), request.state.session.pk, None)
    return JSONResponse(out)
