"""Run groups (026): saved groups of a project's runs, Compare, suggestions and the assistant
(D227). Reads answer outsiders on public projects; writes need membership (D226)."""

import logging
from typing import Annotated, Any, Literal

import httpx
import orjson
from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator

from app.api.src.api.dashboard import Snapshot
from app.api.src.api.deps import (
    APIError,
    JSONResponse,
    MemberProjectDep,
    ReadableProjectDep,
    StoreDep,
    UserDep,
    ViewerDep,
    rest_user,
)
from app.api.src.api.runs import RunSelection, RunsQuery, query_runs
from app.api.src.core import assistant, proposals, query
from app.api.src.core import group_analysis as analysis
from app.api.src.core.accounts import User
from app.api.src.core.group_analysis import RunRow
from app.api.src.core.groups import (
    Group,
    Groups,
    Membership,
    best_by,
    finite,
    flatten_conditions,
    kinds,
    repeat_key,
)
from app.api.src.core.store import Project, Store

router = APIRouter(prefix="/api/v1", tags=["groups"], dependencies=[Depends(rest_user)])
# Reads that answer outsiders too, on public projects (D226).
public = APIRouter(prefix="/api/v1", tags=["groups"])

log = logging.getLogger(__name__)

P = "/projects/{entity}/{project}/groups"

Kind = Literal["varies", "repeat", "same", "unique"]
Order = Literal["asc", "desc"]


def check_group_name(value: str) -> str:
    """A group's name: 1-128 characters once surrounding whitespace is dropped, without control
    characters."""
    value = value.strip()
    if not 1 <= len(value) <= 128:
        raise ValueError("must be 1-128 characters")
    if not all(ch.isprintable() for ch in value):
        raise ValueError("must not contain control characters")
    return value


GroupName = Annotated[str, AfterValidator(check_group_name)]
Color = Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")]


def check_filter(value: Any) -> dict[str, Any]:
    """A Mongo-style run filter, as ``runs/query`` takes it (null: none). A filter the query
    language refuses answers 400 (``FilterError``)."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("must be an object")  # noqa: TRY004 (pydantic reports ValueErrors)
    query.Selection(value).sql(0, "r.pk")
    return value


class GroupSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    color: Color | None = None
    filter: dict[str, Any] | None = None
    best_key: str | None = None
    best_order: Order | None = None
    repeat_key: str | None = None
    columns: dict[str, Kind] | None = None
    view: dict[str, Any] | None = None

    @field_validator("filter", mode="before")
    @classmethod
    def _filter(cls, value: Any) -> Any:
        return check_filter(value)


class GroupCreate(GroupSettings):
    name: GroupName
    runs: list[str] = Field(default_factory=list, description="the group's runs, by id")


class GroupUpdate(GroupSettings):
    name: GroupName | None = None
    add: list[str] = Field(default_factory=list, description="runs to add, by id")
    drop: list[str] = Field(default_factory=list, description="runs to drop, by id")
    restore: list[str] = Field(
        default_factory=list, description="dropped runs to offer again as new matches"
    )
    loaded: bool = Field(False, description="move the load time to now (D216)")


class BestSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    best_key: str | None
    best_order: Order = "asc"


class Dismissal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signature: str = Field(pattern=r"^[0-9a-fA-F]{64}$")


class AssistRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request: str = Field(max_length=2000)
    filter: list[Any] = Field(default_factory=list, description="the draft's RunConditions")


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)


class ShownProposal(BaseModel):
    """A proposal on screen, as ``groups/propose`` returned it; fields it doesn't read are
    ignored, so the client can send its proposals back as they are."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(max_length=200)
    name: str = Field(max_length=200)
    why: str = Field("", max_length=1000)
    runs: list[str] = Field(default_factory=list)
    filter: dict[str, Any] | None = None
    axes: list[str] = Field(default_factory=list)
    best_key: str | None = None
    best_order: Order | None = None


class ProposeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages: list[ChatMessage] = Field(default_factory=list, max_length=100)
    proposals: list[ShownProposal] | None = Field(None, max_length=50)


def iso(value: Any) -> str:
    return value.isoformat()


async def find_group(store: Store, project: Project, group: int) -> Group:
    found = await Groups(store).get(project, group)
    if found is None:
        raise APIError(404, f"group {group} not found")
    return found


async def resolve(groups: Groups, project: Project, ids: list[str]) -> dict[str, int]:
    """The pks of runs named by id; an unknown id answers 404, listing them."""
    found = await groups.resolve(project, ids)
    missing = [name for name in dict.fromkeys(ids) if name not in found]
    if missing:
        raise APIError(404, "runs not found", ids=missing)
    return found


def best_json(found: analysis.Best, rows: dict[str, RunRow]) -> dict[str, Any] | None:
    if found.run_id is None:
        return None
    return {
        "run": found.run_id,
        "name": rows[found.run_id].name,
        "value": found.value,
        "run_value": found.run_value,
        "std": found.std,
        "n": found.n,
    }


async def group_infos(
    store: Store, project: Project, groups: list[Group]
) -> tuple[list[dict[str, Any]], dict[int, Membership], dict[int, RunRow]]:
    """Each group as the API returns it (``Group`` in the docs), with the membership and run
    rows read for them."""
    api = Groups(store)
    project_best = await api.project_best(project)
    membership = await api.membership(project, groups)
    keys = {key for group in groups if (key := best_by(group, project_best)[0])}
    pks = {pk for found in membership.values() for pk in found.members}
    rows = await api.rows(project, pks, keys) if pks else {}
    out = []
    for group in groups:
        found = membership[group.pk]
        members = [rows[pk] for pk in found.members if pk in rows]
        key, order = best_by(group, project_best)
        best = None
        if key and order and members:
            by_id = {row.id: row for row in members}
            best = best_json(analysis.best(members, key, order, kinds(group, members)), by_id)
        out.append(
            {
                "id": group.pk,
                "name": group.name,
                "color": group.color,
                "filter": group.filter,
                "run_count": len(found.members),
                "new_count": await api.new_count(project, group, found),
                # The group's own setting; null takes the project's (D217).
                "best_key": group.best_key,
                "best_order": group.best_order,
                "best": best,
                "created_at": iso(group.created_at),
                "loaded_at": iso(group.loaded_at),
            }
        )
    return out, membership, rows


async def group_detail(store: Store, project: Project, group: Group) -> dict[str, Any]:
    """``GET .../groups/{group}``: the group, its runs, how each joined, its dropped runs and
    its settings."""
    (info,), membership, rows = await group_infos(store, project, [group])
    found = membership[group.pk]
    api = Groups(store)
    names = await api.names([*found.members, *found.dropped])
    members = [rows[pk] for pk in found.members if pk in rows]
    added_by = await api.added_by(group)
    return {
        **info,
        "runs": [names[pk] for pk in found.members],
        "dropped": [names[pk] for pk in found.dropped],
        # How each member joined, for the edit page's "+ by hand" (D223): filter or hand.
        "added_by": {names[pk]: added_by.get(pk) for pk in found.members},
        "repeat_key": repeat_key(group, kinds(group, members)),
        "columns": group.columns,
        "view": group.view,
    }


async def assistant_on(store: Store, project: Project, viewer: User | None) -> bool:
    """Whether the assistant answers ``viewer`` on ``project`` (D224): the server has an
    OpenAI key, an org admin turned it on, and the viewer is a member."""
    if not store.settings.openai_api_key or store.accounts.role(viewer, project.org) is None:
        return False
    return await Groups(store).assistant_enabled(project.org)


# ---------------------------------------------------------------------- reads
# The fixed paths come before ``{group}``, which would take them as a group's number.


