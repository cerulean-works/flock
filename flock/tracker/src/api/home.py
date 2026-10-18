import time
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from src.api.deps import (
    AdminOrgDep,
    APIError,
    JSONResponse,
    OrgDep,
    StoreDep,
    UserDep,
    current_user,
    visible_org,
)
from src.core import gpus
from src.core.feed import KINDS, decode_cursor

router = APIRouter(prefix="/api/v1", tags=["home"], dependencies=[Depends(current_user)])


def check_gpu_type(value: str) -> str:
    value = " ".join(value.split())
    if not 1 <= len(value) <= gpus.MAX_TYPE_LENGTH:
        raise ValueError(f"GPU types are 1-{gpus.MAX_TYPE_LENGTH} characters")
    return value


GpuType = Annotated[str, AfterValidator(check_gpu_type)]
Money = Annotated[float, Field(ge=0, le=1e12, allow_inf_nan=False)]


class BillingUpdate(BaseModel):
    """Every key is optional. ``monthly_budget`` applies from the current month on (``null``
    removes the budget); ``rates`` are merged into the prices (``null`` removes one)."""

    model_config = ConfigDict(extra="forbid")

    monthly_budget: Money | None = None
    currency: str | None = Field(None, pattern=r"^[A-Z]{3}$")
    rates: dict[GpuType, Money | None] = Field(default_factory=dict)


@router.get("/feed")
async def feed(
    store: StoreDep,
    user: UserDep,
    org: Annotated[str | None, Query(description="only this organization")] = None,
    project: Annotated[str | None, Query(description="only this project (needs org)")] = None,
    owner: Annotated[str | None, Query(description="me, or a user id: only their runs")] = None,
    kinds: Annotated[str | None, Query(description=f"comma separated: {', '.join(KINDS)}")] = None,
    cursor: Annotated[str | None, Query(description="next, from the previous page")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> Response:
    """What happened across the user's projects, newest first: runs started, finished,
    failed or crashed, and projects created."""
    if org is not None:
        found = visible_org(org, store, user)
        projects = store._org_projects(found)
        if project is not None:
            projects = [p for p in projects if p.name == project]
            if not projects:
                raise APIError(404, f"project {org}/{project} not found")
    elif project is not None:
        raise APIError(400, "project needs org")
    else:
        projects = [p for p in store.projects.values() if store.accounts.role(user, p.org)]
    wanted = set(KINDS)
    if kinds is not None:
        wanted = {kind.strip() for kind in kinds.split(",") if kind.strip()}
        unknown = sorted(wanted - set(KINDS))
        if unknown or not wanted:
            raise APIError(400, f"unknown kinds {unknown}; use {', '.join(KINDS)}")
    owner_pk = None
    if owner is not None:
        if owner == "me":
            owner_pk = user.pk
        elif owner.isdigit():
            owner_pk = int(owner)
        else:
            raise APIError(400, "owner is me or a user id")
    try:
        key = decode_cursor(cursor) if cursor else None
    except ValueError as e:
        raise APIError(400, str(e)) from None
    items, next_cursor = store.feed.page(projects, wanted, owner_pk, key, limit)
    return JSONResponse({"items": items, "next": next_cursor})


@router.get("/orgs/{org}/billing")
async def get_billing(org: OrgDep, store: StoreDep) -> Response:
    """The org's currency, monthly budgets and GPU prices, with the GPU types its runs used."""
    return JSONResponse(store.budgets.billing(org))


@router.patch("/orgs/{org}/billing")
async def update_billing(org: AdminOrgDep, body: BillingUpdate, store: StoreDep) -> Response:
    """Set the monthly budget, the currency label, or GPU prices (admins)."""
    fields = body.model_dump(include=body.model_fields_set & {"monthly_budget", "currency"})
    if fields.get("currency", "") is None:
        raise APIError(422, "currency cannot be null")
    await store.budgets.update(org, fields, body.rates)
    return JSONResponse(store.budgets.billing(org))


@router.get("/orgs/{org}/budget")
async def get_budget(
    org: OrgDep,
    store: StoreDep,
    month: Annotated[str | None, Query(description="YYYY-MM, default this month (UTC)")] = None,
) -> Response:
    """Spend in a month against the budget: by project, the top runs, and GPU hours that
    have no price yet."""
    if month is None:
        when = gpus.month_of(time.time())
    else:
        try:
            when = gpus.parse_month(month)
        except ValueError as e:
            raise APIError(422, f"month {e}") from None
    return JSONResponse(store.budgets.budget(org, when))
