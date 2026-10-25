from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from app.api.src.api.deps import (
    JSONResponse,
    StoreDep,
    UserDep,
    current_user,
    hub_enabled,
    visible_org,
)

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