@public.get(P)
async def list_groups(project: ReadableProjectDep, store: StoreDep, viewer: ViewerDep) -> Response:
    """The project's groups with their run counts, new matches and best run, the number of
    ungrouped runs, the project's best by setting, and whether the assistant is on for you."""
    api = Groups(store)
    groups, _, _ = await group_infos(store, project, await api.of_project(project))
    key, order = await api.project_best(project)
    return JSONResponse(
        {
            "groups": groups,
            "ungrouped_count": await api.ungrouped_count(project),
            "best_key": key,
            "best_order": order,
            "assistant": await assistant_on(store, project, viewer),
        }
    )


def metrics(run: RunRow) -> dict[str, float]:
    """A run's finite summary numbers, W&B's own (``summary._step``...) left out: Compare's
    suggested metric columns."""
    return {
        k: v
        for k, value in run.summary.items()
        if not k.startswith("summary._") and (v := finite(value)) is not None
    }


def compare_row(
    group: Group | None, rows: list[RunRow], run: RunRow | None, found: analysis.Best | None,
    key: str | None, order: Order | None,
) -> dict[str, Any]:  # fmt: skip
    """One row of Compare (D221): a group's best, or a single run (``group`` None)."""
    if group is None:
        assert run is not None
        value = finite(run.summary.get(key)) if key else None
        return {
            "group": None,
            "name": run.name,
            "color": None,
            "run": run.id,
            "run_name": run.name,
            "value": value,
            "run_value": value,
            "std": None,
            "n": int(value is not None),
            "runs": 1,
            "values": [] if value is None else [value],
            "config": dict(run.config),
            "summary": {},
            "best_key": key,
            "best_order": order,
        }
    return {
        "group": group.pk,
        "name": group.name,
        "color": group.color,
        "run": run.id if run else None,
        "run_name": run.name if run else None,
        "value": found.value if found else None,
        "run_value": found.run_value if found else None,
        "std": found.std if found else None,
        "n": found.n if found else 0,
        "runs": len(rows),
        # Every member's value, for Compare's chart of each group's spread.
        "values": [v for row in rows if key and (v := finite(row.summary.get(key))) is not None],
        "config": dict(run.config) if run else {},
        "summary": {},
        "best_key": key,
        "best_order": order,
    }


def ranked(rows: list[dict[str, Any]], order: Order | None) -> list[dict[str, Any]]:
    """Rows best first by ``value``; rows without one last."""
    sign = -1.0 if order == "desc" else 1.0
    return sorted(rows, key=lambda r: (r["value"] is None, sign * (r["value"] or 0.0)))


@public.get(f"{P}/compare")
async def compare(project: ReadableProjectDep, store: StoreDep) -> Response:
    """Compare (D221): each group's best, the config keys that differ between them, and the
    ungrouped runs ranked by the same column (D220).

    Rows of groups ranked by another column than the project's come last; their ``best_key``
    says which. Each row's ``summary`` is its run's summary numbers."""
    api = Groups(store)
    key, order = await api.project_best(project)
    groups = await api.of_project(project)
    membership = await api.membership(project, groups)
    keys = {k for group in groups if (k := best_by(group, (key, order))[0])}
    if key:
        keys.add(key)
    rows = await api.rows(project, None, keys)

    group_rows, other_rows, ignored = [], [], set()
    for group in groups:
        members = [rows[pk] for pk in membership[group.pk].members if pk in rows]
        found_kinds = kinds(group, members)
        ignored |= {k for k, kind in found_kinds.items() if kind == "repeat"}
        group_key, group_order = best_by(group, (key, order))
        found = None
        if group_key and group_order and members:
            found = analysis.best(members, group_key, group_order, found_kinds)
        by_id = {row.id: row for row in members}
        run = by_id.get(found.run_id) if found and found.run_id else None
        row = compare_row(group, members, run, found, group_key, group_order)
        (group_rows if group_key == key else other_rows).append(row)

    ungrouped_pks = await api.ungrouped(project)
    ungrouped = [
        compare_row(None, [], rows[pk], None, key, order) for pk in ungrouped_pks if pk in rows
    ]

    everything = list(rows.values())
    # The shown runs' every summary number, for the suggested metric columns: read apart, since
    # the rows above carry only the best-by columns.
    pk_of = {row.id: pk for pk, row in rows.items()}
    shown = [r for r in [*group_rows, *other_rows, *ungrouped] if r["run"] is not None]
    if shown:
        full = await api.rows(project, {pk_of[r["run"]] for r in shown}, None)
        for r in shown:
            if (run := full.get(pk_of[r["run"]])) is not None:
                r["summary"] = metrics(run)

    # Seeds, and paths and ids distinct in every run, always differ: they aren't compared.
    project_kinds = analysis.classify_columns(everything)
    ignored |= {k for k, kind in project_kinds.items() if kind in ("repeat", "unique")}
    configs = [row["config"] for row in group_rows if row["run"] is not None]
    differing = [k for k in analysis.differing_keys(configs) if k not in ignored]
    return JSONResponse(
        {
            "best_key": key,
            "best_order": order,
            "keys": differing,
            "rows": [*ranked(group_rows, order), *other_rows],
            "ungrouped": ranked(ungrouped, order),
            "ungrouped_count": len(ungrouped),
        }
    )


