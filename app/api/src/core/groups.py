"""Run groups (026): saved lists of runs, their new matches, ungrouped runs, Compare's rows and
suggestions, over the tables of D226.

A group is its list of runs (D216). Its filter only decides which runs it offers as new
matches: runs that match it and are neither in the group nor dropped from it. Nothing here
adds a run to a group except an explicit ``add``. Kinds, bests, names and suggestions are
``group_analysis``'s pure functions over ``RunRow``s that ``rows`` loads from ``run_values``.
"""

import math
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar, Literal

import asyncpg
import orjson

from app.api.src.core import group_analysis as analysis
from app.api.src.core import query, values
from app.api.src.core.errors import Conflict
from app.api.src.core.group_analysis import Kind, RunRow

if TYPE_CHECKING:
    from app.api.src.core.store import Org, Project, Store

Order = Literal["asc", "desc"]

# New groups take the next of the run palette's colors (the web's workspace palette).
COLORS = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948")

# Suggestions per project, with the fingerprint they were computed for (``Groups.suggestions``):
# the most recently computed ``_SUGGESTIONS_MAX`` projects, oldest first.
_SUGGESTIONS: dict[int, tuple[tuple[Any, ...], list[analysis.Suggestion]]] = {}
_SUGGESTIONS_MAX = 256

# The columns ``Group.load`` reads, of ``run_groups`` aliased ``g``.
GROUP_COLUMNS = """
    g.pk, g.project_pk, g.name, g.color, g.filter::text, g.loaded_at, g.best_key, g.best_order,
    g.repeat_key, g.columns::text, g.view::text, g.created_by, g.created_at, g.updated_at
"""

# Writes a group's ($1) rows for runs $2 with status $3 and how each joined ($4). A run already
# with that status keeps its row, so a member added again keeps how it first joined.
_SET_RUNS = """
    INSERT INTO run_group_runs (group_pk, run_pk, status, added_by)
    SELECT $1, run_pk, $3, added_by FROM unnest($2::bigint[], $4::text[]) AS t(run_pk, added_by)
    ON CONFLICT (group_pk, run_pk) DO UPDATE
        SET status = excluded.status, added_by = excluded.added_by, changed_at = now()
        WHERE run_group_runs.status <> excluded.status
"""

# The live runs of project $1 that are a member of none of its groups: the ungrouped runs (D220).
# Groups of the project only: a row a group write left on a run moved away meanwhile is stale.
_UNGROUPED = """
    SELECT r.pk FROM runs r WHERE r.project_pk = $1 AND r.deleted_at IS NULL
    AND NOT EXISTS (
        SELECT FROM run_group_runs m JOIN run_groups g ON g.pk = m.group_pk AND g.project_pk = $1
        WHERE m.run_pk = r.pk AND m.status = 'member'
    )
"""


@dataclass
class Group:
    """A group as read by one request."""

    pk: int
    project_pk: int
    name: str
    color: str
    filter: dict[str, Any]
    loaded_at: datetime
    best_key: str | None
    best_order: Order | None
    repeat_key: str | None
    columns: dict[str, Kind]
    view: dict[str, Any]
    created_by: int | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def load(cls, record: Any) -> Group:
        r = list(record)
        for i in (4, 9, 10):
            r[i] = orjson.loads(r[i]) if r[i] else {}
        return cls(*r)


@dataclass
class Membership:
    """A group's live runs: members and dropped runs, by run pk, in creation order."""

    members: list[int]
    dropped: list[int]

    def all(self) -> set[int]:
        return {*self.members, *self.dropped}


def finite(value: Any) -> float | None:
    """``value`` as a float if it's a finite number (D217: NaN, infinities and missing values
    are never best), else None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def kinds(group: Group, rows: Sequence[RunRow]) -> dict[str, Kind]:
    """The kind of each config key of a group's runs (D218), with the group's overrides and
    its repeat key set by hand."""
    overrides: dict[str, Kind] = dict(group.columns)
    if group.repeat_key:
        overrides[group.repeat_key] = "repeat"
    return analysis.classify_columns(rows, overrides)


def repeat_key(group: Group, found: Mapping[str, Kind]) -> str | None:
    """The group's repeat key: the one set by hand, else the first key of kind repeat."""
    if group.repeat_key:
        return group.repeat_key
    return next((key for key in sorted(found) if found[key] == "repeat"), None)


def best_by(
    group: Group, project_best: tuple[str | None, Order | None]
) -> tuple[str | None, Order | None]:
    """The column and direction a group is ranked by (D217): its own, else the project's."""
    if group.best_key:
        return group.best_key, group.best_order or "asc"
    return project_best


