from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response
from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from app.api.src.api.deps import (
    AdminOrgDep,
    APIError,
    Email,
    JSONResponse,
    Name,
    OrgDep,
    OrgDisplayName,
    StoreDep,
    SuperadminDep,
    UserDep,
    app_link,
)
from app.api.src.core.accounts import Invite, User
from app.api.src.core.store import Org, Store
from app.api.src.core.wandb_source import WandbAuthError, WandbError, WandbNotFound, WandbSource

router = APIRouter(prefix="/api/v1", tags=["organizations"])

Role = Literal["admin", "member"]


def check_base_url(value: str) -> str:
    value = value.strip().rstrip("/")
    if not value.startswith(("https://", "http://")) or " " in value:
        raise ValueError("must be an http(s) URL")
    return value


class WandbImport(BaseModel):
    """A W&B entity to copy into an organization. The API key is used for this import only,
    and never stored."""

    model_config = ConfigDict(extra="forbid")

    entity: Annotated[str, Field(min_length=1, max_length=128)]
    api_key: Annotated[str, Field(min_length=1, max_length=256)]
    base_url: Annotated[str, AfterValidator(check_base_url)] | None = None


class OrgCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name  # the ID, fixed from now on
    display_name: OrgDisplayName | None = None  # defaults to the ID
    wandb: WandbImport | None = None


class OrgUpdate(BaseModel):
    """Fields to change. ``name`` (the ID) can't change: it is accepted only as it is, so a
    client can send back the organization it read."""

    model_config = ConfigDict(extra="forbid")

    display_name: OrgDisplayName | None = None
    name: str | None = None
    # S3 key lifetimes in seconds (D56); a null maximum means keys may never expire.
    s3_ephemeral_ttl: int | None = None
    s3_key_max_ttl: int | None = None


class RoleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Role


class InviteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: Email
    role: Role = "member"


def org_info(store: Store, org: Org, user: User) -> dict:
    return {
        "name": org.name,
        "display_name": org.display_name,
        "created_at": org.created_at,
        "updated_at": org.updated_at,
        "project_count": sum(1 for p in store.projects_by_pk.values() if p.org is org),
        "member_count": len(store.accounts.members(org)),
        "role": store.accounts.role(user, org),  # the caller's
        "avatar_url": store.avatars.url(org.avatar),
        "s3_ephemeral_ttl": org.s3_ephemeral_ttl,
        "s3_key_max_ttl": org.s3_key_max_ttl,
    }


def invite_info(invite: Invite) -> dict:
    return {
        "id": invite.pk,
        "email": invite.email,
        "role": invite.role,
        "created_by": invite.created_by,
        "created_at": invite.created_at,
        "expires_at": invite.expires_at,
    }


def find_member(store: Store, org: Org, user_id: int) -> User:
    user = store.accounts.users.get(user_id)
    if user is None or org.pk not in user.roles:
        raise APIError(404, f"user {user_id} is not a member of {org.name}")
    return user


# ---------------------------------------------------------------------- organizations


@router.get("/orgs")
async def list_orgs(user: UserDep, store: StoreDep) -> Response:
    """The user's organizations (every organization, for super admins)."""
    orgs = [org for org in store.orgs.values() if store.accounts.role(user, org)]
    orgs.sort(key=lambda org: (org.display_name.casefold(), org.name))
    return JSONResponse({"orgs": [org_info(store, org, user) for org in orgs]})


async def wandb_source(store: Store, body: WandbImport) -> tuple[WandbSource, dict[str, str]]:
    """A W&B source for ``body``, checked to work: a bad key or entity is refused now, before
    anything is created."""
    source = WandbSource(body.base_url or store.settings.wandb_base_url, body.api_key)
    try:
        return source, await source.check(body.entity)
    except BaseException as exc:
        await source.close()
        if isinstance(exc, WandbAuthError):
            raise APIError(400, "W&B rejected the API key") from exc
        if isinstance(exc, WandbNotFound):
            raise APIError(404, f"W&B entity {body.entity} not found") from exc
        if isinstance(exc, WandbError):
            raise APIError(502, str(exc)) from exc
        raise


@router.post("/orgs", status_code=201)
async def create_org(body: OrgCreate, user: SuperadminDep, store: StoreDep) -> Response:
    """Create an organization (super admins only), with its creator as an admin.

    With ``wandb``, the projects and runs of that W&B entity are then copied into it in the
    background; the response's ``import`` is its progress (see ``GET /orgs/{org}/import``).
    """
    source = members = None
    if body.wandb is not None:
        source, members = await wandb_source(store, body.wandb)
    try:
        org = await store.create_org(body.name, body.display_name)
        await store.accounts.add_member(user, org, "admin")
    except BaseException:
        if source is not None:
            await source.close()
        raise
    info = org_info(store, org, user)
    if source is not None and body.wandb is not None and members is not None:
        job = await store.imports.start(org, source, body.wandb.entity, user, members)
        info["import"] = job.info()
    return JSONResponse(info, status_code=201)


@router.get("/orgs/{org}")
async def get_org(org: OrgDep, user: UserDep, store: StoreDep) -> Response:
    return JSONResponse(org_info(store, org, user))