@public.get(f"{P}/suggestions")
async def list_suggestions(project: ReadableProjectDep, store: StoreDep) -> Response:
    """Suggested groups (D219): W&B groups, then clusters of runs with similar configs, from
    the runs in no group. Dismissed suggestions aren't offered again."""
    found = await Groups(store).suggestions(project)
    return JSONResponse(
        {
            "suggestions": [
                {
                    "signature": suggestion.signature.hex(),
                    "source": suggestion.source,
                    "name": suggestion.name,
                    "filter": suggestion.filter,
                    "runs": list(suggestion.run_ids),
                }
                for suggestion in found
            ]
        }
    )


@public.get(P + "/{group}")
async def get_group(project: ReadableProjectDep, group: int, store: StoreDep) -> Response:
    """One group: its runs, dropped runs, repeat key, kind overrides and chart settings."""
    found = await find_group(store, project, group)
    return JSONResponse(await group_detail(store, project, found))


@public.post(P + "/{group}/runs/query")
async def query_group_runs(
    project: ReadableProjectDep, group: int, body: RunsQuery, store: StoreDep
) -> Response:
    """The runs query (``POST .../runs/query``) over the group's runs: ``filter`` narrows
    them further."""
    found = await find_group(store, project, group)
    api = Groups(store)
    members = (await api.membership(project, [found]))[found.pk].members
    ids = list((await api.names(members)).values())
    membership: dict[str, Any] = {"name": {"$in": ids}}
    combined = membership if body.filter is None else {"$and": [membership, body.filter]}
    return await query_runs(project, body.model_copy(update={"filter": combined}), store)


@public.get(P + "/{group}/workspace")
async def group_workspace(
    project: ReadableProjectDep,
    group: int,
    store: StoreDep,
    sort: Annotated[str | None, Query(description="e.g. -created_at or summary.loss")] = None,
    filter: Annotated[str | None, Query(description="Mongo-style filter as JSON")] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=0, le=500)] = 100,
    max_points: Annotated[int, Query(ge=Snapshot.MIN_POINTS, le=10_000)] = 500,
    system: Annotated[bool, Query(description="also include system metric curves")] = False,
) -> Response:
    """The workspace snapshot (``GET .../workspace``) of the group's runs, so a group's page
    shows them with the Runs page's table and charts: ``filter`` narrows them further, and
    ``total`` counts the matches among them."""
    found = await find_group(store, project, group)
    api = Groups(store)
    members = (await api.membership(project, [found]))[found.pk].members
    ids = list((await api.names(members)).values())
    membership: dict[str, Any] = {"name": {"$in": ids}}
    narrowed = RunSelection(filter=filter).filter
    combined = membership if narrowed is None else {"$and": [membership, narrowed]}
    selection = RunSelection(sort=sort or "-created_at", filter=combined)
    body = await Snapshot.build(store, project, selection, offset, limit, max_points, system)
    return Response(body, media_type="application/octet-stream")


