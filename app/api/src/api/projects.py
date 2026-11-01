from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict

from app.api.src.api.deps import (
    AdminProjectDep,
    APIError,
    JSONResponse,
    Name,
    ProjectDep,
    StoreDep,
    UserDep,
    rest_user,
    visible_org,
)
from app.api.src.core.errors import NotFound

router = APIRouter(prefix="/api/v1", tags=["projects"], dependencies=[Depends(rest_user)])


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


@router.get("/projects")
async def list_projects(
    store: StoreDep,
    user: UserDep,
    entity: Annotated[str | None, Query(description="only this entity")] = None,
) -> Response:
    """Projects of the user's organizations with their run counts, including empty ones."""
    projects = [
        p
        for p in await store.projects()
        if (entity is None or p.entity == entity) and store.accounts.role(user, p.org)
    ]
    return JSONResponse({"projects": await store.project_infos(projects)})


@router.post("/projects", status_code=201)
async def create_project(body: ProjectCreate, store: StoreDep, user: UserDep) -> Response:
    """Create an empty project in an org of the user (``wandb.init`` also creates projects)."""
    await visible_org(body.entity, store, user)
    project = await store.create_project(body.entity, body.name, body.description)
    return JSONResponse(await store.project_info(project), status_code=201)


@router.get("/projects/{entity}/{project}")
async def get_project(project: ProjectDep, store: StoreDep) -> Response:
    return JSONResponse(await store.project_info(project))


@router.patch("/projects/{entity}/{project}")
async def update_project(
    project: AdminProjectDep, body: ProjectUpdate, store: StoreDep
) -> Response:
    """Rename a project or change its description (org admins only).

    Renaming is refused (409) while the project has active runs, since their clients address
    them by the old name. Dashboards streaming the project get a ``project`` event.
    """
    fields = body.model_dump(include=body.model_fields_set)
    try:
        project = await store.update_project(project, fields)
    except NotFound as exc:
        raise APIError(404, str(exc)) from exc
    return JSONResponse(await store.project_info(project))


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
