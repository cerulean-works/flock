"""Evals launched on Modal from the web app (026): what may be launched, start, list, follow
and cancel. Members and admins of the org may launch; anyone else gets the org's 404."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from app.api.src.api.deps import APIError, JSONResponse, Name, OrgDep, StoreDep, UserDep, rest_user
from app.api.src.api.hf_imports import Repo, Revision
from app.api.src.core.eval_launches import EvalLaunch
from app.api.src.core.hf_imports import HubError
from app.api.src.core.store import Store

router = APIRouter(
    prefix="/api/v1/orgs/{org}/evals/launches",
    tags=["evals"],
    dependencies=[Depends(rest_user)],
)

RegistryName = Annotated[str, Field(min_length=1, max_length=128)]


class LaunchBody(BaseModel):
    """An eval to launch: a public Hugging Face model, scored on public suites and tasks in BF16
    at the bank's settings, logged to ``project``."""

    model_config = ConfigDict(extra="forbid")

    model: Repo
    revision: Revision = "main"
    suites: list[RegistryName] = Field(default_factory=list, max_length=100)
    tasks: list[RegistryName] = Field(default_factory=list, max_length=1000)
    add_bos: bool = Field(False, description="start each request with BOS, as Gemma needs")
    project: Name


async def with_users(store: Store, jobs: list[EvalLaunch]) -> list[dict[str, Any]]:
    """Each launch's info, with who started it as the user."""
    briefs = await store.accounts.briefs(j.started_by for j in jobs if j.started_by is not None)
    out = []
    for job in jobs:
        info = job.info()
        info["started_by"] = briefs.get(job.started_by) if job.started_by is not None else None
        out.append(info)
    return out


@router.get("/catalogue")
async def catalogue(org: OrgDep, store: StoreDep) -> Response:
    """The suites and tasks a launch may run (every dataset of each is public), and the caps in
    US dollars. 404 when this server doesn't launch evals."""
    launches = store.eval_launches
    s = store.settings
    return JSONResponse(
        {
            **await launches.catalogue(),
            "max_usd": s.eval_launch_max_usd,
            "monthly_usd": s.eval_launch_monthly_usd,
        }
    )


@router.get("")
async def list_launches(org: OrgDep, store: StoreDep) -> Response:
    """The org's launches of the last month, newest first."""
    jobs = await store.eval_launches.recent(org)
    return JSONResponse({"launches": await with_users(store, jobs)})


@router.post("", status_code=202)
async def launch(org: OrgDep, body: LaunchBody, user: UserDep, store: StoreDep) -> Response:
    """Launch an eval on a Modal H100, in the background.

    The model is resolved to a commit and must be public on the Hub; each suite and task must
    read only public datasets (422 otherwise). It is scored as the eval bank is (BF16, 16384
    batch tokens) and logged as run ``run`` of ``project``. 409 when this month's launches leave
    no room under the monthly cap. Follow it with ``GET {id}``.
    """
    if not body.suites and not body.tasks:
        raise APIError(422, "give at least one suite or task")
    if len(set(body.suites)) != len(body.suites) or len(set(body.tasks)) != len(body.tasks):
        raise APIError(422, "a suite or task is given twice")
    try:
        job = await store.eval_launches.start(
            org,
            user,
            body.project,
            body.model,
            body.revision,
            body.suites,
            body.tasks,
            body.add_bos,
        )
    except HubError as e:
        raise APIError(e.status, e.message) from None
    return JSONResponse((await with_users(store, [job]))[0], status_code=202)


@router.get("/{pk}")
async def get_launch(org: OrgDep, pk: int, store: StoreDep) -> Response:
    """A launch's state: ``running``, ``finished`` (``flock_commit`` scored it), ``failed``
    (with ``error``) or ``cancelled``; ``run`` is its run in ``project``."""
    job = await store.eval_launches.get(org, pk)
    return JSONResponse((await with_users(store, [job]))[0])


@router.delete("/{pk}")
async def cancel_launch(org: OrgDep, pk: int, user: UserDep, store: StoreDep) -> Response:
    """Cancel a running launch (who started it, or an admin). Its GPU stops at once."""
    job = await store.eval_launches.cancel(org, pk, user)
    return JSONResponse((await with_users(store, [job]))[0])