@public.get(P + "/{group}/columns")
async def group_columns(project: ReadableProjectDep, group: int, store: StoreDep) -> Response:
    """Each config key of the group's runs, with its kind (D218), its number of distinct
    values, and whether its kind was set by hand."""
    found = await find_group(store, project, group)
    api = Groups(store)
    members = (await api.membership(project, [found]))[found.pk].members
    rows = list((await api.rows(project, members)).values()) if members else []
    found_kinds = kinds(found, rows)
    out = []
    for key in sorted(found_kinds):
        distinct = {orjson.dumps(row.config[key]) for row in rows if key in row.config}
        out.append(
            {
                "key": key,
                "kind": found_kinds[key],
                "distinct": len(distinct),
                "overridden": key in found.columns or key == found.repeat_key,
            }
        )
    return JSONResponse({"columns": out})


@public.get(P + "/{group}/new")
async def new_matches(project: ReadableProjectDep, group: int, store: StoreDep) -> Response:
    """The group's new matches (D216): runs its filter matches that are neither in it nor
    dropped, newest first. They are offered, never added."""
    found = await find_group(store, project, group)
    api = Groups(store)
    membership = (await api.membership(project, [found]))[found.pk]
    records = await api.new_matches(project, found, membership)
    return JSONResponse(
        {
            "runs": [
                {"id": name, "name": display_name or name, "state": state, "created_at": at}
                for _, name, display_name, state, at, _total in records
            ]
        }
    )


@public.get(P + "/{group}/primary")
async def primary_columns(
    project: ReadableProjectDep, group: int, store: StoreDep, viewer: ViewerDep
) -> Response:
    """The few columns shown first in the group's table (D224): the ones that matter most for
    comparing its runs, as the assistant predicts them (``source: "model"``), else the varying
    config keys (``"analysis"``). Always with the best-by column; never an error of OpenAI's."""
    found = await find_group(store, project, group)
    api = Groups(store)
    best = best_by(found, await api.project_best(project))
    members = (await api.membership(project, [found]))[found.pk].members
    rows = list((await api.rows(project, members, None)).values()) if members else []
    found_kinds = kinds(found, rows)
    columns = assistant.group_columns(rows, found_kinds)
    fallback = {"keys": assistant.primary_fallback(columns, best[0]), "source": "analysis"}
    if not (store.settings.openai_api_key and await api.assistant_enabled(project.org)):
        return JSONResponse(fallback)
    # Stable while the group's runs, best-by column and kinds are: kept per replica.
    signature = analysis.signature(
        [row.id for row in rows]
        + [f"\0best={best[0]}"]
        + [f"\0kind={key}={kind}" for key, kind in found_kinds.items()]
    )
    if (cached := assistant.cached_primary(found.pk, signature)) is not None:
        return JSONResponse({"keys": list(cached), "source": "model"})
    # Only members' visits ask OpenAI (D224); outsiders read what a member's visit predicted.
    if store.accounts.role(viewer, project.org) is None:
        return JSONResponse(fallback)
    input = assistant.primary_input(found.name, found.filter, len(rows), columns, best)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as http:
            client = assistant.DecisionsClient(store.settings.openai_api_key, http=http)
            keys = await assistant.predict_primary(input, columns, best[0], client)
    except Exception:  # the table's columns never fail the page
        log.warning("primary columns of group %d: the model failed", found.pk, exc_info=True)
        return JSONResponse(fallback)
    assistant.cache_primary(found.pk, signature, keys)
    return JSONResponse({"keys": keys, "source": "model"})


# ---------------------------------------------------------------------- writes


