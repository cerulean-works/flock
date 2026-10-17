import base64
from typing import Annotated, Any

import orjson
from fastapi import Depends, Request
from fastapi.responses import Response
from pydantic import AfterValidator

from src.core.store import Project, Run, Store


class JSONResponse(Response):
    """orjson-encoded response that serializes numpy arrays natively (NaN/inf become null).

    Endpoints return these directly so FastAPI skips its own (much slower) serialization.
    """

    media_type = "application/json"

    def render(self, content: Any) -> bytes:
        return orjson.dumps(content, option=orjson.OPT_SERIALIZE_NUMPY)


class APIError(Exception):
    def __init__(self, status_code: int, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.extra = extra  # more fields for the error body, e.g. the ids of conflicting runs


def check_name(value: str) -> str:
    """wandb's rule for project names: 1-128 characters, none of ``/ \\ # ? % :``."""
    if not 1 <= len(value) <= 128 or value != value.strip():
        raise ValueError("must be 1-128 characters without surrounding whitespace")
    if any(ch in "/\\#?%:" or not ch.isprintable() for ch in value):
        raise ValueError("must not contain / \\ # ? % : or control characters")
    return value


# A project or entity name given through the API (names sent by the SDK are taken as they are).
Name = Annotated[str, AfterValidator(check_name)]


def get_store(request: Request) -> Store:
    return request.app.state.store


StoreDep = Annotated[Store, Depends(get_store)]


def current_user(request: Request, store: StoreDep) -> str:
    """Username for the request's API key; any key is accepted when none are configured."""
    keys = store.settings.api_keys
    if not keys:
        return store.settings.default_entity
    key = request.headers.get("x-api-key")
    scheme, _, credentials = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() == "basic":  # what the wandb SDK sends: base64("api:<key>")
        try:
            key = base64.b64decode(credentials).decode().partition(":")[2]
        except ValueError:
            key = None
    elif scheme.lower() == "bearer":
        key = credentials
    user = keys.get(key) if key else None
    if user is None:
        raise APIError(401, "invalid API key")
    return user


UserDep = Annotated[str, Depends(current_user)]


def get_project(entity: str, project: str, store: StoreDep) -> Project:
    found = store.project(entity, project)
    if found is None:
        raise APIError(404, f"project {entity}/{project} not found")
    return found


ProjectDep = Annotated[Project, Depends(get_project)]


def get_run(run: str, project: ProjectDep) -> Run:
    found = project.by_name.get(run)
    if found is None:
        raise APIError(404, f"run {run} not found")
    return found


RunDep = Annotated[Run, Depends(get_run)]
