from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict

from src.api.deps import APIError, JSONResponse, Name, ProjectDep, StoreDep, current_user
from src.core.store import NotFound

router = APIRouter(prefix="/api/v1", tags=["projects"], dependencies=[Depends(current_user)])


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
    store: StoreDep, entity: Annotated[str | None, Query(description="only this entity")] = None
) -> Response:
    """Projects with their run counts, including projects without runs."""
    projects = [p for p in store.projects.values() if entity is None or p.entity == entity]
    return JSONResponse({"projects": [p.info() for p in projects]})


@router.post("/projects", status_code=201)
async def create_project(body: ProjectCreate, store: StoreDep) -> Response:
    """Create an empty project (``wandb.init`` also creates projects it logs to)."""
    project = await store.create_project(body.entity, body.name, body.description)
    return JSONResponse(project.info(), status_code=201)


@router.get("/projects/{entity}/{project}")
async def get_project(project: ProjectDep) -> Response:
    return JSONResponse(project.info())


@router.patch("/projects/{entity}/{project}")
async def update_project(project: ProjectDep, body: ProjectUpdate, store: StoreDep) -> Response:
    """Rename a project or change its description.

    Renaming is refused (409) while the project has active runs, since their clients address
    them by the old name. Dashboards streaming the project get a ``project`` event.
    """
    fields = body.model_dump(include=body.model_fields_set)
    try:
        await store.update_project(project, fields)
    except NotFound as exc:
        raise APIError(404, str(exc)) from exc
    return JSONResponse(project.info())


@router.delete("/projects/{entity}/{project}", status_code=204)
async def delete_project(
    project: ProjectDep,
    store: StoreDep,
    force: Annotated[bool, Query(description="also delete active runs")] = False,
) -> Response:
    """Delete a project with all its runs, their history, logs and files. There is no undo."""
    try:
        await store.delete_project(project, force)
    except NotFound as exc:
        raise APIError(404, str(exc)) from exc
    return Response(status_code=204)
