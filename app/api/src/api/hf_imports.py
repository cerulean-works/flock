"""Hugging Face imports from the web app (D124): start, list, follow and cancel them, and the
Hub metadata the import dialogs show. Members and admins of the org may import."""

import re
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import Response
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from app.api.src.api.deps import APIError, JSONResponse, OrgDep, StoreDep, UserDep, rest_user
from app.api.src.core.hf_imports import HFImport, HubError, TooLarge
from app.api.src.core.store import Store

router = APIRouter(
    prefix="/api/v1/orgs/{org}/imports/hf",
    tags=["imports"],
    dependencies=[Depends(rest_user)],
)

REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


def check_repo(value: str) -> str:
    """A Hub repository id, ``owner/name``."""
    value = value.strip()
    if not REPO.fullmatch(value) or any(part in (".", "..") for part in value.split("/")):
        raise ValueError("must be a Hugging Face repository, such as Qwen/Qwen3-0.6B")
    return value


Repo = Annotated[str, Field(max_length=200), AfterValidator(check_repo)]
Revision = Annotated[str, Field(min_length=1, max_length=255)]
# The access token, for gated and private repositories: used by the import only, never stored.
TokenHeader = Annotated[str | None, Header(alias="x-hf-token", max_length=512)]


class HFImportBody(BaseModel):
    """An import to start. A dataset needs ``dest``, a model ``name``."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["dataset", "model"]
    repo: Repo
    revision: Revision = "main"
    token: Annotated[str, Field(min_length=1, max_length=512)] | None = Field(
        None, description="a Hugging Face access token, used for this import only"
    )
    # A dataset's configs, each with its splits or null for all (default: the only config, or
    # the one called default), and its folder.
    configs: (
        Annotated[
            dict[
                Annotated[str, Field(min_length=1, max_length=256)],
                Annotated[
                    list[Annotated[str, Field(min_length=1, max_length=256)]],
                    Field(min_length=1, max_length=1000),
                ]
                | None,
            ],
            Field(min_length=1, max_length=1000),
        ]
        | None
    ) = None
    dest: Annotated[str, Field(min_length=1, max_length=512)] | None = None
    # A model's name, the new version's tags, and whether to make it primary.
    name: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    tags: list[Annotated[str, Field(max_length=100)]] = Field(default_factory=list, max_length=100)
    primary: bool = False

    @model_validator(mode="after")
    def check_kind(self) -> HFImportBody:
        if self.kind == "dataset" and not self.dest:
            raise ValueError("a dataset import needs dest, the folder to write to")
        if self.kind == "model" and not self.name:
            raise ValueError("a model import needs name, the model to add a version to")
        return self


async def with_users(store: Store, jobs: list[HFImport]) -> list[dict[str, Any]]:
    """Each import's info, with who started it as the user."""
    briefs = await store.accounts.briefs(j.started_by for j in jobs if j.started_by is not None)
    out = []
    for job in jobs:
        info = job.info()
        info["started_by"] = briefs.get(job.started_by) if job.started_by is not None else None
        out.append(info)
    return out


def refused(error: HubError | TooLarge) -> APIError:
    if isinstance(error, TooLarge):
        return APIError(409, error.message, command=error.command, size=error.size, too_large=True)
    return APIError(error.status, error.message)


@router.get("/preview")
async def preview(
    org: OrgDep,
    store: StoreDep,
    kind: Literal["dataset", "model"],
    repo: Annotated[Repo, Query()],
    revision: Annotated[Revision, Query()] = "main",
    token: TokenHeader = None,
) -> Response:
    """What an import dialog shows of a repository, read from the Hub with the token in the
    ``X-HF-Token`` header (if any). A dataset's configs with their splits and sizes, from the
    Hub's dataset viewer. A model's commit, size, ``model_type``, and ``refusal`` when it can't be
    imported. Both say ``max_bytes``, the largest import this server runs."""
    hf = store.hf_imports
    try:
        if kind == "model":
            return JSONResponse(await hf.preview_model(org, repo, revision, token))
        return JSONResponse(await hf.preview_dataset(org, repo, revision, token))
    except HubError as e:
        raise refused(e) from None


@router.get("")
async def list_imports(org: OrgDep, store: StoreDep) -> Response:
    """The org's imports of the last week, newest first."""
    jobs = await store.hf_imports.recent(org)
    return JSONResponse({"imports": await with_users(store, jobs)})


@router.post("", status_code=202)
async def start_import(org: OrgDep, body: HFImportBody, user: UserDep, store: StoreDep) -> Response:
    """Start importing a Hugging Face dataset or model, in the background.

    A dataset becomes one Lance dataset per split of each config in ``configs`` in the org's
    flock-datasets, under ``dest`` (D121, D129). A model becomes a new version of ``name`` in its flock-models (D123). The Hub is
    asked first: a missing, gated or unsupported repository is refused now, and one larger than
    the server's limit answers 409 with ``too_large`` and the ``command`` to run instead. The
    token is used by this import only, and never stored. Follow it with ``GET {id}``.
    """
    s3 = store.s3
    s3.require()
    hf = store.hf_imports
    try:
        if body.kind == "model":
            assert body.name is not None
            job = await hf.start_model(
                org,
                user,
                body.repo,
                body.revision,
                body.name,
                body.tags,
                body.primary,
                body.token,
            )
        else:
            assert body.dest is not None
            job = await hf.start_dataset(
                org,
                user,
                body.repo,
                body.revision,
                body.configs,
                body.dest,
                body.token,
            )
    except (HubError, TooLarge) as e:
        raise refused(e) from None
    return JSONResponse((await with_users(store, [job]))[0], status_code=202)


@router.get("/{pk}")
async def get_import(org: OrgDep, pk: int, store: StoreDep) -> Response:
    """An import's state and progress: ``progress`` from 0 to 1, ``result`` once it finished
    (each split's dataset with its config, or the model's version), ``error`` once it failed."""
    job = await store.hf_imports.get(org, pk)
    return JSONResponse((await with_users(store, [job]))[0])


@router.delete("/{pk}")
async def cancel_import(org: OrgDep, pk: int, user: UserDep, store: StoreDep) -> Response:
    """Cancel a running import (who started it, or an admin). Nothing it wrote stays: a dataset's
    splits are committed only at the end, and a model's upload is aborted."""
    job = await store.hf_imports.cancel(org, pk, user)
    return JSONResponse((await with_users(store, [job]))[0])
