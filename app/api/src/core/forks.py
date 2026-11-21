"""Forks and blends of datasets (design doc 023): new datasets that read their sources' files
in place, so making one copies no rows.

``flock.hub.datasets._blend`` does the Lance work. This keeps what Lance doesn't: where each
fork or blend came from (``dataset_origins``), the source versions it reads (``dataset_links``,
each pinned by a ``flock-ref-{ulid}`` tag on the source, D173), the jobs that build blends and
make datasets independent (``dataset_jobs``, D176), and the refusals that keep a source in place
while others read it (D177), which ``Datasets`` asks for inside its own locks.
"""

import asyncio
import contextlib
import logging
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

import orjson

from app.api.src.core.errors import BadGateway, Conflict, Forbidden, Invalid, NotFound

if TYPE_CHECKING:
    import asyncpg

    from app.api.src.core.accounts import User
    from app.api.src.core.datasets import Datasets
    from app.api.src.core.store import Org, Store

log = logging.getLogger(__name__)

PIN = "flock-ref-"
"""The prefix of the tags Flock puts on source versions (D173). The tag routes refuse it."""

JOB_COLUMNS = (
    "pk, org_pk, kind, dest, options, state, phase, rows_total, rows_done, result, error, "
    "started_by, extract(epoch FROM started_at)::float8, extract(epoch FROM finished_at)::float8"
)

MAX_SEED = 2**63 - 1


class TooLarge(Exception):
    """Making a dataset independent over the size limit (HTTP 409), with the command to run."""

    def __init__(self, message: str, command: str) -> None:
        super().__init__(message)
        self.message = message
        self.command = command


