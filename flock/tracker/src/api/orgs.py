from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict

from src.api.deps import (
    AdminOrgDep,
    APIError,
    Email,
    JSONResponse,
    Name,
    OrgDep,
    StoreDep,
    SuperadminDep,
    UserDep,
    app_link,
)
from src.core.accounts import Invite, User
from src.core.store import Org, Store

router = APIRouter(prefix="/api/v1", tags=["organizations"])

Role = Literal["admin", "member"]


class OrgBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name


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
        "created_at": org.created_at,
        "updated_at": org.updated_at,
        "project_count": sum(1 for p in store.projects_by_pk.values() if p.org is org),
        "member_count": len(store.accounts.members(org)),
        "role": store.accounts.role(user, org),  # the caller's
        "avatar_url": store.avatars.url(org.avatar),
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
    return JSONResponse(
        {"orgs": [org_info(store, org, user) for org in sorted(orgs, key=lambda o: o.name)]}
    )


@router.post("/orgs", status_code=201)
async def create_org(body: OrgBody, user: SuperadminDep, store: StoreDep) -> Response:
    """Create an organization (super admins only), with its creator as an admin."""
    org = await store.create_org(body.name)
    await store.accounts.add_member(user, org, "admin")
    return JSONResponse(org_info(store, org, user), status_code=201)


@router.get("/orgs/{org}")
async def get_org(org: OrgDep, user: UserDep, store: StoreDep) -> Response:
    return JSONResponse(org_info(store, org, user))


@router.patch("/orgs/{org}")
async def rename_org(org: AdminOrgDep, body: OrgBody, user: UserDep, store: StoreDep) -> Response:
    """Rename an organization, the entity of its projects and runs.

    Refused (409, listing them as project/run) while runs are active, since their clients
    address them by entity. Dashboards streaming its projects get a ``project`` event.
    """
    await store.update_org(org, body.name)
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
