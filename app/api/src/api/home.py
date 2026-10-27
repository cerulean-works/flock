import datetime as dt
import time
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from app.api.src.api.deps import (
    AdminOrgDep,
    APIError,
    JSONResponse,
    OrgDep,
    StoreDep,
    UserDep,
    rest_user,
    visible_org,
)
from app.api.src.core import gpus
from app.api.src.core.budgets import Budgets, fingerprint
from app.api.src.core.feed import KINDS, decode_cursor

router = APIRouter(prefix="/api/v1", tags=["home"], dependencies=[Depends(rest_user)])


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


class Dismissal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fingerprint: str = Field(max_length=64, description="the warning's, as it was shown")


class Exclusion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    excluded: bool


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
    items, next_cursor = await store.feed.page(projects, wanted, owner_pk, key, limit)
    return JSONResponse({"items": items, "next": next_cursor})


@router.get("/orgs/{org}/billing")
async def get_billing(org: OrgDep, store: StoreDep) -> Response:
    """The org's currency, monthly budgets and GPU prices, with the GPU types its runs used."""
    return JSONResponse(await store.budgets.billing(org))


@router.patch("/orgs/{org}/billing")
async def update_billing(org: AdminOrgDep, body: BillingUpdate, store: StoreDep) -> Response:
    """Set the monthly budget, the currency label, or GPU prices (admins)."""
    fields = body.model_dump(include=body.model_fields_set & {"monthly_budget", "currency"})
    if fields.get("currency", "") is None:
        raise APIError(422, "currency cannot be null")
    await store.budgets.update(org, fields, body.rates)
    return JSONResponse(await store.budgets.billing(org))


def parse_month(month: str | None) -> dt.date:
    if month is None:
        return gpus.month_of(time.time())
    try:
        return gpus.parse_month(month)
    except ValueError as e:
        raise APIError(422, f"month {e}") from None


@router.get("/orgs/{org}/budget")
async def get_budget(
    org: OrgDep,
    store: StoreDep,
    user: UserDep,
    month: Annotated[str | None, Query(description="YYYY-MM, default this month (UTC)")] = None,
) -> Response:
    """Spend in a month against the budget: by project and the top runs. Admins also get the
    GPU hours that have no price yet and the runs with unknown GPUs; the warnings about them
    are served by ``GET .../budget/warnings``."""
    budget = await store.budgets.budget(org, parse_month(month))
    del budget["warnings"]
    if store.accounts.role(user, org) != "admin":
        del budget["unpriced"], budget["unknown_gpu"]
    return JSONResponse(budget)


@router.get("/orgs/{org}/budget/warnings")
async def list_warnings(
    org: AdminOrgDep,
    store: StoreDep,
    month: Annotated[str | None, Query(description="YYYY-MM, default this month (UTC)")] = None,
) -> Response:
    """The month's warnings nobody dismissed, and every dismissed warning of any month, newest
    month first (admins)."""
    when = parse_month(month)
    warnings = (await store.budgets.budget(org, when))["warnings"]
    active = [w for w in warnings if not w["dismissed"]]
    return JSONResponse(
        {
            "month": gpus.format_month(when),
            "warnings": [{"month": gpus.format_month(when), **w} for w in active],
            "dismissed": await store.budgets.dismissed_warnings(org),
        }
    )


def parse_warning(warning: str) -> tuple[dt.date, str]:
    """A warning id, ``{kind}:{YYYY-MM}``, as (month, kind); 404 if it's no such warning."""
    kind, _, month = warning.partition(":")
    try:
        when = gpus.parse_month(month)
    except ValueError:
        when = None
    if kind not in Budgets.WARNINGS or when is None:
        raise APIError(404, f"no warning {warning}")
    return when, kind


@router.put("/orgs/{org}/budget/warnings/{warning}/dismissal")
async def dismiss_warning(
    org: AdminOrgDep, warning: str, body: Dismissal, store: StoreDep, user: UserDep
) -> Response:
    """Dismiss a budget warning for everyone in the org (admins). It shows again once it is
    about something new."""
    month, kind = parse_warning(warning)
    items = await store.budgets.warning_items(org, month, kind)
    if items is None:
        raise APIError(404, f"no warning {warning}")
    if fingerprint(items) != body.fingerprint:
        raise APIError(409, "the warning changed: reload it")
    await store.budgets.dismiss(org, month, kind, items, user.pk)
    return Response(status_code=204)


@router.delete("/orgs/{org}/budget/warnings/{warning}/dismissal")
async def restore_warning(org: AdminOrgDep, warning: str, store: StoreDep) -> Response:
    """Show a dismissed budget warning again (admins)."""
    month, kind = parse_warning(warning)
    await store.budgets.restore(org, month, kind)
    return Response(status_code=204)


@router.get("/orgs/{org}/budget/deleted-projects")
async def list_deleted_projects(org: AdminOrgDep, store: StoreDep) -> Response:
    """Deleted projects that used GPU hours, with their spend, and whether it's excluded from
    the budget (admins)."""
    deleted = await store.budgets.deleted_projects(org)
    currency = await store.db.fetchval("SELECT currency FROM orgs WHERE pk = $1", org.pk)
    return JSONResponse({"currency": currency, "deleted_projects": deleted})


@router.patch("/orgs/{org}/budget/deleted-projects/{deleted}")
async def update_deleted_project(
    org: AdminOrgDep, deleted: int, body: Exclusion, store: StoreDep, user: UserDep
) -> Response:
    """Leave a deleted project's spend out of the budget, or count it again (admins)."""
    if await store.budgets.deleted_project(org, deleted) is None:
        raise APIError(404, f"no deleted project {deleted}")
    await store.budgets.set_excluded(org, deleted, body.excluded, user.pk)
    return JSONResponse(await store.budgets.deleted_project(org, deleted))