@router.patch("/orgs/{org}")
async def update_org(org: AdminOrgDep, body: OrgUpdate, user: UserDep, store: StoreDep) -> Response:
    """Change an organization's display name, or its S3 key lifetimes.

    Its ID (``name``) is fixed: projects, runs, URLs and wandb clients address the organization
    by it. A different ``name`` answers 400.

    ``s3_ephemeral_ttl`` is how long keys exchanged for a CLI token or API key live, and
    ``s3_key_max_ttl`` the longest any key may live (null: no limit), both in seconds. The
    ephemeral lifetime can't exceed the maximum (422); lowering only the maximum below it
    lowers it too. A lower maximum applies to existing keys at once.
    """
    if body.name is not None and body.name != org.name:
        raise APIError(400, "an organization's ID (name) can't be changed")
    ttls = body.model_dump(include={"s3_ephemeral_ttl", "s3_key_max_ttl"} & body.model_fields_set)
    if ttls.get("s3_ephemeral_ttl", 0) is None:
        raise APIError(422, "s3_ephemeral_ttl can't be null")
    if ttls:
        await store.s3.set_ttls(org, ttls)
    if body.display_name is not None:
        await store.set_org_display_name(org, body.display_name)
    return JSONResponse(org_info(store, org, user))


@router.delete("/orgs/{org}", status_code=204)
async def delete_org(
    org: AdminOrgDep,
    store: StoreDep,
    force: Annotated[bool, Query(description="also delete active runs")] = False,
) -> Response:
    """Delete an organization with all its projects and runs. There is no undo."""
    await store.delete_org(org, force)
    return Response(status_code=204)


# ---------------------------------------------------------------------- imports


@router.get("/orgs/{org}/import")
async def get_import(org: AdminOrgDep, store: StoreDep) -> Response:
    """The progress of the organization's latest W&B import (404 if it never had one)."""
    job = store.imports.get(org)
    if job is None:
        raise APIError(404, f"{org.name} has no import")
    return JSONResponse(job.info())


@router.post("/orgs/{org}/import", status_code=202)
async def start_import(
    org: AdminOrgDep, body: WandbImport, user: SuperadminDep, store: StoreDep
) -> Response:
    """Copy a W&B entity's projects and runs into the organization (super admins only).

    Runs it already has (by project and run id) are skipped, so this also resumes an import
    that was interrupted, failed or cancelled. 409 while an import is running.
    """
    current = store.imports.get(org)
    if current is not None and current.state == "running":
        raise APIError(409, f"an import into {org.name} is already running")
    source, members = await wandb_source(store, body)
    job = await store.imports.start(org, source, body.entity, user, members)
    return JSONResponse(job.info(), status_code=202)


@router.delete("/orgs/{org}/import")
async def cancel_import(org: AdminOrgDep, user: SuperadminDep, store: StoreDep) -> Response:
    """Stop a running import (super admins only); the runs copied so far stay."""
    job = await store.imports.cancel(org)
    if job is None:
        raise APIError(404, f"{org.name} has no import")
    return JSONResponse(job.info())


# ---------------------------------------------------------------------- members


@router.get("/orgs/{org}/members")
async def list_members(org: OrgDep, store: StoreDep) -> Response:
    members = sorted(store.accounts.members(org), key=lambda member: member[0].email)
    return JSONResponse(
        {"members": [{**store.accounts.brief(u), "role": role} for u, role in members]}
    )


@router.patch("/orgs/{org}/members/{user_id}")
async def set_role(org: AdminOrgDep, user_id: int, body: RoleBody, store: StoreDep) -> Response:
    """Change a member's role; an organization always keeps an admin."""
    await store.accounts.set_role(find_member(store, org, user_id), org, body.role)
    return JSONResponse({"id": user_id, "role": body.role})


@router.delete("/orgs/{org}/members/{user_id}", status_code=204)
async def remove_member(org: OrgDep, user_id: int, user: UserDep, store: StoreDep) -> Response:
    """Remove a member (admins), or leave the organization (``user_id`` is your own id).
    Their runs stay."""
    if user_id != user.pk and store.accounts.role(user, org) != "admin":
        raise APIError(403, f"only admins of {org.name} can remove members")
    await store.accounts.remove_member(find_member(store, org, user_id), org)
    return Response(status_code=204)


# ---------------------------------------------------------------------- invites


@router.get("/orgs/{org}/invites")
async def list_invites(org: AdminOrgDep, store: StoreDep) -> Response:
    """Invitations not yet accepted or expired."""
    return JSONResponse({"invites": [invite_info(i) for i in await store.accounts.invites(org)]})


@router.post("/orgs/{org}/invites", status_code=201)
async def create_invite(
    org: AdminOrgDep, body: InviteCreate, request: Request, user: UserDep, store: StoreDep
) -> Response:
    """Invite someone by email. No email is sent: pass the returned ``url`` on to them. It is
    shown only now, and works once."""
    invite, token = await store.accounts.create_invite(org, body.email, body.role, user)
    url = app_link(request, store, f"invite/{token}")
    return JSONResponse({**invite_info(invite), "url": url, "token": token}, status_code=201)


@router.delete("/orgs/{org}/invites/{invite_id}", status_code=204)
async def delete_invite(org: AdminOrgDep, invite_id: int, store: StoreDep) -> Response:
    await store.accounts.delete_invite(org, invite_id)
    return Response(status_code=204)