def flatten_conditions(filters: Iterable[Any]) -> list[dict[str, Any]]:
    """The conditions (``{"key", "op", "value"}``) of the web's run filters, in order, those
    inside ``{"match", "filters"}`` groups included."""
    out = []
    for item in filters:
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("filters"), list):
            out += flatten_conditions(item["filters"])
        elif isinstance(item.get("key"), str) and isinstance(item.get("op"), str):
            out.append(item)
    return out


class Groups:
    def __init__(self, store: Store) -> None:
        self.store = store

    @property
    def pool(self) -> asyncpg.Pool:
        return self.store.db.pool

    # ------------------------------------------------------------------ settings

    async def project_best(self, project: Project) -> tuple[str | None, Order | None]:
        """The project's best by setting (D217). Until someone sets one, the first of
        ``summary.val/loss``, ``summary.eval/loss`` and ``summary.loss`` the project has,
        lowest first, or (None, None)."""
        record = await self.pool.fetchrow(
            "SELECT best_key, best_order FROM projects WHERE pk = $1", project.pk
        )
        if record is not None and record[0]:
            return record[0], record[1] or "asc"
        summary = [c.name for c in await query.columns(self.pool, project.pk)]
        found = analysis.default_best_key(k for k in summary if k.startswith("summary."))
        return (found, "asc") if found else (None, None)

    async def set_project_best(self, project: Project, key: str | None, order: Order) -> None:
        await self.pool.execute(
            "UPDATE projects SET best_key = $2, best_order = $3 WHERE pk = $1",
            project.pk,
            key,
            order if key else None,
        )

    async def assistant_enabled(self, org: Org) -> bool:
        """Whether an org admin turned the assistant on for the org (D224)."""
        return bool(
            await self.pool.fetchval("SELECT assistant_enabled FROM orgs WHERE pk = $1", org.pk)
        )

    async def set_assistant_enabled(self, org: Org, enabled: bool) -> None:
        await self.pool.execute(
            "UPDATE orgs SET assistant_enabled = $2 WHERE pk = $1", org.pk, enabled
        )

    # ------------------------------------------------------------------ groups

    async def of_project(self, project: Project) -> list[Group]:
        """The project's groups, oldest first."""
        records = await self.pool.fetch(
            f"SELECT {GROUP_COLUMNS} FROM run_groups g WHERE g.project_pk = $1 ORDER BY g.pk",
            project.pk,
        )
        return [Group.load(record) for record in records]

    async def get(self, project: Project, pk: int) -> Group | None:
        record = await self.pool.fetchrow(
            f"SELECT {GROUP_COLUMNS} FROM run_groups g WHERE g.project_pk = $1 AND g.pk = $2",
            project.pk,
            pk,
        )
        return Group.load(record) if record is not None else None

    async def resolve(self, project: Project, ids: Iterable[str]) -> dict[str, int]:
        """The pks of the project's live runs among ``ids``, by id."""
        return {
            name: pk
            for pk, name in await self.pool.fetch(
                "SELECT pk, name FROM runs WHERE project_pk = $1 AND name = ANY($2) "
                "AND deleted_at IS NULL",
                project.pk,
                list(dict.fromkeys(ids)),
            )
        }

    @staticmethod
    async def matching(
        conn: asyncpg.Connection | asyncpg.Pool, project_pk: int, filter: Mapping[str, Any]
    ) -> set[int]:
        """The pks of the runs a group's filter matches; none for an empty filter (D216: a
        group without a filter never has new matches)."""
        if not filter:
            return set()
        records, _ = await query.Selection(dict(filter)).runs(conn, project_pk, columns="r.pk")
        return {record[0] for record in records}

    async def create(
        self,
        project: Project,
        *,
        name: str,
        filter: Mapping[str, Any],
        runs: Sequence[int],
        color: str | None = None,
        best_key: str | None = None,
        best_order: Order | None = None,
        repeat_key: str | None = None,
        columns: Mapping[str, Kind] | None = None,
        view: Mapping[str, Any] | None = None,
        creator: int | None = None,
    ) -> Group:
        """Create a group of the runs ``runs`` (pks), built with ``filter`` (D216).

        Its runs are the ones listed: those the filter matches join as ``filter``, the others
        as ``hand``. Runs the filter matches now but that aren't listed were unticked on the
        create page, so they're dropped and never offered as new matches."""
        async with self.pool.acquire() as conn, conn.transaction():
            matched = await self.matching(conn, project.pk, filter)
            if color is None:
                count = await conn.fetchval(
                    "SELECT count(*) FROM run_groups WHERE project_pk = $1", project.pk
                )
                color = COLORS[count % len(COLORS)]
            try:
                record = await conn.fetchrow(
                    "INSERT INTO run_groups AS g (project_pk, name, color, filter, best_key, "
                    "best_order, repeat_key, columns, view, created_by) "
                    "VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7, $8::jsonb, $9::jsonb, $10) "
                    f"RETURNING {GROUP_COLUMNS}",
                    project.pk,
                    name,
                    color,
                    orjson.dumps(filter).decode(),
                    best_key,
                    best_order if best_key else None,
                    repeat_key,
                    orjson.dumps(columns or {}).decode(),
                    orjson.dumps(view or {}).decode(),
                    creator,
                )
            except asyncpg.UniqueViolationError as exc:
                raise Conflict(f"a group named {name} already exists") from exc
            assert record is not None
            group = Group.load(record)
            members = list(dict.fromkeys(runs))
            await self._add(conn, group.pk, members, matched)
            await self._drop(conn, group.pk, sorted(matched - set(members)))
        return group

    @staticmethod
    async def _add(
        conn: asyncpg.Connection, group_pk: int, run_pks: Sequence[int], matched: Collection[int]
    ) -> None:
        if run_pks:
            added_by = ["filter" if pk in matched else "hand" for pk in run_pks]
            await conn.execute(_SET_RUNS, group_pk, list(run_pks), "member", added_by)

    @staticmethod
    async def _drop(conn: asyncpg.Connection, group_pk: int, run_pks: Sequence[int]) -> None:
        if run_pks:
            await conn.execute(_SET_RUNS, group_pk, list(run_pks), "dropped", [None] * len(run_pks))

    # Settings a PATCH may change, and whether each is stored as jsonb.
    FIELDS: ClassVar[dict[str, bool]] = {
        "name": False,
        "color": False,
        "filter": True,
        "best_key": False,
        "best_order": False,
        "repeat_key": False,
        "columns": True,
        "view": True,
    }

    async def update(
        self,
        group: Group,
        fields: Mapping[str, Any],
        add: Sequence[int] = (),
        drop: Sequence[int] = (),
        restore: Sequence[int] = (),
        loaded: bool = False,
    ) -> Group:
        """Change a group's settings (``FIELDS``), add runs (pks), drop runs, restore dropped
        runs (they're offered as new matches again if they match), and with ``loaded`` move
        the load time to now (D216). A changed filter only changes which runs are offered: no
        member is removed."""
        sets: list[str] = ["updated_at = now()"]
        args: list[Any] = [group.pk]
        for field, value in fields.items():
            jsonb = self.FIELDS[field]
            args.append(orjson.dumps(value).decode() if jsonb else value)
            sets.append(f"{field} = ${len(args)}{'::jsonb' if jsonb else ''}")
        if "best_key" in fields and not fields["best_key"] and "best_order" not in fields:
            sets.append("best_order = NULL")
        if loaded:
            sets.append("loaded_at = now()")
        async with self.pool.acquire() as conn, conn.transaction():
            try:
                record = await conn.fetchrow(
                    f"UPDATE run_groups g SET {', '.join(sets)} WHERE g.pk = $1 "
                    f"RETURNING {GROUP_COLUMNS}",
                    *args,
                )
            except asyncpg.UniqueViolationError as exc:
                raise Conflict(f"a group named {fields['name']} already exists") from exc
            if record is None:  # deleted meanwhile
                raise Conflict(f"group {group.name} was deleted")
            updated = Group.load(record)
            if add:
                matched = await self.matching(conn, updated.project_pk, updated.filter)
                await self._add(conn, updated.pk, list(dict.fromkeys(add)), matched)
            await self._drop(conn, updated.pk, list(dict.fromkeys(drop)))
            if restore:
                await conn.execute(
                    "DELETE FROM run_group_runs WHERE group_pk = $1 AND run_pk = ANY($2) "
                    "AND status = 'dropped'",
                    updated.pk,
                    list(restore),
                )
        return updated

    async def delete(self, group: Group) -> None:
        """Delete a group; its runs stay (D227)."""
        await self.pool.execute("DELETE FROM run_groups WHERE pk = $1", group.pk)

    # ------------------------------------------------------------------ runs

    async def membership(self, project: Project, groups: Sequence[Group]) -> dict[int, Membership]:
        """Each group's live members and dropped runs, by group pk."""
        out = {group.pk: Membership([], []) for group in groups}
        if not groups:
            return out
        for group_pk, run_pk, status in await self.pool.fetch(
            "SELECT m.group_pk, m.run_pk, m.status FROM run_group_runs m "
            "JOIN runs r ON r.pk = m.run_pk AND r.project_pk = $2 AND r.deleted_at IS NULL "
            "WHERE m.group_pk = ANY($1) ORDER BY r.created_at, r.pk",
            list(out),
            project.pk,
        ):
            found = out[group_pk]
            (found.members if status == "member" else found.dropped).append(run_pk)
        return out

    async def added_by(self, group: Group) -> dict[int, str]:
        """How each member joined (D223): ``filter`` or ``hand``, by run pk."""
        return dict(
            await self.pool.fetch(
                "SELECT run_pk, added_by FROM run_group_runs "
                "WHERE group_pk = $1 AND status = 'member'",
                group.pk,
            )
        )

    async def names(self, run_pks: Iterable[int]) -> dict[int, str]:
        """Run ids by pk."""
        return dict(
            await self.pool.fetch("SELECT pk, name FROM runs WHERE pk = ANY($1)", list(run_pks))
        )

    async def new_matches(
        self, project: Project, group: Group, membership: Membership
    ) -> list[asyncpg.Record]:
        """The group's new matches (D216): runs its filter matches that are neither in it nor
        dropped, newest first, as ``(pk, name, display_name, state, created_at, total)``
        (``total``: the filter's matches, ``Selection.runs``' last column)."""
        if not group.filter:
            return []
        columns = "r.pk, r.name, r.display_name, r.state, extract(epoch FROM r.created_at)::float8"
        records, _ = await query.Selection(group.filter).runs(
            self.pool, project.pk, columns=columns
        )
        known = membership.all()
        return [record for record in records if record[0] not in known]

    async def new_count(self, project: Project, group: Group, membership: Membership) -> int:
        """How many new matches the group has."""
        return len(await self.matching(self.pool, project.pk, group.filter) - membership.all())

    async def ungrouped(self, project: Project) -> list[int]:
        """The pks of the runs in no group (D220)."""
        return [pk for (pk,) in await self.pool.fetch(_UNGROUPED, project.pk)]

    async def ungrouped_count(self, project: Project) -> int:
        return await self.pool.fetchval(f"SELECT count(*)::int FROM ({_UNGROUPED}) o", project.pk)

    async def rows(
        self,
        project: Project,
        run_pks: Iterable[int] | None = None,
        summary_keys: Iterable[str] | None = (),
    ) -> dict[int, RunRow]:
        """The live runs ``run_pks`` (default: every run of the project) as ``RunRow``s, by pk
        in creation order: their ``config.*`` values, and the ``summary_keys`` (None: every
        summary value). Integral numbers of integer columns are ints, as the run table has
        them."""
        pks = None if run_pks is None else list(run_pks)
        records = await self.pool.fetch(
            "SELECT pk, name, display_name, state, extract(epoch FROM created_at)::float8, "
            "group_name FROM runs WHERE project_pk = $1 AND deleted_at IS NULL "
            "AND ($2::bigint[] IS NULL OR pk = ANY($2)) ORDER BY created_at, pk",
            project.pk,
            pks,
        )
        if not records:
            return {}
        found = [record[0] for record in records]
        wanted = None if summary_keys is None else list(summary_keys)
        int_only: dict[str, bool] = dict(
            await self.pool.fetch(
                f"SELECT key, int_only FROM ({values.COLUMNS}) c "
                "WHERE key LIKE 'config.%' OR ($2::text[] IS NULL AND key LIKE 'summary.%') "
                "OR key = ANY($2)",
                project.pk,
                wanted,
            )
        )
        configs: dict[int, dict[str, Any]] = {pk: {} for pk in found}
        summaries: dict[int, dict[str, Any]] = {pk: {} for pk in found}
        for run_pk, key, num, txt, obj in await self.pool.fetch(
            "SELECT run_pk, key, num, txt, obj::text FROM run_values "
            "WHERE project_pk = $1 AND run_pk = ANY($2) AND (key LIKE 'config.%' "
            "OR ($3::text[] IS NULL AND key LIKE 'summary.%') OR key = ANY($3))",
            project.pk,
            found,
            wanted,
        ):
            value = values.decode(num, txt, obj, int_only.get(key, False))
            (configs if key.startswith("config.") else summaries)[run_pk][key] = value
        return {
            pk: RunRow(
                id=name,
                name=display_name or name,
                created_at=created_at,
                state=state,
                group=group_name,
                config=configs[pk],
                summary=summaries[pk],
            )
            for pk, name, display_name, state, created_at, group_name in records
        }

    # ------------------------------------------------------------------ suggestions

    async def dismissed(self, project: Project) -> set[bytes]:
        return {
            bytes(signature)
            for (signature,) in await self.pool.fetch(
                "SELECT signature FROM run_group_dismissals WHERE project_pk = $1", project.pk
            )
        }

    async def dismiss(self, project: Project, signature: bytes) -> None:
        """Stop suggesting the suggestion with this signature (D219)."""
        await self.pool.execute(
            "INSERT INTO run_group_dismissals (project_pk, signature) VALUES ($1, $2) "
            "ON CONFLICT DO NOTHING",
            project.pk,
            signature,
        )

    async def suggestions(self, project: Project) -> list[analysis.Suggestion]:
        """Suggested groups (D219), from every run of the project that is in no group.

        Clustering takes ~2 s at 500 free runs, and the project layout reads suggestions on every
        dashboard refresh, so they're kept per replica until the project's runs, groups or
        dismissals change (D219: "recomputed when the project's runs change"). The key leaves out
        `runs.updated_at`, which moves with every logged step of a running run, though
        suggestions only read configs: a config changed mid-run shows on the next run change.
        """
        key = await self.pool.fetchrow(
            """
            SELECT (SELECT created_at FROM projects WHERE pk = $1),
                   (SELECT count(*) FROM runs WHERE project_pk = $1 AND deleted_at IS NULL),
                   (SELECT max(pk) FROM runs WHERE project_pk = $1 AND deleted_at IS NULL),
                   (SELECT count(*) FROM run_groups WHERE project_pk = $1),
                   (SELECT max(pk) FROM run_groups WHERE project_pk = $1),
                   (SELECT count(*) FROM run_group_runs gr
                    JOIN run_groups g ON g.pk = gr.group_pk WHERE g.project_pk = $1),
                   (SELECT max(gr.changed_at) FROM run_group_runs gr
                    JOIN run_groups g ON g.pk = gr.group_pk WHERE g.project_pk = $1),
                   (SELECT count(*) FROM run_group_dismissals WHERE project_pk = $1)
            """,
            project.pk,
        )
        fingerprint = tuple(key) if key is not None else ()
        cached = _SUGGESTIONS.get(project.pk)
        if cached is not None and cached[0] == fingerprint:
            return cached[1]
        rows = await self.rows(project)
        grouped = set(rows) - set(await self.ungrouped(project))
        ids = {rows[pk].id for pk in grouped if pk in rows}
        found = analysis.suggest(list(rows.values()), ids, await self.dismissed(project))
        _SUGGESTIONS.pop(project.pk, None)
        _SUGGESTIONS[project.pk] = (fingerprint, found)
        while len(_SUGGESTIONS) > _SUGGESTIONS_MAX:
            del _SUGGESTIONS[next(iter(_SUGGESTIONS))]
        return found

    # ------------------------------------------------------------------ the assistant

    async def observed(self, project: Project, cap: int = 1000) -> dict[str, list[Any]]:
        """Up to ``cap`` distinct values of each of the project's columns, for the assistant's
        candidates (D224). A list's elements count as its values (``tags`` is ``x`` matches a
        run tagged ``x``)."""
        out: dict[str, list[Any]] = {}
        seen: dict[str, set[bytes]] = {}
        for key, num, txt, obj in await self.pool.fetch(
            """
            SELECT key, num, txt, obj::text FROM (
                SELECT key, num, txt, obj,
                       row_number() OVER (PARTITION BY key ORDER BY num, txt, obj) AS i
                FROM (
                    SELECT DISTINCT v.key, v.num, v.txt, v.obj FROM run_values v
                    JOIN runs r ON r.pk = v.run_pk AND r.deleted_at IS NULL
                    WHERE v.project_pk = $1
                ) d
            ) n WHERE i <= $2
            """,
            project.pk,
            cap,
        ):
            value = values.decode(num, txt, obj)
            items = value if isinstance(value, list) else [value]
            for item in items:
                if isinstance(item, (dict, list)) or item is None:
                    continue
                if isinstance(item, float) and not math.isfinite(item):
                    continue
                encoded = orjson.dumps(item)
                known = seen.setdefault(key, set())
                if encoded not in known and len(known) < cap:
                    known.add(encoded)
                    out.setdefault(key, []).append(item)
        return out

    async def run_refs(self, project: Project) -> list[tuple[str, str]]:
        """Every live run's id and display name, newest first."""
        return [
            (name, display_name or name)
            for name, display_name in await self.pool.fetch(
                "SELECT name, display_name FROM runs WHERE project_pk = $1 "
                "AND deleted_at IS NULL ORDER BY created_at DESC, pk DESC",
                project.pk,
            )
        ]
