from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from app.api.src.api.datasets import hub_writable
from app.api.src.api.deps import (
    APIError,
    JSONResponse,
    StoreDep,
    UserDep,
    current_user,
    hub_enabled,
    visible_org,
)
from app.api.src.core.mirrors import TooLarge, UpstreamError

router = APIRouter(
    prefix="/api/v1", tags=["hub"], dependencies=[Depends(current_user), Depends(hub_enabled)]
)


class HubKeyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=128)]
    read_only: bool = False
    org: str | None = None  # limit the key to this org's datasets


class CredentialsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    read_only: bool = False


# Hugging Face repo ids (`owner/name`, or a legacy bare `name`) and config names. Both end up
# in the mirror's dataset name, so neither may hold characters dataset names refuse.
HfRepo = Annotated[str, Field(pattern=r"^[A-Za-z0-9][\w.-]{0,95}(/[A-Za-z0-9][\w.-]{0,95})?$")]
HfConfig = Annotated[str, Field(pattern=r"^[\w.-]{1,64}$")]


class MirrorBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo: HfRepo
    config: HfConfig | None = None
    revision: Annotated[str, Field(min_length=1, max_length=256)] | None = None


@router.get("/me/hub-keys")
async def list_hub_keys(user: UserDep, store: StoreDep) -> Response:
    keys = await store.hub_keys.keys(user)
    return JSONResponse({"keys": [store.hub_keys.info(k) for k in keys]})


@router.post("/me/hub-keys", status_code=201)
async def create_hub_key(body: HubKeyCreate, user: UserDep, store: StoreDep) -> Response:
    """A new S3 key for tools that speak S3 (boto3, DuckDB, Lance). The secret is shown only
    once."""
    org = visible_org(body.org, store, user) if body.org is not None else None
    key, secret = await store.hub_keys.create(user, body.name, body.read_only, org)
    return JSONResponse(
        {
            **store.hub_keys.info(key),
            "secret_access_key": secret,
            "endpoint": store.hub_keys.endpoint,
            "region": store.settings.hub_s3_region,
        },
        status_code=201,
    )


@router.delete("/me/hub-keys/{pk}", status_code=204)
async def delete_hub_key(pk: int, user: UserDep, store: StoreDep) -> Response:
    """Revoke an S3 key at once."""
    await store.hub_keys.delete(user, pk)
    return Response(status_code=204)


@router.post("/hub/credentials")
async def create_credentials(
    user: UserDep, store: StoreDep, body: CredentialsBody | None = None
) -> Response:
    """Temporary S3 credentials (``FLOCK_HUB_CREDENTIALS_TTL``), as ``flock.hub`` asks for
    with an API key. They act with the user's current roles, checked on every request."""
    read_only = body.read_only if body is not None else False
    return JSONResponse(store.hub_keys.temporary(user, read_only=read_only))


@router.get("/hub/mirrors")
async def list_mirrors(
    store: StoreDep,
    repo: Annotated[str, Query(min_length=1)],
    config: Annotated[str | None, Query()] = None,
) -> Response:
    """The mirror dataset of a Hugging Face repo (and config), and its imports, newest first.
    ``flock.hub`` asks this before requesting an import."""
    mirrors = store.mirrors
    return JSONResponse(
        {
            "dataset": f"{mirrors.org_name}/{mirrors.dataset_name(repo, config or None)}",
            "mirrors": [mirrors.info(m) for m in await mirrors.of_repo(repo, config or None)],
        }
    )


@router.post("/hub/mirrors", dependencies=[Depends(hub_writable)])
async def request_mirror(body: MirrorBody, user: UserDep, store: StoreDep) -> Response:
    """Mirror a Hugging Face dataset at a revision (D50): 200 with a finished mirror, 202
    with a running one (new or shared), 422 with a ``command`` when it is too large."""
    try:
        mirror = await store.mirrors.request(user, body.repo, body.config, body.revision)
    except TooLarge as e:
        raise APIError(422, e.message, command=e.command) from e
    except UpstreamError as e:
        raise APIError(502, str(e)) from e
    except ValueError as e:  # an unknown or missing config
        raise APIError(400, str(e)) from e
    status = 200 if mirror.state == "finished" else 202
    return JSONResponse(store.mirrors.info(mirror), status_code=status)


@router.get("/hub/mirrors/{pk}")
async def get_mirror(pk: int, store: StoreDep) -> Response:
    """A mirror's state; ``progress`` holds the importer's last stage while it runs."""
    return JSONResponse(store.mirrors.info(await store.mirrors.get(pk)))
