"""The home feed: what happened across projects, derived from runs and projects (there is no
activity log). Items come from one query over ``projects`` and ``runs`` (D76)."""

import base64
import binascii
from collections.abc import Collection, Iterable
from typing import TYPE_CHECKING, Any

import orjson

if TYPE_CHECKING:
    from app.api.src.core.store import Project, Store

# Items at the same instant are ordered project, then run start, then run end.
RANKS = {"project_created": 0, "run_started": 1}
ENDED = {"finished": "run_finished", "failed": "run_failed", "crashed": "run_crashed"}
KINDS = (*RANKS, *ENDED.values())

# (at, rank, pk): items are listed in decreasing key order; cursors are the last key served.
Key = tuple[float, int, int]

# Every item of the projects $1, newest first after the cursor ($6, $7, $8): projects created
# (unless $2 is false), runs started (unless $3 is false) and runs that ended in one of the
# states $5, of the owner $4 if given.
ITEMS = """
    SELECT at, rank, pk, state FROM (
        SELECT extract(epoch FROM created_at)::float8 AS at, 0 AS rank, pk, NULL AS state
        FROM projects WHERE pk = ANY($1) AND $2
        UNION ALL
        SELECT extract(epoch FROM created_at)::float8, 1, pk, NULL FROM runs
        WHERE project_pk = ANY($1) AND deleted_at IS NULL AND $3
            AND ($4::bigint IS NULL OR owner_pk = $4)
        UNION ALL
        SELECT extract(epoch FROM heartbeat_at)::float8, 2, pk, state FROM runs
        WHERE project_pk = ANY($1) AND deleted_at IS NULL AND state = ANY($5)
            AND ($4::bigint IS NULL OR owner_pk = $4)
    ) items
    WHERE $6::float8 IS NULL OR (at, rank, pk) < ($6, $7, $8)
    ORDER BY at DESC, rank DESC, pk DESC
    LIMIT $9
"""

RUNS = """
    SELECT r.pk, r.name, r.display_name, r.state, coalesce(r.gpu_type_override, r.gpu_type),
           coalesce(r.gpu_count_override, r.gpu_count),
           (SELECT coalesce(sum(seconds), 0) FROM gpu_usage u WHERE u.run_pk = r.pk),
           r.tags, r.group_name, r.job_type, r.owner_pk, r.project_pk
    FROM runs r WHERE r.pk = ANY($1)
"""


def encode_cursor(key: Key) -> str:
    return base64.urlsafe_b64encode(orjson.dumps(list(key))).decode().rstrip("=")


def decode_cursor(cursor: str) -> Key:
    try:
        at, rank, pk = orjson.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        return float(at), int(rank), int(pk)
    except (ValueError, TypeError, binascii.Error) as e:
        raise ValueError("invalid cursor") from e


class Feed:
    def __init__(self, store: Store) -> None:
        self.store = store

    async def page(
        self,
        projects: Iterable[Project],
        kinds: Collection[str] = KINDS,
        owner: int | None = None,
        cursor: Key | None = None,
        limit: int = 30,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Items of ``projects`` (only runs of ``owner``, if given) older than ``cursor``,
        newest first; returns them and the cursor of the next page (None on the last)."""
        by_pk = {project.pk: project for project in projects}
        db = self.store.db
        found = await db.fetch(
            ITEMS,
            list(by_pk),
            "project_created" in kinds and owner is None,
            "run_started" in kinds,
            owner,
            [state for state, kind in ENDED.items() if kind in kinds],
            *(cursor if cursor is not None else (None, None, None)),
            limit + 1,
        )
        page = found[:limit]
        runs = {
            record[0]: record
            for record in await db.fetch(RUNS, [pk for _, rank, pk, _ in page if rank])
        }
        owners = await self.store.accounts.briefs(run[10] for run in runs.values())
        items = []
        for at, rank, pk, state in page:
            key = (at, rank, pk)
            if not rank:
                items.append(self._item(key, "project_created", by_pk[pk], None, None))
                continue
            run = runs.get(pk)
            project = by_pk.get(run[11]) if run is not None else None
            if run is None or project is None:
                continue  # deleted or moved meanwhile
            kind = "run_started" if rank == 1 else ENDED[state]
            items.append(self._item(key, kind, project, run, owners.get(run[10])))
        more = len(found) > limit
        last = (page[-1][0], page[-1][1], page[-1][2]) if page else None
        return items, encode_cursor(last) if more and last else None

    @staticmethod
    def _item(
        key: Key, kind: str, project: Project, run: Any, actor: dict[str, Any] | None
    ) -> dict[str, Any]:
        item: dict[str, Any] = {
            "id": f"{kind}:{key[2]}",
            "kind": kind,
            "at": key[0],
            "org": project.entity,
            "project": project.name,
        }
        if run is None:
            item["actor"] = None
            item["description"] = project.description
            return item
        _, name, display_name, state, gpu_type, gpu_count, runtime, tags, group, job_type = run[:10]
        item["actor"] = actor
        item["run"] = {
            "id": name,
            "display_name": display_name,
            "state": state,
            "gpu_type": gpu_type,
            "gpu_count": gpu_count,
            "runtime": round(runtime, 3),
            "tags": list(tags),
            "group": group,
            "job_type": job_type,
        }
        return item
