from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict

from app.api.src.api.deps import (
    AdminProjectDep,
    APIError,
    JSONResponse,
    MemberProjectDep,
    Name,
    ReadableProjectDep,
    StoreDep,
    UserDep,
    ViewerDep,
    rest_user,
    visible_org,
)
from app.api.src.core.errors import NotFound

router = APIRouter(prefix="/api/v1", tags=["projects"], dependencies=[Depends(rest_user)])
# Routes that answer outsiders too, on public projects (D112): they need no credential.
public = APIRouter(prefix="/api/v1", tags=["projects"])


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity: Name
    name: Name
    description: str | None = None


class ProjectUpdate(BaseModel):
    """Fields to change; ``description: null`` clears the description."""

    model_config = ConfigDict(extra="forbid")

    name: Name | None = None
    description: str | None = None
    visibility: Literal["private", "public"] | None = None


@public.get("/projects")
async def list_projects(
    store: StoreDep,
    viewer: ViewerDep,
    entity: Annotated[str | None, Query(description="only this entity")] = None,
) -> Response:
    """Projects of the user's organizations with their run counts, including empty ones.

    With ``entity``, that organization's projects: its public ones to outsiders (D114).
    Without it, there's no way to list public projects across organizations."""
    accounts = store.accounts
    if entity is None:
        # Anonymous viewers belong to no org: nothing to read every project for.
        found = await store.projects() if viewer is not None else []
        projects = [p for p in found if accounts.role(viewer, p.org)]
    else:
        org = await store.find_org(entity)
        found = await store.projects([org]) if org is not None else []
        projects = [p for p in found if accounts.access(viewer, p)]
    return JSONResponse({"projects": await store.project_infos(projects)})


@router.post("/projects", status_code=201)
async def create_project(body: ProjectCreate, store: StoreDep, user: UserDep) -> Response:
    """Create an empty project in an org of the user (``wandb.init`` also creates projects)."""
    await visible_org(body.entity, store, user)
    project = await store.create_project(body.entity, body.name, body.description, creator=user.pk)
    return JSONResponse(await store.project_info(project), status_code=201)


@public.get("/projects/{entity}/{project}")
async def get_project(project: ReadableProjectDep, store: StoreDep, viewer: ViewerDep) -> Response:
    """The project, with the caller's ``access``: ``admin``, ``member`` or ``public`` (read
    only, D111)."""
    access = store.accounts.access(viewer, project)
    return JSONResponse({**await store.project_info(project), "access": access})


@public.get("/projects/{entity}/{project}/owners")
async def list_owners(project: ReadableProjectDep, store: StoreDep, viewer: ViewerDep) -> Response:
    """The users who logged the project's runs, as the runs' ``owner`` names them by id.
    Outsiders get them without email (D113)."""
    pks = await store.db.fetch(
        "SELECT DISTINCT owner_pk FROM runs WHERE project_pk = $1 AND deleted_at IS NULL "
        "AND owner_pk IS NOT NULL",
        project.pk,
    )
    public_only = store.accounts.access(viewer, project) == "public"
    briefs = await store.accounts.briefs((pk for (pk,) in pks), public_only)
    return JSONResponse({"owners": sorted(briefs.values(), key=lambda b: b["id"])})


@router.patch("/projects/{entity}/{project}")
async def update_project(
    project: MemberProjectDep, body: ProjectUpdate, store: StoreDep, user: UserDep
) -> Response:
    """Rename a project, change its description (org admins only), or its ``visibility`` (org
    admins and the member who created it, D118).

    Renaming is refused (409) while the project has active runs, since their clients address
    them by the old name. Dashboards streaming the project get a ``project`` event.

    A ``public`` project can be read by anyone with its URL, signed in or not (D110). Making
    it private ends outsiders' streams.
    """
    fields = body.model_dump(include=body.model_fields_set)
    if "visibility" in fields and fields["visibility"] is None:
        raise APIError(422, "visibility can't be null")
    admin = store.accounts.role(user, project.org) == "admin"
    if not admin and (fields.keys() - {"visibility"} or project.created_by != user.pk):
        raise APIError(
            403, f"only admins of {project.entity}, and its creator for its visibility, can do this"
        )
    try:
        project = await store.update_project(project, fields, user)
    except NotFound as exc:
        raise APIError(404, str(exc)) from exc
    access = "admin" if admin else "member"
    return JSONResponse({**await store.project_info(project), "access": access})


@router.delete("/projects/{entity}/{project}", status_code=204)
async def delete_project(
    project: AdminProjectDep,
    store: StoreDep,
    force: Annotated[bool, Query(description="also delete active runs")] = False,
) -> Response:
    """Delete a project with all its runs, their history, logs and files (org admins only).
    There is no undo."""
    try:
        await store.delete_project(project, force)
    except NotFound as exc:
        raise APIError(404, str(exc)) from exc
    return Response(status_code=204)
