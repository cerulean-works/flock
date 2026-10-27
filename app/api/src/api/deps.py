import base64
from collections.abc import Iterable
from typing import Annotated, Any

import orjson
from fastapi import Depends, Request
from fastapi.responses import Response
from pydantic import AfterValidator, Field

from app.api.src.core.accounts import Session, User
from app.api.src.core.errors import Forbidden
from app.api.src.core.store import Org, Project, Run, Store


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


# A project name or organization ID given through the API (names the SDK sends are taken as is).
Name = Annotated[str, AfterValidator(check_name)]


def check_display_name(value: str) -> str:
    """An organization's display name: any text of 1-128 characters once surrounding
    whitespace is dropped, without control characters."""
    value = value.strip()
    if not 1 <= len(value) <= 128:
        raise ValueError("must be 1-128 characters")
    if not all(ch.isprintable() for ch in value):
        raise ValueError("must not contain control characters")
    return value


OrgDisplayName = Annotated[str, AfterValidator(check_display_name)]


def get_store(request: Request) -> Store:
    return request.app.state.store


StoreDep = Annotated[Store, Depends(get_store)]


def check_email(value: str) -> str:
    """Addresses are only checked for shape (nothing is sent to them) and kept lowercased."""
    value = value.strip().lower()
    local, _, domain = value.partition("@")
    if not local or "." not in domain or any(ch.isspace() for ch in value) or len(value) > 254:
        raise ValueError("must be an email address")
    return value


Email = Annotated[str, AfterValidator(check_email)]
Password = Annotated[str, Field(min_length=8, max_length=256)]


WANDB_KEY_REFUSED = (
    "wandb keys only work with the wandb SDK: use `flock login` or a browser session"
)


def current_user(request: Request, store: StoreDep) -> User:
    """The user behind the request's access token (``Bearer <jwt>``), CLI token (``Bearer
    flock_cli_…``) or wandb key (``Bearer``, ``x-api-key``, or the wandb SDK's basic auth).
    Which kind it was (D70) is kept in ``request.state.credential``: ``"browser"``, ``"cli"``
    or ``"wandb"`` (any ``api_keys`` row). The session of an access token or CLI token is kept
    in ``request.state.session``, a wandb key in ``request.state.api_key``.
    """
    accounts = store.accounts
    key = request.headers.get("x-api-key")
    scheme, _, credentials = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() == "basic":  # what the wandb SDK sends: base64("api:<key>")
        try:
            key = base64.b64decode(credentials).decode().partition(":")[2]
        except ValueError:
            key = None
    elif scheme.lower() == "bearer":
        if credentials.count(".") == 2:  # a JWT; wandb keys have no dots
            session = accounts.session_for_token(credentials)
            if session is None:
                raise APIError(401, "invalid or expired access token")
            request.state.session, request.state.credential = session, "browser"
            return accounts.users[session.user]
        if credentials.startswith(accounts.CLI_TOKEN_PREFIX):
            session = accounts.session_for_cli_token(credentials)
            if session is None:
                raise APIError(401, "invalid or expired CLI token: run flock login again")
            request.state.session, request.state.credential = session, "cli"
            return accounts.users[session.user]
        key = credentials
    api_key = accounts.api_key(key) if key else None
    user = accounts.users.get(api_key.user) if api_key is not None else None
    if user is None:
        raise APIError(401, "invalid or missing access token, CLI token or wandb key")
    request.state.api_key, request.state.credential = api_key, "wandb"
    return user


UserDep = Annotated[User, Depends(current_user)]


def rest_user(request: Request, user: UserDep) -> User:
    """The user of a REST API request: a browser session or CLI token. wandb keys are refused
    (D72), so a leaked one reaches tracking and nothing else. The REST routers depend on it."""
    if request.state.credential == "wandb":
        raise APIError(401, WANDB_KEY_REFUSED)
    return user


RestUser = Annotated[User, Depends(rest_user)]


def wandb_user(user: UserDep) -> User:
    """The user of a wandb protocol request: any credential, wandb keys included. The wandb
    protocol's router depends on it."""
    return user


WandbUser = Annotated[User, Depends(wandb_user)]


def current_session(request: Request, user: UserDep) -> Session | None:
    """The sign-in session of a request made with an access token or CLI token (None for wandb
    keys)."""
    return getattr(request.state, "session", None)


SessionDep = Annotated[Session | None, Depends(current_session)]


def session_user(user: UserDep, session: SessionDep) -> User:
    """The user behind a browser's access token. CLI tokens (and wandb keys) are refused, so a
    leaked one can't approve sign-ins for new machines."""
    if session is None or session.kind != "web":
        raise APIError(403, "only a signed-in browser can do this, not a CLI token")
    return user


SessionUserDep = Annotated[User, Depends(session_user)]


def get_superadmin(user: UserDep) -> User:
    if not user.is_superadmin:
        raise APIError(403, "only a super admin can do this")
    return user


SuperadminDep = Annotated[User, Depends(get_superadmin)]


def app_link(request: Request, store: Store, path: str) -> str:
    """A URL in the dashboard (where invite, reset and sign-in links land)."""
    settings = store.settings
    base = settings.web_url or settings.api_public_url or str(request.base_url)
    return f"{base.rstrip('/')}/{path}"


def visible_org(name: str, store: Store, user: User) -> Org:
    """The org ``name`` if ``user`` belongs to it; others get the same 404 as unknown orgs."""
    org = store.orgs.get(name)
    if org is None or store.accounts.role(user, org) is None:
        raise APIError(404, f"organization {name} not found")
    return org


def get_org(org: str, store: StoreDep, user: UserDep) -> Org:
    return visible_org(org, store, user)


OrgDep = Annotated[Org, Depends(get_org)]


def get_admin_org(org: OrgDep, store: StoreDep, user: UserDep) -> Org:
    if store.accounts.role(user, org) != "admin":
        raise APIError(403, f"only admins of {org.name} can do this")
    return org


AdminOrgDep = Annotated[Org, Depends(get_admin_org)]


def get_project(entity: str, project: str, store: StoreDep, user: UserDep) -> Project:
    found = store.project(entity, project)
    if found is None or store.accounts.role(user, found.org) is None:
        raise APIError(404, f"project {entity}/{project} not found")
    return found


ProjectDep = Annotated[Project, Depends(get_project)]


def get_admin_project(project: ProjectDep, store: StoreDep, user: UserDep) -> Project:
    if store.accounts.role(user, project.org) != "admin":
        raise APIError(403, f"only admins of {project.entity} can do this")
    return project


AdminProjectDep = Annotated[Project, Depends(get_admin_project)]


def get_run(run: str, project: ProjectDep) -> Run:
    found = project.by_name.get(run)
    if found is None:
        raise APIError(404, f"run {run} not found")
    return found


RunDep = Annotated[Run, Depends(get_run)]


def check_owner(store: Store, user: User, project: Project, runs: Iterable[Run]) -> None:
    """Members change only their own runs; admins change any run of their orgs."""
    if store.accounts.role(user, project.org) == "admin":
        return
    others = [run.name for run in runs if run.owner != user.pk]
    if others:
        raise Forbidden("you can only change runs you logged", others)