@dataclass(eq=False)
class Job:
    pk: int
    org_pk: int
    kind: str
    dest: str
    options: dict[str, Any]
    state: str
    phase: str | None
    rows_total: int
    rows_done: int
    result: dict[str, Any] | None
    error: str | None
    started_by: int | None
    started_at: float
    finished_at: float | None = None
    task: asyncio.Task | None = field(default=None, repr=False)
    stop: bool = field(default=False, repr=False)
    saved_at: float = field(default=0.0, repr=False)

    @classmethod
    def from_record(cls, record: Any) -> Job:
        values = list(record)
        values[4] = orjson.loads(values[4])
        values[9] = orjson.loads(values[9]) if values[9] is not None else None
        return cls(*values)

    @property
    def progress(self) -> float:
        if self.state == "finished":
            return 1.0
        if not self.rows_total:
            return 0.0
        return min(self.rows_done / self.rows_total, 1.0)

    def info(self) -> dict[str, Any]:
        return {
            "id": self.pk,
            "kind": self.kind,
            "dest": self.dest,
            "options": self.options,
            "state": self.state,
            "phase": self.phase,
            "rows_total": self.rows_total,
            "rows_done": self.rows_done,
            "progress": self.progress,
            "result": self.result,
            "error": self.error,
            "started_by": self.started_by,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


@dataclass
class Request:
    """A blend as asked (D174, D175): sources with a count each, or fractions of ``total``."""

    sources: list[dict[str, Any]]
    total: int | None = None
    seed: int | None = None
    columns: list[dict[str, Any]] | None = None
    replace: bool = False
    to: str | None = None


class Cancelled(Exception):
    pass


class Forks:
    """The org's forks, blends and their jobs (``store.forks``)."""

    SAVE_INTERVAL: ClassVar[float] = 1.0
    HEARTBEAT: ClassVar[float] = 5.0
    STALE_AFTER: ClassVar[float] = 60.0
    LIST_DAYS: ClassVar[int] = 7
    LIST_LIMIT: ClassVar[int] = 100
    MAX_SOURCES: ClassVar[int] = 16
    DEPENDENTS_SHOWN: ClassVar[int] = 10

    def __init__(self, store: Store) -> None:
        self.store = store
        self.jobs: dict[int, Job] = {}

    @property
    def datasets(self) -> Datasets:
        return self.store.datasets

    # ------------------------------------------------------------------ lifecycle

    async def load(self) -> None:
        await self.sweep()

    async def close(self) -> None:
        for job in list(self.jobs.values()):
            await self._stop(job, "interrupted")

    async def sweep(self) -> None:
        """Jobs whose replica stopped heartbeating are interrupted, and a blend cut short has
        what it wrote and its pins removed."""
        await self.store.db.execute(
            "UPDATE dataset_jobs SET state = 'interrupted', phase = NULL, finished_at = now() "
            "WHERE state = 'running' AND (heartbeat_at IS NULL "
            "OR heartbeat_at < now() - make_interval(secs => $1))",
            self.STALE_AFTER,
        )
        if self.store.s3.upstream is None:
            return
        records = await self.store.db.fetch(
            "SELECT pk, org_pk, dest FROM dataset_jobs WHERE kind = 'blend' AND NOT cleaned "
            "AND state IN ('interrupted', 'failed', 'cancelled')"
        )
        for pk, org_pk, dest in records:
            if pk in self.jobs:
                continue
            org = await self.store.org_by_pk(org_pk)
            try:
                if org is not None:
                    await self._undo_blend(org, dest, pk)
                await self.store.db.execute(
                    "UPDATE dataset_jobs SET cleaned = true WHERE pk = $1", pk
                )
            except Exception:
                log.exception("could not clean up blend %d at %s", pk, dest)

    async def _stop(self, job: Job, state: str) -> None:
        """Stops a running job: a blend sees ``stop`` at its next fragment and undoes what it
        wrote; making a dataset independent can't stop part way, so it's waited for."""
        task = job.task
        if task is None or task.done():
            return
        job.state = state
        job.stop = True
        with contextlib.suppress(asyncio.CancelledError):
            await task

    # ------------------------------------------------------------------ reads

    async def origin(self, org: Org, path: str) -> dict[str, Any] | None:
        """Where the dataset at ``path`` came from, when Flock made it (D179)."""
        record = await self.store.db.fetchrow(
            "SELECT kind, detail, created_by, extract(epoch FROM created_at)::float8 "
            "FROM dataset_origins WHERE org_pk = $1 AND path = $2 AND trash_pk IS NULL",
            org.pk,
            path,
        )
        if record is None:
            return None
        kind, detail, created_by, created_at = record
        user = await self.store.accounts.user(created_by) if created_by else None
        return {
            "kind": kind,
            **orjson.loads(detail),
            "created_by": self.store.accounts.brief(user) if user is not None else None,
            "created_at": created_at,
        }

    async def kinds(self, org: Org, paths: Sequence[str]) -> dict[str, str]:
        """Which of ``paths`` are forks or blends, for the folder listing's labels."""
        if not paths:
            return {}
        records = await self.store.db.fetch(
            "SELECT path, kind FROM dataset_origins WHERE org_pk = $1 AND path = ANY($2) "
            "AND trash_pk IS NULL",
            org.pk,
            list(paths),
        )
        return {r["path"]: r["kind"] for r in records}

    async def dependents(self, org: Org, path: str) -> dict[str, Any]:
        """The datasets reading ``path`` (D177, D179): how many, the first few, and how many of
        those wait in the trash."""
        records = await self.store.db.fetch(
            "SELECT DISTINCT dependent_path, trash_pk IS NOT NULL FROM dataset_links "
            "WHERE org_pk = $1 AND source_path = $2 ORDER BY 2, 1",
            org.pk,
            path,
        )
        live = [p for p, trashed in records if not trashed]
        return {
            "count": len(records),
            "trashed": len(records) - len(live),
            "paths": live[: self.DEPENDENTS_SHOWN],
        }

    async def kept(self, org: Org, path: str) -> dict[int, int]:
        """For each version of ``path`` pinned, how many datasets read it (D179)."""
        records = await self.store.db.fetch(
            "SELECT source_version, count(DISTINCT dependent_path) FROM dataset_links "
            "WHERE org_pk = $1 AND source_path = $2 GROUP BY 1",
            org.pk,
            path,
        )
        return {int(v): int(n) for v, n in records}

    # ------------------------------------------------------------------ hooks for Datasets

    async def refuse_sources(
        self, conn: asyncpg.Connection, org: Org, path: str, folder: bool, what: str
    ) -> None:
        """Raises ``Conflict`` when the dataset at ``path`` (or one in the folder) is read by
        another dataset, so it can't be renamed, moved or deleted (D177). Called with the
        path's lock held."""
        records = await conn.fetch(
            "SELECT source_path, dependent_path, trash_pk IS NOT NULL FROM dataset_links "
            "WHERE org_pk = $1 AND (source_path = $2 "
            "OR ($3 AND left(source_path, length($2) + 1) = $2 || '/')) "
            "ORDER BY 3, 2",
            org.pk,
            path,
            folder,
        )
        if not records:
            return
        source = records[0][0]
        dependents = list(dict.fromkeys(r[1] for r in records))
        trashed = len({r[1] for r in records if r[2]})
        shown = ", ".join(dependents[:2])
        more = f" and {len(dependents) - 2} more" if len(dependents) > 2 else ""
        count = f"{len(dependents)} dataset{'s' if len(dependents) != 1 else ''}"
        reads = "reads" if len(dependents) == 1 else "read"
        if source == path:
            message = f"{path} can't be {what} while {count} {reads} from it: {shown}{more}."
        else:
            message = (
                f"{path} can't be {what}, since {count} {reads} from {source} in it: {shown}{more}."
            )
        if trashed:
            message += (
                f" {trashed} of them {'is' if trashed == 1 else 'are'} in the trash, and still "
                "need it until deleted for good."
            )
        message += " Delete them or make them independent first."
        raise Conflict(message)

    async def renamed(self, conn: asyncpg.Connection, org: Org, path: str, to: str) -> None:
        """The dataset or folder at ``path`` moved to ``to``: its forks and blends follow."""
        for table, column in (("dataset_origins", "path"), ("dataset_links", "dependent_path")):
            await conn.execute(
                f"UPDATE {table} SET {column} = $3 || substr({column}, length($2) + 1) "
                f"WHERE org_pk = $1 AND trash_pk IS NULL AND ({column} = $2 "
                f"OR left({column}, length($2) + 1) = $2 || '/')",
                org.pk,
                path,
                to,
            )

    async def trashed(self, conn: asyncpg.Connection, org: Org, path: str, pk: int) -> None:
        """The dataset or folder at ``path`` went to the trash as entry ``pk``: its forks and
        blends keep their pins, so it can be restored, and their path is free again."""
        for table, column in (("dataset_origins", "path"), ("dataset_links", "dependent_path")):
            await conn.execute(
                f"UPDATE {table} SET trash_pk = $3 WHERE org_pk = $1 AND trash_pk IS NULL "
                f"AND ({column} = $2 OR left({column}, length($2) + 1) = $2 || '/')",
                org.pk,
                path,
                pk,
            )

    async def restored(
        self, conn: asyncpg.Connection, org: Org, path: str, target: str, pk: int
    ) -> None:
        """Trash entry ``pk``, deleted from ``path``, came back at ``target``."""
        for table, column in (("dataset_origins", "path"), ("dataset_links", "dependent_path")):
            await conn.execute(
                f"UPDATE {table} SET trash_pk = NULL, {column} = $3 || substr({column}, "
                f"length($2) + 1) WHERE org_pk = $1 AND trash_pk = $4",
                org.pk,
                path,
                target,
                pk,
            )

    async def purged(self, org_pk: int, pk: int) -> None:
        """Trash entry ``pk`` was deleted for good: its pins are released (D173)."""
        records = await self.store.db.fetch(
            "SELECT ulid, source_path FROM dataset_links WHERE org_pk = $1 AND trash_pk = $2",
            org_pk,
            pk,
        )
        await self._unpin(org_pk, records)
        await self.store.db.execute(
            "DELETE FROM dataset_links WHERE org_pk = $1 AND trash_pk = $2", org_pk, pk
        )
        await self.store.db.execute(
            "DELETE FROM dataset_origins WHERE org_pk = $1 AND trash_pk = $2", org_pk, pk
        )

    # ------------------------------------------------------------------ pins

    async def _bucket(self, org: Org) -> str:
        return (await self.datasets.bucket(org)).upstream_name

    async def _pin(self, name: str, pins: Sequence[tuple[str, str, int]]) -> None:
        """Tags each ``(ulid, source path, version)`` on its source. Undoes the ones made when
        one fails."""
        import lance

        options = self.datasets.storage_options()
        done: list[tuple[str, str]] = []

        def tag(ulid: str, path: str, version: int) -> None:
            ds = lance.dataset(self.datasets.uri(name, path), storage_options=options)
            ds.tags.create(f"{PIN}{ulid}", version)

        try:
            for ulid, path, version in pins:
                await self.datasets._lance(lambda u=ulid, p=path, v=version: tag(u, p, v))
                done.append((ulid, path))
        except BaseException:
            await self._unpin_named(name, done)
            raise

    async def _unpin(self, org_pk: int, records: Sequence[Any]) -> None:
        org = await self.store.org_by_pk(org_pk)
        if org is None or not records:
            return
        try:
            name = await self._bucket(org)
        except NotFound:
            return
        await self._unpin_named(name, [(r[0], r[1]) for r in records])

    async def _unpin_named(self, name: str, pins: Sequence[tuple[str, str]]) -> None:
        import lance

        options = self.datasets.storage_options()

        def untag(ulid: str, path: str) -> None:
            ds = lance.dataset(self.datasets.uri(name, path), storage_options=options)
            if f"{PIN}{ulid}" in ds.tags.list():
                ds.tags.delete(f"{PIN}{ulid}")

        for ulid, path in pins:
            try:
                await asyncio.to_thread(untag, ulid, path)
            except Exception:
                log.warning("could not remove the pin %s%s on %s", PIN, ulid, path, exc_info=True)

    async def _links(
        self,
        conn: asyncpg.Connection,
        org: Org,
        dependent: str,
        sources: Sequence[tuple[str, int]],
        kind: str,
    ) -> list[tuple[str, str, int]]:
        """Records the pins of a new fork or blend at ``dependent``: each source at its version,
        and every pin a source holds itself (inherited, since the new manifest reads those
        files too). Returns them as ``(ulid, path, version)`` for tagging."""
        from app.api.src.core.models import new_ulid

        pins: list[tuple[str, str, int, str]] = []
        for path, version in sources:
            pins.append((new_ulid(), path, version, kind))
            inherited = await conn.fetch(
                "SELECT DISTINCT source_path, source_version FROM dataset_links "
                "WHERE org_pk = $1 AND dependent_path = $2 AND trash_pk IS NULL",
                org.pk,
                path,
            )
            pins += [(new_ulid(), p, int(v), "inherited") for p, v in inherited]
        for ulid, path, version, link_kind in pins:
            await conn.execute(
                "INSERT INTO dataset_links (org_pk, ulid, dependent_path, source_path, "
                "source_version, kind) VALUES ($1, $2, $3, $4, $5, $6)",
                org.pk,
                ulid,
                dependent,
                path,
                version,
                link_kind,
            )
        return [(u, p, v) for u, p, v, _ in pins]

    async def _check_free(self, name: str, path: str) -> None:
        from app.api.src.core.datasets import check_new_path

        check_new_path(path)
        await self.datasets._check_target(name, path)

    # ------------------------------------------------------------------ forks (D171)

    async def fork(
        self, org: Org, user: User, path: str, version: str | None, to: str
    ) -> dict[str, Any]:
        """Makes a fork of ``path`` at ``version`` (default the latest) at ``to``."""
        from app.api.src.core.datasets import check_path
        from flock.hub.datasets import _blend

        name = await self._bucket(org)
        check_path(path)
        to = to.strip().strip("/")
        if to == path:
            raise Invalid("Choose a new path for the fork.")
        async with self.datasets._locked(name, [path, to]) as conn:
            resolved = await self.datasets.resolve(name, path, version)
            await self._check_free(name, to)
            await self._insert_origin(
                conn,
                org,
                user,
                to,
                "fork",
                {"source": {"path": path, "version": resolved.number}},
            )
            pins = await self._links(conn, org, to, [(path, resolved.number)], "fork")
            await self._pin(name, pins)
            import lance

            options = self.datasets.storage_options()
            uri, target = self.datasets.uri(name, path), self.datasets.uri(name, to)

            def clone() -> int:
                source = lance.dataset(uri, version=resolved.number, storage_options=options)
                return _blend.fork(source, resolved.number, target, options).version

            try:
                made = await self.datasets._lance(clone)
            except BaseException:
                await self._unpin_named(name, [(u, p) for u, p, _ in pins])
                with contextlib.suppress(Exception):
                    await self.datasets._remove_prefix(name, f"{to}/")
                raise
        return {"path": to, "version": made, "source": {"path": path, "version": resolved.number}}

    async def _insert_origin(
        self,
        conn: asyncpg.Connection,
        org: Org,
        user: User,
        path: str,
        kind: str,
        detail: Mapping[str, Any],
    ) -> None:
        try:
            await conn.execute(
                "INSERT INTO dataset_origins (org_pk, path, kind, detail, created_by) "
                "VALUES ($1, $2, $3, $4::jsonb, $5)",
                org.pk,
                path,
                kind,
                orjson.dumps(detail).decode(),
                user.pk,
            )
        except Exception as e:
            if type(e).__name__ == "UniqueViolationError":
                raise Conflict(f"Something is already being made at {path}.") from None
            raise

    # ------------------------------------------------------------------ blends (D174, D175)

    async def _sources(self, org: Org, name: str, request: Request) -> tuple[list[Any], list[str]]:
        """Each source opened at its version, with its rows to take; and the problems with the
        sources and amounts (an empty list when there are none)."""
        import lance

        from app.api.src.core.datasets import check_path
        from flock.hub.datasets import _blend

        problems: list[str] = []
        sources = request.sources
        if len(sources) < 2:
            problems.append("A blend needs at least two sources.")
        if len(sources) > self.MAX_SOURCES:
            raise Invalid(f"A blend has at most {self.MAX_SOURCES} sources.")
        paths = [str(s.get("path", "")).strip().strip("/") for s in sources]
        if len(set(paths)) != len(paths):
            raise Invalid("Each dataset can be a source once.")
        fractions = [s.get("fraction") for s in sources]
        counts = [s.get("rows") for s in sources]
        by_fraction = any(f is not None for f in fractions)
        if by_fraction and any(c is not None for c in counts):
            raise Invalid("Give every source a row count, or every source a fraction.")
        rows: list[int] = []
        if by_fraction:
            if request.total is None:
                problems.append("Enter the total rows to split.")
                rows = [0] * len(sources)
            elif any(f is None for f in fractions):
                problems.append("Give every source a percentage.")
                rows = [0] * len(sources)
            else:
                try:
                    rows = _blend.split_counts(
                        [float(f) for f in fractions if f is not None], request.total
                    )
                except _blend.BlendError as e:
                    problems.append(str(e))
                    rows = [0] * len(sources)
        else:
            rows = [int(c or 0) for c in counts]
            if any(c is None or c < 1 for c in counts):
                problems.append("Take at least one row from each source.")
        options = self.datasets.storage_options()
        opened = []
        for path, source, k in zip(paths, sources, rows, strict=True):
            check_path(path)
            version = source.get("version")
            resolved = await self.datasets.resolve(
                name, path, None if version in (None, "") else str(version)
            )
            uri = self.datasets.uri(name, path)
            ds = await self.datasets._lance(
                lambda u=uri, v=resolved.number: lance.dataset(
                    u, version=v, storage_options=options
                )
            )
            opened.append(_blend.SourceInfo(path, ds, k))
        return opened, problems

    async def preview(self, org: Org, request: Request) -> dict[str, Any]:
        """What a blend would take, and its column map with every problem, writing nothing."""
        from flock.hub.datasets import _blend

        name = await self._bucket(org)
        sources, problems = await self._sources(org, name, request)

        def read() -> dict[str, Any]:
            columns = (
                request.columns if request.columns is not None else _blend.automatic_map(sources)
            )
            plan = _blend.check_map(sources, columns)
            live = [s.ds.count_rows() for s in sources]
            found = list(problems)
            for source, n in zip(sources, live, strict=True):
                if source.rows > n and not request.replace:
                    found.append(
                        f"{source.path} has {n:,} rows, fewer than the {source.rows:,} asked "
                        "for. Ask for fewer, or allow taking a source more than once."
                    )
            found += plan.problems
            writes = [
                {"column": out, "source": path, "kind": way[0]}
                for path, ways in plan.ways.items()
                for out, way in ways.items()
                if way[0] in ("cast", "copy", "value")
            ]
            return {
                "sources": [
                    {
                        "path": s.path,
                        "version": s.version,
                        "live": n,
                        "rows": s.rows,
                        "share": s.rows / n if n else None,
                        "schema": [
                            {
                                "name": f.name,
                                "type": _blend.type_name(f.type),
                                "nullable": f.nullable,
                            }
                            for f in s.schema
                        ],
                    }
                    for s, n in zip(sources, live, strict=True)
                ],
                "total": sum(s.rows for s in sources),
                "columns": columns,
                "automatic": request.columns is None,
                "unused": _blend.unused_columns(sources, columns),
                "writes": writes,
                "problems": found,
                # The column map's own problems, for the Columns step; the rest are the sources'.
                "column_problems": plan.problems,
            }

        out = await self.datasets._lance(read)
        if request.to:
            try:
                await self._check_free(name, request.to.strip().strip("/"))
            except (Conflict, Invalid) as e:
                out["problems"].append(getattr(e, "message", str(e)))
        return out

    async def start_blend(self, org: Org, user: User, request: Request) -> Job:
        """Checks the blend and starts building it at ``request.to``. Raises ``Invalid`` with
        every problem when it can't be made as asked."""

        if request.seed is None or not 0 <= request.seed <= MAX_SEED:
            raise Invalid(f"The seed is a whole number from 0 to {MAX_SEED}.")
        to = (request.to or "").strip().strip("/")
        name = await self._bucket(org)
        await self._check_free(name, to)
        request.to = to
        preview = await self.preview(org, request)
        if preview["problems"]:
            raise Invalid(" ".join(preview["problems"]))
        sources = [(s["path"], s["version"]) for s in preview["sources"]]
        lineage = {
            "seed": request.seed,
            "spec": (
                {"mode": "fraction", "total": request.total}
                if any(s.get("fraction") is not None for s in request.sources)
                else {"mode": "rows"}
            ),
            "replace": request.replace,
            "columns": preview["columns"],
            "sources": [
                {
                    "path": s["path"],
                    "version": s["version"],
                    "rows": s["rows"],
                    "live": s["live"],
                    **({"fraction": r["fraction"]} if r.get("fraction") is not None else {}),
                }
                for s, r in zip(preview["sources"], request.sources, strict=True)
            ],
        }
        async with self.datasets._locked(name, [to, *(p for p, _ in sources)]) as conn:
            await self._check_free(name, to)
            for path, version in sources:
                if (await self.datasets.resolve(name, path, str(version))).number != version:
                    raise Conflict(f"{path} changed. Try again.")
            record = await conn.fetchrow(
                "INSERT INTO dataset_jobs (org_pk, kind, dest, options, state, started_by, "
                "started_at, heartbeat_at, rows_total) VALUES ($1, 'blend', $2, $3::jsonb, "
                f"'running', $4, now(), now(), $5) RETURNING {JOB_COLUMNS}",
                org.pk,
                to,
                orjson.dumps(lineage).decode(),
                user.pk,
                sum(s["live"] for s in preview["sources"]) + preview["total"],
            )
            job = Job.from_record(record)
            await self._insert_origin(conn, org, user, to, "blend", {**lineage, "job": job.pk})
            pins = await self._links(conn, org, to, sources, "blend")
            await self._pin(name, pins)
        self.jobs[job.pk] = job
        job.task = asyncio.get_running_loop().create_task(
            self._run(job, self._blend(job, org, name, request, lineage))
        )
        return job

    async def _blend(
        self, job: Job, org: Org, name: str, request: Request, lineage: dict[str, Any]
    ) -> None:
        from flock.hub.datasets import _blend

        job.phase = "sampling"
        sources, _ = await self._sources(org, name, request)
        options = self.datasets.storage_options()
        dest = self.datasets.uri(name, job.dest)

        def progress(rows: int) -> None:
            if job.stop:
                raise _blend.BlendError("The blend was cancelled.")
            job.rows_done += rows

        def build() -> Any:
            return _blend.build(
                sources,
                lineage["columns"],
                dest,
                seed=lineage["seed"],
                replace=lineage["replace"],
                lineage=lineage,
                storage_options=options,
                progress=progress,
                stop=lambda: job.stop,
            )

        try:
            ds = await asyncio.to_thread(build)
        except _blend.BlendError as e:
            raise Invalid(str(e)) from None
        job.result = {"path": job.dest, "version": ds.version, "rows": ds.count_rows()}
        await self.store.db.execute(
            "UPDATE dataset_origins SET detail = detail || $3::jsonb WHERE org_pk = $1 "
            "AND path = $2 AND trash_pk IS NULL",
            org.pk,
            job.dest,
            orjson.dumps({"version": ds.version}).decode(),
        )
        self.datasets._forget(name, job.dest)

    async def _undo_blend(self, org: Org, dest: str, pk: int) -> None:
        """Removes what a blend that didn't finish wrote, its origin and its pins."""
        records = await self.store.db.fetch(
            "SELECT l.ulid, l.source_path FROM dataset_links l JOIN dataset_origins o "
            "ON o.org_pk = l.org_pk AND o.path = l.dependent_path AND o.trash_pk IS NULL "
            "WHERE o.org_pk = $1 AND o.path = $2 AND (o.detail->>'job')::bigint = $3 "
            "AND l.trash_pk IS NULL",
            org.pk,
            dest,
            pk,
        )
        owned = await self.store.db.fetchval(
            "SELECT count(*) FROM dataset_origins WHERE org_pk = $1 AND path = $2 "
            "AND trash_pk IS NULL AND (detail->>'job')::bigint = $3",
            org.pk,
            dest,
            pk,
        )
        if not owned:
            return
        await self._unpin(org.pk, records)
        try:
            name = await self._bucket(org)
            await self.datasets._remove_prefix(name, f"{dest}/")
            self.datasets._forget(name, dest)
        except NotFound:
            pass
        await self.store.db.execute(
            "DELETE FROM dataset_links WHERE org_pk = $1 AND dependent_path = $2 "
            "AND trash_pk IS NULL",
            org.pk,
            dest,
        )
        await self.store.db.execute(
            "DELETE FROM dataset_origins WHERE org_pk = $1 AND path = $2 AND trash_pk IS NULL",
            org.pk,
            dest,
        )

    # ------------------------------------------------------------------ independence (D178)

    def independent_command(self, path: str) -> str:
        return f"flock datasets make-independent {path}"

    async def start_independent(self, org: Org, user: User, path: str) -> Job:
        """Starts rewriting the fork or blend at ``path`` into its own files."""
        from app.api.src.core.datasets import check_path, data_size

        check_path(path)
        name = await self._bucket(org)
        if await self.origin(org, path) is None:
            raise Invalid(f"{path} isn't a fork or a blend, so it reads no other dataset.")
        if not await self.store.db.fetchval(
            "SELECT EXISTS (SELECT FROM dataset_links WHERE org_pk = $1 AND dependent_path = $2 "
            "AND trash_pk IS NULL)",
            org.pk,
            path,
        ):
            raise Invalid(f"{path} is already independent.")
        async with self.store.db.pool.acquire() as conn:
            await self.refuse_sources(conn, org, path, False, "made independent")
        latest = await self.datasets.resolve(name, path, None)
        ds = await self.datasets._lance(lambda: self.datasets._open(name, path, latest))
        size = await self.datasets._lance(lambda: data_size(ds.get_fragments()))
        rows = await self.datasets._lance(ds.count_rows)
        limit = self.store.settings.hf_import_max_bytes
        if size > limit:
            from app.api.src.core.hf_imports import human

            raise TooLarge(
                f"{path} is {human(size)}, more than the {human(limit)} this server copies. "
                "Make it independent with the flock command instead.",
                self.independent_command(path),
            )
        running = await self.store.db.fetchval(
            "SELECT EXISTS (SELECT FROM dataset_jobs WHERE org_pk = $1 AND dest = $2 "
            "AND state = 'running')",
            org.pk,
            path,
        )
        if running:
            raise Conflict(f"{path} is already being made independent.")
        record = await self.store.db.fetchrow(
            "INSERT INTO dataset_jobs (org_pk, kind, dest, options, state, started_by, "
            "started_at, heartbeat_at, rows_total, cleaned) VALUES ($1, 'independent', $2, "
            f"$3::jsonb, 'running', $4, now(), now(), $5, true) RETURNING {JOB_COLUMNS}",
            org.pk,
            path,
            orjson.dumps({"size": size}).decode(),
            user.pk,
            rows,
        )
        job = Job.from_record(record)
        self.jobs[job.pk] = job
        job.task = asyncio.get_running_loop().create_task(
            self._run(job, self._independent(job, org, name))
        )
        return job

    async def _independent(self, job: Job, org: Org, name: str) -> None:
        from flock.hub.datasets import _blend

        path = job.dest
        job.phase = "copying"
        async with self.datasets._locked(name, [path]) as conn:
            await self.refuse_sources(conn, org, path, False, "made independent")
            uri, options = self.datasets.uri(name, path), self.datasets.storage_options()

            def progress(rows: int) -> None:
                job.rows_done += rows

            ds = await asyncio.to_thread(_blend.make_independent, uri, options, progress)
            records = await conn.fetch(
                "DELETE FROM dataset_links WHERE org_pk = $1 AND dependent_path = $2 "
                "AND trash_pk IS NULL RETURNING ulid, source_path",
                org.pk,
                path,
            )
            await conn.execute(
                "UPDATE dataset_origins SET detail = detail || '{\"independent\": true}'::jsonb "
                "WHERE org_pk = $1 AND path = $2 AND trash_pk IS NULL",
                org.pk,
                path,
            )
        await self._unpin_named(name, [(r[0], r[1]) for r in records])
        self.datasets._forget(name, path)
        job.result = {"path": path, "version": ds.version, "rows": ds.count_rows()}

    # ------------------------------------------------------------------ jobs

    async def get(self, org: Org, pk: int) -> Job:
        job = self.jobs.get(pk)
        if job is not None and job.org_pk == org.pk:
            return job
        record = await self.store.db.fetchrow(
            f"SELECT {JOB_COLUMNS} FROM dataset_jobs WHERE pk = $1 AND org_pk = $2", pk, org.pk
        )
        if record is None:
            raise NotFound(f"job {pk} not found")
        return Job.from_record(record)

    async def recent(self, org: Org) -> list[Job]:
        records = await self.store.db.fetch(
            f"SELECT {JOB_COLUMNS} FROM dataset_jobs WHERE org_pk = $1 "
            "AND started_at > now() - make_interval(days => $2) ORDER BY pk DESC LIMIT $3",
            org.pk,
            self.LIST_DAYS,
            self.LIST_LIMIT,
        )
        return [self.jobs.get(r[0]) or Job.from_record(r) for r in records]

    async def cancel(self, org: Org, pk: int, user: User) -> Job:
        job = await self.get(org, pk)
        if job.started_by != user.pk and self.store.accounts.role(user, org) != "admin":
            raise Forbidden("Only who started it, or an admin, can cancel it.")
        if job.state != "running":
            return job
        if job.kind == "independent":
            raise Conflict("Making a dataset independent can't be cancelled once it started.")
        if pk in self.jobs:
            await self._stop(self.jobs[pk], "cancelled")
            return job
        await self.store.db.execute(
            "UPDATE dataset_jobs SET cancel_requested = true WHERE pk = $1 AND state = 'running'",
            pk,
        )
        return await self.get(org, pk)

    async def _save(self, job: Job, force: bool = False) -> None:
        now = time.time()
        if not force and now - job.saved_at < self.SAVE_INTERVAL:
            return
        job.saved_at = now
        cancelled = await self.store.db.fetchval(
            """
            UPDATE dataset_jobs SET state = CASE WHEN cancel_requested AND $2 = 'running'
                    THEN 'cancelled' ELSE $2 END,
                phase = $3, rows_total = $4, rows_done = $5, result = $6::jsonb, error = $7,
                finished_at = coalesce(finished_at, to_timestamp($8)), heartbeat_at = now()
            WHERE pk = $1 RETURNING cancel_requested
            """,
            job.pk,
            job.state,
            job.phase,
            job.rows_total,
            job.rows_done,
            orjson.dumps(job.result).decode() if job.result is not None else None,
            job.error,
            job.finished_at,
        )
        if cancelled and job.state == "running":
            job.state = "cancelled"
            job.stop = True
            raise Cancelled

    async def _heartbeat(self, job: Job) -> None:
        while True:
            await asyncio.sleep(self.SAVE_INTERVAL)
            try:
                await self._save(job, force=time.time() - job.saved_at > self.HEARTBEAT)
            except Cancelled:
                return  # the build sees ``job.stop`` between fragments
            except Exception:
                log.exception("could not save the state of dataset job %d", job.pk)

    async def _run(self, job: Job, work: Any) -> None:
        heartbeat = asyncio.get_running_loop().create_task(self._heartbeat(job))
        org = await self.store.org_by_pk(job.org_pk)
        try:
            await work
            job.state = "finished" if job.state == "running" else job.state
        except asyncio.CancelledError, Cancelled:
            if job.state == "running":
                job.state = "cancelled"
        except NotFound as e:
            job.state = "failed"
            job.error = str(e)
        except Exception as e:
            if job.stop:
                job.state = "cancelled" if job.state == "running" else job.state
            else:
                if not isinstance(e, (Invalid, Conflict, Forbidden, BadGateway)):
                    log.exception("dataset job %d failed", job.pk)
                job.state = "failed"
                job.error = getattr(e, "message", None) or str(e)
        finally:
            work.close()
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            if job.kind == "blend" and job.state != "finished" and org is not None:
                try:
                    await self._undo_blend(org, job.dest, job.pk)
                    await self.store.db.execute(
                        "UPDATE dataset_jobs SET cleaned = true WHERE pk = $1", job.pk
                    )
                except Exception:
                    log.exception("could not clean up blend %d", job.pk)
            elif job.kind == "blend":
                await self.store.db.execute(
                    "UPDATE dataset_jobs SET cleaned = true WHERE pk = $1", job.pk
                )
            job.phase = None
            job.finished_at = time.time()
            try:
                await self._save(job, force=True)
            except Cancelled:
                pass
            except Exception:
                log.exception("could not save the state of dataset job %d", job.pk)
            if self.jobs.get(job.pk) is job:
                del self.jobs[job.pk]


def request_from(body: Mapping[str, Any]) -> Request:
    return Request(
        sources=[dict(s) for s in body.get("sources", [])],
        total=body.get("total"),
        seed=body.get("seed"),
        columns=[dict(c) for c in body["columns"]] if body.get("columns") is not None else None,
        replace=bool(body.get("replace", False)),
        to=body.get("to"),
    )