@router.put(P + "/settings")
async def set_best(project: MemberProjectDep, body: BestSettings, store: StoreDep) -> Response:
    """Set the project's best by column and direction (D217); a null ``best_key`` goes back to
    the default."""
    await Groups(store).set_project_best(project, body.best_key, body.best_order)
    key, order = await Groups(store).project_best(project)
    return JSONResponse({"best_key": key, "best_order": order})


@router.post(P, status_code=201)
async def create_group(
    project: MemberProjectDep, body: GroupCreate, store: StoreDep, user: UserDep
) -> Response:
    """Create a group of the runs ``runs``, built with ``filter`` (D216). Runs the filter
    matches that aren't in ``runs`` are dropped: they were left out on purpose."""
    api = Groups(store)
    found = await resolve(api, project, body.runs)
    group = await api.create(
        project,
        name=body.name,
        filter=body.filter or {},
        runs=[found[name] for name in dict.fromkeys(body.runs)],
        color=body.color,
        best_key=body.best_key,
        best_order=body.best_order,
        repeat_key=body.repeat_key,
        columns=body.columns,
        view=body.view,
        creator=user.pk,
    )
    return JSONResponse(await group_detail(store, project, group), status_code=201)


@router.post(P + "/suggestions/dismiss", status_code=204)
async def dismiss_suggestion(
    project: MemberProjectDep, body: Dismissal, store: StoreDep
) -> Response:
    """Stop suggesting a suggestion, until its runs change (D219)."""
    await Groups(store).dismiss(project, bytes.fromhex(body.signature))
    return Response(status_code=204)


def column_kind(column: query.Column) -> str:
    """A column's display kind, as the workspace snapshot gives it (``Snapshot.build``)."""
    if column.name == "state":
        return "state"
    if column.name == "owner":
        return "user"
    if column.name in Snapshot.TIME_COLUMNS:
        return "time"
    if column.name in Snapshot.DURATION_COLUMNS:
        return "duration"
    if column.objs == 0:
        return "num"
    return {"string": "str", "bool": "bool"}.get(column.kind, "json")


@router.post(P + "/assist")
async def assist(
    project: MemberProjectDep, body: AssistRequest, store: StoreDep, user: UserDep
) -> Response:
    """One assistant turn (D224): the request and the draft's filters in, an action out. 404
    while the assistant is off; 502 when the Decisions API fails."""
    if not await assistant_on(store, project, user):
        raise APIError(404, "the assistant is off")
    api = Groups(store)
    observed = await api.observed(project)
    # Users are named by email, which a request can say, rather than by id.
    emails = {member.pk: member.email for member, _ in await store.accounts.members(project.org)}
    observed["owner"] = [emails[pk] for pk in observed.get("owner", []) if pk in emails]
    columns = [
        assistant.Column(
            key=column.name,
            kind=column_kind(column),
            runs=column.runs,
            values=tuple(observed.get(column.name, ())),
        )
        for column in await query.columns(store.db.pool, project.pk)
    ]
    runs = [assistant.RunRef(id, name) for id, name in await api.run_refs(project)]
    current = [
        assistant.Condition(item["key"], item["op"], item.get("value"))
        for item in flatten_conditions(body.filter)
    ]
    key = store.settings.openai_api_key
    assert key  # assistant_on checked it
    client = assistant.DecisionsClient(key)
    try:
        # An AssistantError is a BadGateway: 502.
        action = await assistant.assist(body.request, columns, runs, current, client)
    finally:
        await client.aclose()
    return JSONResponse(action.to_json())


async def proposal_context(store: Store, project: Project) -> proposals.Context:
    """The project as the proposals model reads it (D229): its live runs with their config
    and summary metrics, its best by, saved groups and the heuristic suggestions."""
    api = Groups(store)
    best = await api.project_best(project)
    metrics = [
        column.name
        for column in sorted(
            await query.columns(store.db.pool, project.pk), key=lambda c: (-c.runs, c.name)
        )
        if column.name.startswith("summary.")
        and not column.name.startswith("summary._")
        and column.nums > 0
    ][: proposals.METRICS]
    keys = list(dict.fromkeys([*metrics, *([best[0]] if best[0] else [])]))
    by_pk = await api.rows(project, None, keys)
    saved_groups = await api.of_project(project)
    membership = await api.membership(project, saved_groups)
    saved = [
        proposals.SavedGroup(
            group.name,
            group.filter,
            tuple(by_pk[pk].id for pk in membership[group.pk].members if pk in by_pk),
        )
        for group in saved_groups
    ]
    return proposals.context(
        f"{project.entity}/{project.name}",
        list(by_pk.values()),
        best,
        metrics,
        saved,
        await api.suggestions(project),
    )


@router.post(P + "/propose")
async def propose_groups(
    project: MemberProjectDep, body: ProposeRequest, store: StoreDep, user: UserDep
) -> Response:
    """One turn of the Auto tab's chat (D229): the conversation and the proposals on screen
    in, the model's reply and the full set of proposals out, each checked and completed from
    the project's runs. 403 while the assistant is off; 502 when the model fails."""
    if not await assistant_on(store, project, user):
        raise APIError(403, "the assistant is off for this organization")
    ctx = await proposal_context(store, project)
    pool = store.db.pool

    async def match(filter: dict[str, Any]) -> set[str] | None:
        try:
            records, _ = await query.Selection(filter).runs(pool, project.pk, columns="r.name")
        except query.FilterError:
            return None
        return {record[0] for record in records}

    messages = [proposals.Message(m.role, m.content) for m in body.messages]
    shown = None
    if body.proposals is not None:
        shown = [
            proposals.Shown(
                id=item.id,
                name=item.name,
                why=item.why,
                runs=tuple(item.runs),
                filter=item.filter,
                axes=tuple(item.axes),
                best_key=item.best_key,
                best_order=item.best_order,
            )
            for item in body.proposals
        ]
    settings = store.settings
    client = proposals.ResponsesClient(
        settings.openai_api_key or "", model=settings.openai_proposal_model
    )
    try:
        # An AssistantError is a BadGateway: 502.
        turn = await proposals.propose(ctx, messages, shown, match, client)
    finally:
        await client.aclose()
    return JSONResponse(turn.to_json())


@router.patch(P + "/{group}")
async def update_group(
    project: MemberProjectDep, group: int, body: GroupUpdate, store: StoreDep
) -> Response:
    """Change a group's name, filter or settings, add, drop or restore runs, and with
    ``"loaded": true`` move its load time to now (D216). A new filter never removes a run."""
    found = await find_group(store, project, group)
    api = Groups(store)
    given = body.model_fields_set
    if "name" in given and body.name is None:
        raise APIError(422, "name can't be null")
    if "color" in given and body.color is None:
        raise APIError(422, "color can't be null")
    fields = body.model_dump(include=set(Groups.FIELDS) & given)
    for name in ("filter", "columns", "view"):
        if name in fields and fields[name] is None:
            fields[name] = {}
    lists = {"add": body.add, "drop": body.drop, "restore": body.restore}
    ids = [name for names in lists.values() for name in names]
    pks = await resolve(api, project, ids) if ids else {}
    overlap = set(body.add) & set(body.drop)
    if overlap:
        raise APIError(400, "runs both added and dropped", ids=sorted(overlap))
    updated = await api.update(
        found,
        fields,
        add=[pks[name] for name in body.add],
        drop=[pks[name] for name in body.drop],
        restore=[pks[name] for name in body.restore],
        loaded=body.loaded,
    )
    return JSONResponse(await group_detail(store, project, updated))


@router.delete(P + "/{group}", status_code=204)
async def delete_group(project: MemberProjectDep, group: int, store: StoreDep) -> Response:
    """Delete a group. Its runs stay."""
    await Groups(store).delete(await find_group(store, project, group))
    return Response(status_code=204)
