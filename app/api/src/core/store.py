import asyncio
import contextlib
import logging
import secrets
import string
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Self

import asyncpg
import orjson

from app.api.src.core import gpus, live, values
from app.api.src.core.accounts import Accounts
from app.api.src.core.avatars import Avatars
from app.api.src.core.budgets import Budgets
from app.api.src.core.bus import Bus
from app.api.src.core.errors import Conflict, Discarded, Forbidden, NotFound
from app.api.src.core.feed import Feed
from app.api.src.core.files import file_store
from app.api.src.core.history import History, Series, write_chunks
from app.api.src.core.imports import Imports
from app.api.src.core.live import Hub
from app.api.src.core.metric_defs import parse_metric_defs
from app.api.src.core.names import ADJECTIVES, NOUNS
from app.api.src.core.s3 import S3
from app.api.src.db.database import Database
from app.api.src.settings import Settings
from app.api.src.utils import encode_id, loads_lenient, parse_rows

log = logging.getLogger(__name__)

DEFAULT_PROJECT = "uncategorized"

# States in which a client is still logging to a run.
ACTIVE_STATES = ("running", "preempting")

# The columns ``Org.load`` reads, of ``orgs`` aliased ``o``.
ORG_COLUMNS = """
    o.pk, o.name, extract(epoch FROM o.created_at)::float8,
    extract(epoch FROM o.updated_at)::float8, o.currency, o.avatar, o.display_name,
    o.s3_ephemeral_ttl, o.s3_key_max_ttl
"""
# The columns ``Project.load`` reads, of ``projects`` aliased ``p`` (and its org's).
PROJECT_COLUMNS = f"""
    p.pk, p.name, p.description, extract(epoch FROM p.created_at)::float8,
    extract(epoch FROM p.updated_at)::float8, {ORG_COLUMNS}
"""
# The columns ``Run.load`` reads, of ``runs`` aliased ``r``.
RUN_COLUMNS = """
    r.pk, r.project_pk, r.name, r.display_name, r.state,
    extract(epoch FROM r.created_at)::float8, extract(epoch FROM r.updated_at)::float8,
    extract(epoch FROM r.heartbeat_at)::float8, r.tags, r.notes, r.group_name, r.job_type,
    r.host, r.program, r.commit_hash, r.description, r.sweep, r.exitcode, r.owner_pk,
    r.gpu_type, r.gpu_count, r.gpu_type_override, r.gpu_count_override,
    r.history_line_count, r.events_line_count, r.log_line_count, r.metric_defs::text,
    (SELECT coalesce(sum(u.seconds), 0) FROM gpu_usage u WHERE u.run_pk = r.pk)
"""


@dataclass(eq=False)
class Run:
    """A run as read from the database by one request (D76): nothing keeps it up to date."""

    ACTIVE_STATES: ClassVar[frozenset[str]] = frozenset(ACTIVE_STATES)
    # Attributes stored in the run table alongside config.* and summary.* columns.
    ATTRIBUTES: ClassVar[tuple[str, ...]] = values.ATTRIBUTES

    pk: int
    project: Project
    name: str
    display_name: str | None
    state: str
    created_at: float
    updated_at: float
    heartbeat_at: float
    tags: list[str]
    notes: str | None = None
    group: str | None = None
    job_type: str | None = None
    host: str | None = None
    program: str | None = None
    commit: str | None = None
    description: str | None = None
    sweep: str | None = None
    exitcode: int | None = None
    # The user who logged the run (a user pk); runs from before accounts existed have none.
    owner: int | None = None
    # GPUs read from the wandb metadata (a None count: not known yet), and those set by hand,
    # which take precedence.
    gpu_type_detected: str | None = None
    gpu_count_detected: int | None = None
    gpu_type_override: str | None = None
    gpu_count_override: int | None = None
    history_line_count: int = 0
    events_line_count: int = 0
    log_line_count: int = 0
    # What ``wandb.define_metric`` set for plotting: step metrics and hidden metrics.
    metric_defs: list[dict[str, Any]] = field(default_factory=list)
    # Seconds the run was alive (sending heartbeats), over all its resumes: its ledger entries.
    runtime: float = 0.0

    @classmethod
    def load(cls, record: Any, project: Project, offset: int = 0) -> Self:
        """A run from ``RUN_COLUMNS`` at ``offset`` in ``record``."""
        r = record[offset : offset + 28]
        return cls(
            pk=r[0],
            project=project,
            name=r[2],
            display_name=r[3],
            state=r[4],
            created_at=r[5],
            updated_at=r[6],
            heartbeat_at=r[7],
            tags=list(r[8]),
            notes=r[9],
            group=r[10],
            job_type=r[11],
            host=r[12],
            program=r[13],
            commit=r[14],
            description=r[15],
            sweep=r[16],
            exitcode=r[17],
            owner=r[18],
            gpu_type_detected=r[19],
            gpu_count_detected=r[20],
            gpu_type_override=r[21],
            gpu_count_override=r[22],
            history_line_count=r[23],
            events_line_count=r[24],
            log_line_count=r[25],
            metric_defs=orjson.loads(r[26]) if r[26] else [],
            runtime=r[27],
        )

    @property
    def active(self) -> bool:
        return self.state in self.ACTIVE_STATES

    @property
    def entity(self) -> str:
        return self.project.entity

    @property
    def gpu_type(self) -> str | None:
        override = self.gpu_type_override
        return override if override is not None else self.gpu_type_detected

    @property
    def gpu_count(self) -> int | None:
        override = self.gpu_count_override
        return override if override is not None else self.gpu_count_detected

    @property
    def gpu_hours(self) -> float | None:
        return values.gpu_hours(self.runtime, self.gpu_count)

    @property
    def path(self) -> str:
        return f"{self.project.entity}/{self.project.name}/{self.name}"

    @property
    def graphql_id(self) -> str:
        return encode_id("Run", "v1", self.name, self.project.name, self.project.entity)

    def attributes(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.ATTRIBUTES}

    def lines(self, stream: str) -> int:
        """Lines received for a series stream: history rows, or system metric samples."""
        return self.history_line_count if stream == "history" else self.events_line_count


@dataclass
class ImportedRun:
    """A finished run copied from another tracker (see ``Store.import_run``).

    ``history`` and ``events`` are wandb-history.jsonl and wandb-events.jsonl lines, ``logs``
    output.log lines, and ``files`` run files by name. Times are epoch seconds.
    """

    name: str
    display_name: str | None
    state: str
    created_at: float
    heartbeat_at: float
    config: dict[str, Any] | None = None
    summary: dict[str, Any] | None = None
    tags: list[str] = field(default_factory=list)
    notes: str | None = None
    group: str | None = None
    job_type: str | None = None
    host: str | None = None
    program: str | None = None
    commit: str | None = None
    sweep: str | None = None
    exitcode: int | None = None
    owner: int | None = None
    history: list[str] = field(default_factory=list)
    events: list[str] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)
    files: dict[str, bytes] = field(default_factory=dict)


class Org:
    """An organization: the wandb entity its projects and runs are logged under, as read by
    one request.

    ``name`` is its ID: the entity in URLs, API paths and the wandb SDK, fixed at creation.
    ``display_name`` is what people see, and changes freely.
    """

    def __init__(
        self,
        pk: int,
        name: str,
        created_at: float = 0.0,
        updated_at: float = 0.0,
        currency: str = "USD",
        avatar: bytes | None = None,
        display_name: str | None = None,
        s3_ephemeral_ttl: int = 8 * 3600,
        s3_key_max_ttl: int | None = 30 * 86400,
    ):
        self.pk = pk
        self.name = name
        self.display_name = display_name if display_name is not None else name
        self.created_at = created_at
        self.updated_at = updated_at
        self.currency = currency
        self.avatar = avatar  # the digest of its avatar image (see Avatars)
        # S3 key lifetimes in seconds (D56): of keys exchanged for a CLI token, and the longest
        # any key may live (None: no limit).
        self.s3_ephemeral_ttl = s3_ephemeral_ttl
        self.s3_key_max_ttl = s3_key_max_ttl

    @classmethod
    def load(cls, record: Any, offset: int = 0) -> Self:
        """An org from ``ORG_COLUMNS`` at ``offset`` in ``record``."""
        pk, name, created_at, updated_at, currency, avatar, display_name, ephemeral, max_ttl = (
            record[offset : offset + 9]
        )
        return cls(
            pk,
            name,
            created_at,
            updated_at,
            currency,
            bytes(avatar) if avatar else None,
            display_name,
            ephemeral,
            max_ttl,
        )


class Project:
    """A project as read by one request."""

    def __init__(
        self,
        pk: int,
        org: Org,
        name: str,
        description: str | None = None,
        created_at: float = 0.0,
        updated_at: float = 0.0,
    ) -> None:
        self.pk = pk
        self.org = org
        self.name = name
        self.description = description
        self.created_at = created_at
        self.updated_at = updated_at

    @classmethod
    def load(cls, record: Any, offset: int = 0) -> Self:
        """A project from ``PROJECT_COLUMNS`` at ``offset`` in ``record``."""
        pk, name, description, created_at, updated_at = record[offset : offset + 5]
        return cls(pk, Org.load(record, offset + 5), name, description, created_at, updated_at)

    @property
    def entity(self) -> str:
        return self.org.name

    @property
    def key(self) -> tuple[str, str]:
        return (self.entity, self.name)


class Store:
    # Upsertable run fields -> SQL column names.
    FIELD_COLUMNS: ClassVar[dict[str, str]] = {
        "display_name": "display_name",
        "notes": "notes",
        "description": "description",
        "group": "group_name",
        "job_type": "job_type",
        "host": "host",
        "program": "program",
        "commit": "commit_hash",
        "sweep": "sweep",
        "state": "state",
    }
    # A run's row, locked by a write to it (D80), with what resends, summary diffs and the
    # usage ledger need. Live runs only: a run deleted while the writer waited isn't found.
    LOCK_RUN: ClassVar[str] = """
        SELECT state, exitcode, extract(epoch FROM updated_at)::float8,
               extract(epoch FROM heartbeat_at)::float8, history_line_count, events_line_count,
               log_line_count, coalesce(gpu_type_override, gpu_type),
               coalesce(gpu_count_override, gpu_count),
               (SELECT coalesce(sum(seconds), 0) FROM gpu_usage u WHERE u.run_pk = r.pk),
               owner_pk, gpu_type, gpu_count
        FROM runs r WHERE pk = $1 AND deleted_at IS NULL FOR UPDATE
    """
    # The same for the run a wandb client names (entity, project, run).
    LOCK_NAMED_RUN: ClassVar[str] = """
        SELECT r.state, r.exitcode, extract(epoch FROM r.updated_at)::float8,
               extract(epoch FROM r.heartbeat_at)::float8, r.history_line_count,
               r.events_line_count, r.log_line_count,
               coalesce(r.gpu_type_override, r.gpu_type),
               coalesce(r.gpu_count_override, r.gpu_count),
               (SELECT coalesce(sum(seconds), 0) FROM gpu_usage u WHERE u.run_pk = r.pk),
               r.owner_pk, r.pk, r.project_pk, p.org_pk
        FROM runs r
        JOIN projects p ON p.pk = r.project_pk AND p.deleted_at IS NULL
        JOIN orgs o ON o.pk = p.org_pk AND o.deleted_at IS NULL
        WHERE o.name = $1 AND p.name = $2 AND r.name = $3 AND r.deleted_at IS NULL
        FOR UPDATE OF r
    """
    # Tables holding one row per line of a wandb file.
    LINE_TABLES: ClassVar[frozenset[str]] = frozenset({"history", "events", "logs"})
    # Plottable series streams -> the table holding their raw lines.
    STREAMS: ClassVar[dict[str, str]] = {"history": "history", "system": "events"}
    # Org pks stay below 100000 so S3 bucket names have room for them (D53).
    MAX_ORG_PK: ClassVar[int] = 99999

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.files_dir = settings.files_dir
        self.files = file_store(settings)  # D82
        self.db = Database(settings.database_url, max_size=settings.pool_size)
        self._tasks: set[asyncio.Task] = set()
        # Wakes this replica's background work early: a run finished, or runs were deleted.
        self._work_wanted = asyncio.Event()
        self._worker: asyncio.Task | None = None
        self._files_swept = -float("inf")  # when orphaned run files were last looked for
        self.draining = False
        self.live = Hub()
        self.relay = live.Relay(self.live, None)
        self.secret = b""
        # Notifications from every replica (D79): created first, as the parts below listen.
        self.bus = Bus(settings.listen_database_url or settings.database_url)
        self.bus.listen(live.CHANNEL, self.relay.receive)
        self.bus.on_reset(self.relay.reset)
        self.accounts = Accounts(self)
        self.budgets = Budgets(self)
        self.avatars = Avatars(self)
        self.feed = Feed(self)
        self.imports = Imports(self)
        self.s3 = S3(self)

    @staticmethod
    async def _prefixed(conn: asyncpg.Connection, pk: int, prefix: str) -> dict[str, Any]:
        """A run's stored columns starting with ``prefix`` (``"summary."``, ``"config."``)."""
        # Keys sort by code point: those starting with "summary." lie below "summary/".
        upper = prefix[:-1] + chr(ord(prefix[-1]) + 1)
        rows = await conn.fetch(
            "SELECT key, num, txt, obj::text FROM run_values "
            "WHERE run_pk = $1 AND key >= $2 AND key < $3",
            pk,
            prefix,
            upper,
        )
        return {key: values.decode(num, txt, obj) for key, num, txt, obj in rows}

    @staticmethod
    def _pg_text(lines: list[str]) -> list[str]:
        """Postgres text cannot hold NUL bytes, which raw console output occasionally contains."""
        return [line.replace("\x00", "") for line in lines]

    async def open(self) -> None:
        """Connect, check the schema is migrated and start listening (D83: nothing is loaded,
        so this takes the same time whatever the data)."""
        await self.files.open()
        await self.db.open(migrate=self.settings.migrate_on_startup)
        try:
            secret = await self.db.fetchval("SELECT value FROM meta WHERE key = 'upload_secret'")
            self.secret = bytes.fromhex(secret)
            self.relay.start(self.db.pool)
            await self.bus.start()
            await self.accounts.load()
            await self.imports.load()
            await self.s3.load()
            self._background(self._detect_missing_gpus())
            self._worker = asyncio.get_running_loop().create_task(self._work_loop())
        except BaseException:
            await self.bus.close()
            await self.relay.close()
            await self.db.close()
            await self.files.close()
            raise

    async def close(self) -> None:
        await self.s3.close()
        await self.imports.close()
        if self._worker is not None:
            self._worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker
        await self.avatars.flush()
        await self.accounts.flush()
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
        await self.bus.close()
        await self.relay.close()
        await self.db.close()
        await self.files.close()

    @property
    def ready(self) -> bool:
        """Whether this replica should get traffic (D83): it hears every replica's writes,
        and isn't shutting down."""
        return self.bus.listening and not self.draining

    def drain(self) -> None:
        """Start shutting down (on SIGTERM): fail readiness, and end dashboard streams, which
        never finish on their own (clients reconnect to another replica and replay)."""
        self.draining = True
        self.live.close()

    def _background(self, coro: Any) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # ------------------------------------------------------------------ lookups (D76)

    async def org(self, name: str) -> Org:
        org = await self.find_org(name)
        if org is None:
            raise NotFound(f"organization {name} not found")
        return org

    async def find_org(self, name: str) -> Org | None:
        record = await self.db.fetchrow(
            f"SELECT {ORG_COLUMNS} FROM orgs o WHERE o.name = $1 AND o.deleted_at IS NULL", name
        )
        return Org.load(record) if record is not None else None

    async def orgs(self, pks: Iterable[int] | None = None) -> list[Org]:
        """Every org (or those of ``pks``), in pk order."""
        records = await self.db.fetch(
            f"SELECT {ORG_COLUMNS} FROM orgs o WHERE o.deleted_at IS NULL "
            "AND ($1::bigint[] IS NULL OR o.pk = ANY($1)) ORDER BY o.pk",
            None if pks is None else list(pks),
        )
        return [Org.load(record) for record in records]

    async def org_by_pk(self, pk: int) -> Org | None:
        found = await self.orgs([pk])
        return found[0] if found else None

    async def project(self, entity: str, name: str) -> Project | None:
        record = await self.db.fetchrow(
            f"SELECT {PROJECT_COLUMNS} FROM projects p JOIN orgs o ON o.pk = p.org_pk "
            "WHERE o.name = $1 AND p.name = $2 AND o.deleted_at IS NULL "
            "AND p.deleted_at IS NULL",
            entity,
            name,
        )
        return Project.load(record) if record is not None else None

    async def projects(self, orgs: Iterable[Org] | None = None) -> list[Project]:
        """Every project (or those of ``orgs``), by org then creation."""
        records = await self.db.fetch(
            f"SELECT {PROJECT_COLUMNS} FROM projects p JOIN orgs o ON o.pk = p.org_pk "
            "WHERE o.deleted_at IS NULL AND p.deleted_at IS NULL "
            "AND ($1::bigint[] IS NULL OR o.pk = ANY($1)) ORDER BY o.pk, p.pk",
            None if orgs is None else [org.pk for org in orgs],
        )
        return [Project.load(record) for record in records]

    async def project_infos(self, projects: Sequence[Project]) -> list[dict[str, Any]]:
        """What the API shows of each project, with its run and column counts."""
        counts = {
            pk: (runs, active)
            for pk, runs, active in await self.db.fetch(
                "SELECT project_pk, count(*)::int, (count(*) FILTER (WHERE state = ANY($2)))::int "
                "FROM runs WHERE project_pk = ANY($1) AND deleted_at IS NULL GROUP BY project_pk",
                [project.pk for project in projects],
                list(ACTIVE_STATES),
            )
        }
        columns: dict[int, int] = {
            pk: count
            for pk, count in await self.db.fetch(
                """
                SELECT c.project_pk, count(*)::int FROM (
                    SELECT project_pk, key FROM (
                        SELECT project_pk, key, runs FROM project_keys
                        WHERE project_pk = ANY($1)
                        UNION ALL
                        SELECT project_pk, key, runs FROM project_key_changes
                        WHERE project_pk = ANY($1)
                    ) k GROUP BY project_pk, key HAVING sum(runs) > 0
                ) c GROUP BY c.project_pk
                """,
                [project.pk for project in projects],
            )
        }
        out = []
        for project in projects:
            runs, active = counts.get(project.pk, (0, 0))
            out.append(
                {
                    "entity": project.entity,
                    "name": project.name,
                    "description": project.description,
                    "created_at": project.created_at,
                    "updated_at": project.updated_at,
                    "run_count": runs,
                    "active_run_count": active,
                    "column_count": columns.get(project.pk, 0),
                }
            )
        return out

    async def project_info(self, project: Project) -> dict[str, Any]:
        return (await self.project_infos([project]))[0]

    async def get_run(self, entity: str, project: str, name: str) -> Run | None:
        found = await self.project(entity, project)
        return await self.project_run(found, name) if found is not None else None

    async def project_run(self, project: Project, name: str) -> Run | None:
        return (await self.project_runs(project, [name])).get(name)

    async def project_runs(self, project: Project, names: Iterable[str]) -> dict[str, Run]:
        """The live runs of ``project`` named ``names``, by name."""
        records = await self.db.fetch(
            f"SELECT {RUN_COLUMNS} FROM runs r WHERE r.project_pk = $1 AND r.name = ANY($2) "
            "AND r.deleted_at IS NULL",
            project.pk,
            list(names),
        )
        return {record[2]: Run.load(record, project) for record in records}

    async def run_keys(self, runs: Sequence[Run], stream: str = "history") -> list[set[str]]:
        """The plottable metrics each run logged to a series stream, without wandb's
        bookkeeping (``_step``, ``_runtime``, ...), which every row carries."""
        found: dict[int, set[str]] = {run.pk: set() for run in runs}
        for run_pk, key in await self.db.fetch(
            "SELECT run_pk, key FROM run_metrics WHERE run_pk = ANY($1) AND stream = $2 "
            "AND key NOT LIKE '\\_%'",
            list(found),
            stream,
        ):
            found[run_pk].add(key)
        return [found[run.pk] for run in runs]

    async def run_counts(self, project: Project) -> tuple[int, int]:
        """How many runs ``project`` has, and how many of them are active."""
        record = await self.db.fetchrow(
            "SELECT count(*)::int, (count(*) FILTER (WHERE state = ANY($2)))::int FROM runs "
            "WHERE project_pk = $1 AND deleted_at IS NULL",
            project.pk,
            list(ACTIVE_STATES),
        )
        assert record is not None
        return record[0], record[1]

    async def ordinals(self, project: Project, runs: Sequence[Run]) -> list[int]:
        """Each run's position among ``project``'s runs in creation order (from 0)."""
        found: dict[int, int] = {
            pk: ordinal
            for pk, ordinal in await self.db.fetch(
                "SELECT pk, ordinal FROM (SELECT pk, (row_number() OVER (ORDER BY created_at, pk)"
                " - 1)::int AS ordinal FROM runs WHERE project_pk = $1 AND deleted_at IS NULL) o "
                "WHERE pk = ANY($2)",
                project.pk,
                [run.pk for run in runs],
            )
        }
        return [found.get(run.pk, 0) for run in runs]

    async def active_runs(self, project: Project) -> list[str]:
        return [
            name
            for (name,) in await self.db.fetch(
                "SELECT name FROM runs WHERE project_pk = $1 AND deleted_at IS NULL "
                "AND state = ANY($2) ORDER BY created_at, pk",
                project.pk,
                list(ACTIVE_STATES),
            )
        ]

    async def read_config(self, run: Run) -> dict[str, Any]:
        raw = await self.db.fetchval("SELECT config FROM runs WHERE pk = $1", run.pk)
        return loads_lenient(raw) if raw else {}

    async def read_summary(self, run: Run) -> dict[str, Any]:
        raw = await self.db.fetchval("SELECT summary FROM runs WHERE pk = $1", run.pk)
        return loads_lenient(raw) if raw else {}

    async def tail_lines(self, table: str, run: Run, count: int) -> list[str]:
        assert table in self.LINE_TABLES
        rows = await self.db.fetch(
            f"SELECT data FROM {table} WHERE run_pk = $1 ORDER BY line DESC LIMIT $2",
            run.pk,
            count,
        )
        return [r[0] for r in reversed(rows)]

    async def read_lines(
        self, table: str, run: Run, offset: int, limit: int
    ) -> list[tuple[int, str]]:
        assert table in self.LINE_TABLES
        rows = await self.db.fetch(
            f"SELECT line, data FROM {table} WHERE run_pk = $1 AND line >= $2 "
            "ORDER BY line LIMIT $3",
            run.pk,
            offset,
            limit,
        )
        return [(r[0], r[1]) for r in rows]

    async def list_files(self, run: Run) -> list[tuple[str, int, float]]:
        rows = await self.db.fetch(
            "SELECT name, size, extract(epoch FROM updated_at)::float8 FROM run_files "
            "WHERE run_pk = $1 ORDER BY name",
            run.pk,
        )
        return [(r[0], r[1], r[2]) for r in rows]

    @staticmethod
    def file_parts(name: str) -> tuple[str, ...] | None:
        """The path components of a run file's name; None if it would leave the run's directory."""
        parts = Path(name).parts
        if not parts or Path(name).is_absolute() or any(p in ("..", "") for p in parts):
            return None
        return parts

    def file_path(self, run: Run, name: str) -> Path | None:
        """Where a run's file is with the local backend; None for invalid names."""
        parts = self.file_parts(name)
        return None if parts is None else self.files_dir / str(run.pk) / Path(*parts)

    async def read_file(self, run: Run, name: str) -> bytes | None:
        """A run file's bytes (for small files the server reads itself)."""
        if self.file_parts(name) is None:
            return None
        found = await self.files.get(run.pk, name)
        if found is None:
            return None
        if isinstance(found, Path):
            return await asyncio.to_thread(found.read_bytes)
        chunks, _ = found
        return b"".join([chunk async for chunk in chunks])

    # ------------------------------------------------------------------ locks (D80)

    @staticmethod
    async def _lock_org(conn: asyncpg.Connection, org: Org, update: bool = False) -> None:
        """Lock ``org``'s row (shared: no one deletes it meanwhile; ``update``: no one else
        changes its projects meanwhile); NotFound if it was deleted."""
        mode = "UPDATE" if update else "SHARE"
        if not await conn.fetchval(
            f"SELECT true FROM orgs WHERE pk = $1 AND deleted_at IS NULL FOR {mode}", org.pk
        ):
            raise NotFound(f"organization {org.name} not found")

    @staticmethod
    async def _lock_projects(
        conn: asyncpg.Connection, projects: Sequence[Project], update: bool = False
    ) -> None:
        """Lock projects' rows in pk order, after their org's (catalog changes lock orgs, then
        projects, then runs, so they never deadlock); NotFound if one was deleted."""
        mode = "UPDATE" if update else "SHARE"
        pks = sorted({project.pk for project in projects})
        found = await conn.fetch(
            f"SELECT pk FROM projects WHERE pk = ANY($1) AND deleted_at IS NULL "
            f"ORDER BY pk FOR {mode}",
            pks,
        )
        if len(found) < len(pks):
            alive = {pk for (pk,) in found}
            gone = next(p for p in projects if p.pk not in alive)
            raise NotFound(f"project {gone.entity}/{gone.name} not found")

    # ------------------------------------------------------------------ mutations

    async def upsert_run(
        self,
        entity: str,
        project_name: str | None,
        name: str | None,
        fields: dict[str, Any],
        owner: int | None = None,
        admin: bool = True,
    ) -> tuple[Run, bool]:
        """Create or update a run. ``fields`` holds only the values the client provided.

        Recognised fields: the keys of ``FIELD_COLUMNS`` plus ``tags`` (list), ``config``
        (wandb-style config dict) and ``summary`` (dict). A new run belongs to ``owner``; an
        existing one can only be updated by its owner, unless ``admin``.
        """
        project_name = project_name or DEFAULT_PROJECT
        # Clients that don't name the run get a random 8 character id, as wandb does.
        alphabet = string.ascii_lowercase + string.digits
        name = name or "".join(secrets.choice(alphabet) for _ in range(8))
        if await self.discarding(entity, project_name, name):
            raise Discarded(project_name, name)
        while True:
            project = await self.ensure_project(entity, project_name)
            try:
                async with self.db.pool.acquire() as conn, conn.transaction():
                    # Shared: deleting, renaming or moving runs out of it waits for this.
                    await self._lock_projects(conn, [project])
                    pk = await self._create_run(conn, project, name, fields, owner)
                    created = pk is not None
                    if pk is None:
                        pk = await conn.fetchval(
                            "SELECT pk FROM runs WHERE project_pk = $1 AND name = $2 "
                            "AND deleted_at IS NULL",
                            project.pk,
                            name,
                        )
                        await self._update_run(conn, project, pk, name, fields, owner, admin)
            except NotFound:
                continue  # deleted or renamed meanwhile: resolve the name again
            run = await self.project_run(project, name)
            if run is None:  # deleted right after
                raise NotFound(f"run {entity}/{project_name}/{name} not found")
            return run, created

    @staticmethod
    def _check_owner(path: str, run_owner: int | None, owner: int | None, admin: bool) -> None:
        if not admin and run_owner != owner:
            raise Forbidden(f"run {path} belongs to another user", [path.rsplit("/", 1)[-1]])

    async def _create_run(
        self,
        conn: asyncpg.Connection,
        project: Project,
        name: str,
        fields: dict[str, Any],
        owner: int | None = None,
    ) -> int | None:
        """Insert the run ``name``; None if the project has one (it is left alone)."""
        now = time.time()
        display_name = fields.get("display_name")
        if not display_name:
            # wandb-style "adjective-noun-N" unless the client named the run.
            count = await conn.fetchval(
                "SELECT count(*) FROM runs WHERE project_pk = $1 AND deleted_at IS NULL",
                project.pk,
            )
            display_name = f"{secrets.choice(ADJECTIVES)}-{secrets.choice(NOUNS)}-{count + 1}"
        cols = {col: fields.get(key) for key, col in self.FIELD_COLUMNS.items()}
        cols["display_name"] = display_name
        cols["state"] = fields.get("state") or "running"
        tags = [str(tag) for tag in fields.get("tags") or []]
        config = fields.get("config")
        summary = fields.get("summary")
        metric_defs = parse_metric_defs(config) if config else []
        pk = await conn.fetchval(
            """
            INSERT INTO runs (project_pk, name, display_name, state, created_at, updated_at,
                              heartbeat_at, config, summary, tags, notes, group_name,
                              job_type, host, program, commit_hash, description, sweep,
                              owner_pk, metric_defs)
            VALUES ($1, $2, $3, $4, to_timestamp($5), to_timestamp($5), to_timestamp($5),
                    $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18::jsonb)
            ON CONFLICT (project_pk, name) WHERE deleted_at IS NULL DO NOTHING
            RETURNING pk
            """,
            project.pk,
            name,
            display_name,
            cols["state"],
            now,
            orjson.dumps(config).decode() if config is not None else None,
            orjson.dumps(summary).decode() if summary is not None else None,
            tags,
            cols["notes"],
            cols["group_name"],
            cols["job_type"],
            cols["host"],
            cols["program"],
            cols["commit_hash"],
            cols["description"],
            cols["sweep"],
            owner,
            orjson.dumps(metric_defs).decode(),
        )
        if pk is None:
            return None
        columns = {
            "name": name,
            "display_name": display_name,
            "state": cols["state"],
            "created_at": now,
            "updated_at": now,
            "heartbeat_at": now,
            "tags": tags,
            "group": cols["group_name"],
            "job_type": cols["job_type"],
            "notes": cols["notes"],
            "description": cols["description"],
            "host": cols["host"],
            "program": cols["program"],
            "commit": cols["commit_hash"],
            "sweep": cols["sweep"],
            "owner": owner,
            **(values.config_columns(config) if config else {}),
            **(values.summary_columns(summary) if summary else {}),
        }
        await values.write(conn, project.pk, [(pk, values.diff({}, columns))])
        created = {"run": name, "display_name": display_name, "state": cols["state"]}
        await live.publish(conn, [live.event_message(project.pk, None, "created", created)])
        return pk

    async def _update_run(
        self,
        conn: asyncpg.Connection,
        project: Project,
        pk: int,
        name: str,
        fields: dict[str, Any],
        owner: int | None,
        admin: bool,
    ) -> None:
        now = time.time()
        sets: dict[str, Any] = {}
        for key, col in self.FIELD_COLUMNS.items():
            if fields.get(key) is not None:
                sets[col] = fields[key]
        tags = fields.get("tags")
        if tags is not None:
            tags = [str(tag) for tag in tags]
            sets["tags"] = tags
        config = fields.get("config")
        summary = fields.get("summary")
        if config is not None:
            sets["config"] = orjson.dumps(config).decode()
        if summary is not None:
            sets["summary"] = orjson.dumps(summary).decode()
        defs = parse_metric_defs(config) if config is not None else None
        if defs is not None:
            sets["metric_defs"] = orjson.dumps(defs).decode()
        assignments = ", ".join(f"{col} = ${i}" for i, col in enumerate(sets, start=3))
        attributes: dict[str, Any] = {"updated_at": now}
        for key in self.FIELD_COLUMNS:
            if fields.get(key) is not None:
                attributes[key] = fields[key]
        if tags is not None:
            attributes["tags"] = tags
        old = await conn.fetchrow(
            "SELECT display_name, notes, description, group_name, job_type, host, program, "
            "commit_hash, sweep, state, tags, extract(epoch FROM updated_at)::float8, "
            "history_line_count, metric_defs::text, owner_pk FROM runs WHERE pk = $1 FOR UPDATE",
            pk,
        )
        assert old is not None
        self._check_owner(f"{project.entity}/{project.name}/{name}", old[14], owner, admin)
        await conn.execute(
            f"UPDATE runs SET updated_at = to_timestamp($2){', ' if sets else ''}"
            f"{assignments} WHERE pk = $1",
            pk,
            now,
            *sets.values(),
        )
        before = dict(zip((*self.FIELD_COLUMNS, "tags", "updated_at"), old, strict=False))
        before["tags"] = list(before["tags"])
        changes = values.diff(before, attributes, keys=attributes)
        if config is not None:
            previous = await self._prefixed(conn, pk, "config.")
            values.diff(previous, values.config_columns(config), into=changes)
        if summary is not None:
            previous = await self._prefixed(conn, pk, "summary.")
            values.diff(previous, values.summary_columns(summary), into=changes)
        await values.write(conn, project.pk, [(pk, changes)])
        state = attributes.get("state", old[9])
        data: dict[str, Any] = {"run": name, "state": state, "lines": old[12], "updated_at": now}
        summary_changed = {
            key: value for key, value in changes.values.items() if key.startswith("summary.")
        }
        if summary_changed:
            data["summary"] = summary_changed
        if defs is not None and defs != orjson.loads(old[13] or "[]"):
            data["metric_defs"] = defs
        await live.publish(conn, [live.event_message(project.pk, name, "run", data)])
        if old[9] in ACTIVE_STATES and state not in ACTIVE_STATES:
            self._work_wanted.set()  # compact its chunks

    async def file_stream(
        self,
        entity: str,
        project_name: str,
        name: str,
        payload: dict[str, Any],
        owner: int | None = None,
        admin: bool = True,
    ) -> None:
        """Apply one wandb ``file_stream`` request (from ``owner``, see ``upsert_run``)."""
        files = payload.get("files") or {}
        history = files.get("wandb-history.jsonl")
        summary = files.get("wandb-summary.json")
        events = files.get("wandb-events.jsonl")
        output = files.get("output.log")

        if payload.get("complete"):
            exitcode = payload.get("exitcode")
            exitcode = int(exitcode) if exitcode is not None else 0
            state = "finished" if exitcode == 0 else "failed"
        else:
            exitcode = None
            state = "preempting" if payload.get("preempting") else "running"

        # Each wandb file arrives as {"offset": first line number, "content": [lines]}.
        chunks = []
        for item in (history, events, output):
            content = (item or {}).get("content") or []
            chunks.append((int((item or {}).get("offset") or 0), [str(c) for c in content]))
        (h_off, h_lines), (e_off, e_lines), (o_off, o_lines) = chunks
        summary_raw = None
        summary_data = None
        if summary and summary.get("content"):
            summary_raw = summary["content"][-1]
            try:
                if isinstance(summary_raw, (str, bytes)):
                    summary_data = loads_lenient(summary_raw)
                else:
                    summary_data = summary_raw
                    summary_raw = orjson.dumps(summary_raw).decode()
            except ValueError:
                summary_data = None
            if not isinstance(summary_data, dict):
                log.warning(
                    "run %s/%s/%s: dropping unparseable summary", entity, project_name, name
                )
                summary_raw = summary_data = None
            elif isinstance(summary_raw, bytes):
                summary_raw = summary_raw.decode()
        now = time.time()

        async with self.db.pool.acquire() as conn, conn.transaction():
            # Only UpsertBucket creates runs: a client still logging to a deleted run must not
            # bring back a partial copy of it.
            locked = await conn.fetchrow(self.LOCK_NAMED_RUN, entity, project_name, name)
            if locked is None:
                if await self.discarding(entity, project_name, name):
                    return
                raise NotFound(f"run {entity}/{project_name}/{name} not found")
            old_state, old_exitcode, old_updated, old_heartbeat, *counts = locked[:7]
            gpu_type, gpu_count, runtime, run_owner, pk, project_pk, org_pk = locked[7:]
            self._check_owner(f"{entity}/{project_name}/{name}", run_owner, owner, admin)
            old_history, old_events, old_logs = counts
            history_count = max(old_history, h_off + len(h_lines))
            events_count = max(old_events, e_off + len(e_lines))
            log_count = max(old_logs, o_off + len(o_lines))
            # Clients resend lines they couldn't confirm: only lines past those stored become
            # points, so chunks never overlap (D78). Each metric's are one chunk per stream.
            fresh: dict[str, int] = {}
            chunk_rows = []
            for stream, offset, lines, stored in (
                ("history", h_off, h_lines, old_history),
                ("system", e_off, e_lines, old_events),
            ):
                new_from = max(offset, stored)
                if new_from < offset + len(lines):
                    hist = History()
                    hist.append_rows(new_from, parse_rows(lines[new_from - offset :]))
                    if hist.series:
                        fresh[stream] = new_from
                        for key, series in hist.series.items():
                            chunk_rows.append(
                                (stream, key, new_from, series.n, series.encode(new_from))
                            )
            accrued = self.budgets.accrual(old_heartbeat, now)
            previous = (
                await self._prefixed(conn, pk, "summary.") if summary_data is not None else {}
            )
            # The request's lines, ledger and chunks in one statement, so one round trip.
            # Lines are upserted because clients resend chunks they could not confirm.
            await conn.execute(
                """
                WITH history_lines AS (
                    INSERT INTO history (run_pk, line, data)
                    SELECT $1, line, data FROM unnest($2::int[], $3::text[]) AS t(line, data)
                    ON CONFLICT (run_pk, line) DO UPDATE SET data = excluded.data
                ), event_lines AS (
                    INSERT INTO events (run_pk, line, data)
                    SELECT $1, line, data FROM unnest($4::int[], $5::text[]) AS t(line, data)
                    ON CONFLICT (run_pk, line) DO UPDATE SET data = excluded.data
                ), log_lines AS (
                    INSERT INTO logs (run_pk, line, data)
                    SELECT $1, line, data FROM unnest($6::int[], $7::text[]) AS t(line, data)
                    ON CONFLICT (run_pk, line) DO UPDATE SET data = excluded.data
                ), usage AS (
                    INSERT INTO gpu_usage (org_pk, project_pk, run_pk, run_name, month,
                                           seconds, gpu_type, gpu_count)
                    SELECT $15::bigint, $16::bigint, $1, $17::text, month, seconds,
                           $18::text, $19::int
                    FROM unnest($20::date[], $21::float8[]) AS t(month, seconds)
                    ON CONFLICT (run_pk, month)
                    DO UPDATE SET seconds = gpu_usage.seconds + excluded.seconds
                ), chunks AS (
                    INSERT INTO series_chunks (run_pk, stream, key, first_line, n, data)
                    SELECT $1, stream, key, first_line, n, data
                    FROM unnest($22::text[], $23::text[], $24::int[], $25::int[],
                                $26::bytea[]) AS t(stream, key, first_line, n, data)
                ), metrics AS (
                    INSERT INTO run_metrics (run_pk, stream, key)
                    SELECT $1, stream, key FROM unnest($22::text[], $23::text[]) AS t(stream, key)
                    ON CONFLICT DO NOTHING
                )
                UPDATE runs
                SET state = $8, exitcode = $9, updated_at = to_timestamp($10),
                    heartbeat_at = to_timestamp($10), summary = COALESCE($11, summary),
                    history_line_count = $12, events_line_count = $13,
                    log_line_count = $14, uncompacted = uncompacted + $27
                WHERE pk = $1
                """,
                pk,
                list(range(h_off, h_off + len(h_lines))),
                self._pg_text(h_lines),
                list(range(e_off, e_off + len(e_lines))),
                self._pg_text(e_lines),
                list(range(o_off, o_off + len(o_lines))),
                self._pg_text(o_lines),
                state,
                exitcode,
                now,
                summary_raw.replace("\x00", "") if summary_raw else None,
                history_count,
                events_count,
                log_count,
                org_pk,
                project_pk,
                name,
                gpu_type,
                gpu_count,
                [month for month, _ in accrued],
                [seconds for _, seconds in accrued],
                *([row[i] for row in chunk_rows] for i in range(5)),
                1 if chunk_rows else 0,
            )
            accrued_seconds = sum(seconds for _, seconds in accrued)
            changes = values.diff(
                {
                    "state": old_state,
                    "exitcode": old_exitcode,
                    "updated_at": old_updated,
                    "heartbeat_at": old_heartbeat,
                    "gpu_hours": values.gpu_hours(runtime, gpu_count),
                },
                {
                    "state": state,
                    "exitcode": exitcode,
                    "updated_at": now,
                    "heartbeat_at": now,
                    "gpu_hours": values.gpu_hours(runtime + accrued_seconds, gpu_count),
                },
            )
            if summary_data is not None:
                values.diff(previous, values.summary_columns(summary_data), into=changes)
            await values.write(conn, project_pk, [(pk, changes)])
            # Every replica's dashboards hear of it (D79): new points as line ranges, which
            # replicas with watchers read back, and the run's new state and summary.
            ends = {"history": history_count, "system": events_count}
            messages = [
                live.points_message(project_pk, pk, name, stream, start, ends[stream])
                for stream, start in fresh.items()
            ]
            summary_changed = {
                key: value for key, value in changes.values.items() if key.startswith("summary.")
            }
            if summary_changed or state != old_state:
                data: dict[str, Any] = {
                    "run": name,
                    "state": state,
                    "lines": history_count,
                    "updated_at": now,
                }
                if summary_changed:
                    data["summary"] = summary_changed
                messages.append(live.event_message(project_pk, name, "run", data))
            await live.publish(conn, messages)
        if state not in ACTIVE_STATES and old_state in ACTIVE_STATES:
            self._work_wanted.set()  # compact its chunks

    async def save_file(self, run: Run, name: str, data: bytes) -> None:
        if self.file_parts(name) is None:
            raise ValueError(f"invalid file name {name!r}")
        async with self.db.pool.acquire() as conn, conn.transaction():
            locked = await conn.fetchrow(self.LOCK_RUN, run.pk)
            if locked is None:
                raise NotFound(f"run {run.path} not found")
            # Written under the run's lock: a purge removes the files after the row.
            await self.files.put(run.pk, name, data)
            await conn.execute(
                "INSERT INTO run_files (run_pk, name, size, updated_at) "
                "VALUES ($1, $2, $3, to_timestamp($4)) "
                "ON CONFLICT (run_pk, name) DO UPDATE "
                "SET size = excluded.size, updated_at = excluded.updated_at",
                run.pk,
                name,
                len(data),
                time.time(),
            )
            if name == self.METADATA_FILE:
                await self._set_detected_gpus(conn, run, locked, *self._read_gpus(data))

    # The file the wandb SDK describes the machine in, GPUs included.
    METADATA_FILE: ClassVar[str] = "wandb-metadata.json"
    METADATA_MAX_BYTES: ClassVar[int] = 1 << 20

    @classmethod
    def _read_gpus(cls, data: bytes) -> tuple[str | None, int]:
        if len(data) > cls.METADATA_MAX_BYTES:
            return None, 0
        try:
            return gpus.detect(orjson.loads(data))
        except ValueError:
            return None, 0

    async def _set_detected_gpus(
        self,
        conn: asyncpg.Connection,
        run: Run,
        locked: Any,
        gpu_type: str | None,
        count: int,
    ) -> None:
        """Record the GPUs found in a run's metadata (``locked``: its ``LOCK_RUN`` row)."""
        state, _, updated_at, _, lines, *_ = locked
        old_type, old_count, runtime, _, detected_type, detected_count = locked[7:]
        if (detected_type, detected_count) == (gpu_type, count):
            return
        overrides = await conn.fetchrow(
            "SELECT gpu_type_override, gpu_count_override FROM runs WHERE pk = $1", run.pk
        )
        assert overrides is not None
        effective = (
            overrides[0] if overrides[0] is not None else gpu_type,
            overrides[1] if overrides[1] is not None else count,
        )
        await conn.execute(
            "WITH usage AS (UPDATE gpu_usage SET gpu_type = $4, gpu_count = $5 "
            "WHERE run_pk = $1) UPDATE runs SET gpu_type = $2, gpu_count = $3 WHERE pk = $1",
            run.pk,
            gpu_type,
            count,
            *effective,
        )
        await self._gpus_written(
            conn, run, (old_type, old_count), effective, runtime, (state, lines, updated_at)
        )

    async def _gpus_written(
        self,
        conn: asyncpg.Connection,
        run: Run,
        before: tuple[str | None, int | None],
        after: tuple[str | None, int | None],
        runtime: float,
        status: tuple[str, int, float],
    ) -> None:
        """Write a run's new effective GPUs to the run table, and tell dashboards."""
        if before == after:
            return
        old = {"gpu_type": before[0], "gpu_count": before[1]}
        new = {"gpu_type": after[0], "gpu_count": after[1]}
        old["gpu_hours"] = values.gpu_hours(runtime, before[1])
        new["gpu_hours"] = values.gpu_hours(runtime, after[1])
        await values.write(conn, run.project.pk, [(run.pk, values.diff(old, new))])
        state, lines, updated_at = status
        data = {
            "run": run.name,
            "state": state,
            "lines": lines,
            "updated_at": updated_at,
            "attributes": new,
        }
        await live.publish(conn, [live.event_message(run.project.pk, run.name, "run", data)])

    async def _detect_missing_gpus(self) -> None:
        """Read the GPUs of runs whose metadata was uploaded before GPUs were tracked (in the
        background: every replica may, and the second finds nothing left)."""
        try:
            records = await self.db.fetch(
                f"SELECT {RUN_COLUMNS}, {PROJECT_COLUMNS} FROM runs r "
                "JOIN projects p ON p.pk = r.project_pk JOIN orgs o ON o.pk = p.org_pk "
                "JOIN run_files f ON f.run_pk = r.pk AND f.name = $1 "
                "WHERE r.gpu_count IS NULL AND r.deleted_at IS NULL",
                self.METADATA_FILE,
            )
            runs = [Run.load(record, Project.load(record, 28)) for record in records]
            if not runs:
                return

            found = []
            for run in runs:
                try:
                    data = await self.read_file(run, self.METADATA_FILE) or b""
                except OSError:
                    data = b""
                found.append(self._read_gpus(data))

            for run, (gpu_type, count) in zip(runs, found, strict=True):
                async with self.db.pool.acquire() as conn, conn.transaction():
                    locked = await conn.fetchrow(self.LOCK_RUN, run.pk)
                    if locked is not None:
                        await self._set_detected_gpus(conn, run, locked, gpu_type, count)
            log.info("read the GPUs of %d runs from their metadata", len(runs))
        except Exception:
            log.exception("could not read the GPUs of older runs")

    async def mark_crashed_runs(self) -> list[str]:
        """Mark active runs whose client stopped sending heartbeats as crashed; returns their
        paths. Safe on every replica (D81): each stale run is claimed by one of them."""
        cutoff = time.time() - self.settings.crash_timeout
        async with self.db.pool.acquire() as conn, conn.transaction():
            records = await conn.fetch(
                """
                WITH stale AS (
                    SELECT pk FROM runs
                    WHERE state = ANY($1) AND deleted_at IS NULL
                        AND heartbeat_at < to_timestamp($2)
                    ORDER BY pk FOR UPDATE SKIP LOCKED
                )
                UPDATE runs r SET state = 'crashed' FROM stale, projects p, orgs o
                WHERE r.pk = stale.pk AND p.pk = r.project_pk AND o.pk = p.org_pk
                RETURNING r.pk, r.project_pk, r.name, r.history_line_count,
                    extract(epoch FROM r.updated_at)::float8, o.name, p.name
                """,
                list(ACTIVE_STATES),
                cutoff,
            )
            by_project: dict[int, list[tuple[int, values.Changes]]] = {}
            messages = []
            for pk, project_pk, name, lines, updated_at, *_ in records:
                changes = values.Changes()
                changes.set("state", "running", "crashed")
                by_project.setdefault(project_pk, []).append((pk, changes))
                data = {"run": name, "state": "crashed", "lines": lines, "updated_at": updated_at}
                messages.append(live.event_message(project_pk, name, "run", data))
            for project_pk, runs in sorted(by_project.items()):
                await values.write(conn, project_pk, runs)
            await live.publish(conn, messages)
        if records:
            self._work_wanted.set()
        return [f"{entity}/{project}/{name}" for _, _, name, _, _, entity, project in records]

    # ------------------------------------------------------------------ projects and edits

    # Run attributes editable through the API (not by the SDK alone).
    EDITABLE: ClassVar[tuple[str, ...]] = (
        "display_name",
        "notes",
        "tags",
        "group",
        "job_type",
        "description",
        "gpu_type",
        "gpu_count",
    )
    # Editable attributes that override detected values: attribute -> (field, SQL column).
    OVERRIDES: ClassVar[dict[str, str]] = {
        "gpu_type": "gpu_type_override",
        "gpu_count": "gpu_count_override",
    }

    async def _insert_project(
        self,
        conn: asyncpg.Connection,
        org: Org,
        name: str,
        description: str | None = None,
        created_at: float | None = None,
    ) -> Project | None:
        """Insert a project row (with ``org`` locked); None if the name is taken.

        ``created_at`` is for projects copied from elsewhere, which keep their creation time."""
        # Stamped with this process's clock, like runs: the database's can run ahead of it, which
        # would date a project created by a run's upload after that run started.
        record = await conn.fetchrow(
            "INSERT INTO projects (org_pk, name, description, created_at, updated_at) "
            "VALUES ($1, $2, $3, to_timestamp($4), to_timestamp($4)) "
            "ON CONFLICT (org_pk, name) WHERE deleted_at IS NULL DO NOTHING "
            "RETURNING pk, name, description, "
            "extract(epoch FROM created_at)::float8, extract(epoch FROM updated_at)::float8",
            org.pk,
            name,
            description,
            time.time() if created_at is None else created_at,
        )
        if record is None:
            return None
        pk, *rest = record
        return Project(pk, org, *rest)

    async def create_project(
        self, entity: str, name: str, description: str | None = None
    ) -> Project:
        org = await self.org(entity)
        async with self.db.pool.acquire() as conn, conn.transaction():
            await self._lock_org(conn, org)
            project = await self._insert_project(conn, org, name, description)
        if project is None:
            raise Conflict(f"project {entity}/{name} already exists")
        return project

    async def ensure_project(
        self,
        entity: str,
        name: str,
        description: str | None = None,
        created_at: float | None = None,
    ) -> Project:
        """The project ``entity/name``, created if it doesn't exist (as ``wandb.init`` does).

        Organizations are never created implicitly: an unknown ``entity`` is ``NotFound``.
        """
        while True:
            project = await self.project(entity, name)
            if project is not None:
                return project
            org = await self.org(entity)
            async with self.db.pool.acquire() as conn, conn.transaction():
                await self._lock_org(conn, org)
                project = await self._insert_project(conn, org, name, description, created_at)
            if project is not None:
                return project
            # Another request created it meanwhile: read that one.

    async def update_project(self, project: Project, fields: dict[str, Any]) -> Project:
        """Rename a project or change its description; returns it as it now is."""
        name = fields.get("name") or project.name
        renamed = name != project.name
        description = fields.get("description", project.description)
        now = time.time()
        async with self.db.pool.acquire() as conn, conn.transaction():
            await self._lock_org(conn, project.org)
            await self._lock_projects(conn, [project], update=True)
            if renamed:
                active = await self.active_runs(project)
                if active:
                    # Their clients address them by the old name in every request.
                    raise Conflict("cannot rename a project with active runs", active)
            try:
                await conn.execute(
                    "UPDATE projects SET name = $2, description = $3, "
                    "updated_at = to_timestamp($4) WHERE pk = $1",
                    project.pk,
                    name,
                    description,
                    now,
                )
            except asyncpg.UniqueViolationError:
                raise Conflict(f"project {project.entity}/{name} already exists") from None
            if renamed:
                # Dashboards address the project by name: tell them, then end their streams.
                event = {"renamed_to": name}
                await live.publish(
                    conn,
                    [
                        live.event_message(project.pk, None, "project", event),
                        live.close_message(project.pk),
                    ],
                )
        return Project(project.pk, project.org, name, description, project.created_at, now)

    async def delete_project(self, project: Project, force: bool = False) -> None:
        """Delete a project with all its runs; refused while runs are active unless ``force``.

        The delete commits at once; a background purge removes the rows and files (D80)."""
        now = time.time()
        async with self.db.pool.acquire() as conn, conn.transaction():
            await self._lock_org(conn, project.org)
            await self._lock_projects(conn, [project], update=True)
            await self._delete_projects(conn, [project], force, now)
        self._work_wanted.set()

    async def _delete_projects(
        self,
        conn: asyncpg.Connection,
        projects: Sequence[Project],
        force: bool,
        now: float,
        org: bool = False,
    ) -> None:
        """Mark ``projects`` (locked) and their runs deleted, telling their dashboards."""
        if not projects:
            return
        by_pk = {project.pk: project for project in projects}
        # Waits for writes to their runs in flight, so the states are the latest.
        runs = await conn.fetch(
            "UPDATE runs SET deleted_at = to_timestamp($2) "
            "WHERE project_pk = ANY($1) AND deleted_at IS NULL RETURNING project_pk, name, state",
            list(by_pk),
            now,
        )
        active = [(by_pk[pk], name) for pk, name, state in runs if state in ACTIVE_STATES]
        if active and not force:
            # An org's delete names them with their project.
            names = [f"{p.name}/{name}" if org else name for p, name in active]
            what = "organization" if org else "project"
            raise Conflict(f"{what} has active runs (pass force=true)", names)
        await self._mark_discarded(
            conn, [(project.entity, project.name, name) for project, name in active]
        )
        for project in projects:
            # Its ledger entries lose the project: keep which deleted project they were of.
            deleted = await conn.fetchval(
                "INSERT INTO deleted_projects (org_pk, name, deleted_at) "
                "SELECT $1, $2, to_timestamp($3) "
                "WHERE EXISTS (SELECT FROM gpu_usage WHERE project_pk = $4) RETURNING pk",
                project.org.pk,
                project.name,
                now,
                project.pk,
            )
            if deleted is not None:
                await conn.execute(
                    "UPDATE gpu_usage SET deleted_project_pk = $1 WHERE project_pk = $2",
                    deleted,
                    project.pk,
                )
        await conn.execute(
            "UPDATE projects SET deleted_at = to_timestamp($2) WHERE pk = ANY($1)",
            list(by_pk),
            now,
        )
        await live.publish(conn, [m for project in projects for m in self._closing(project)])

    @staticmethod
    def _closing(project: Project) -> list[str]:
        """Messages telling a deleted project's dashboards, then ending their streams."""
        event = live.event_message(project.pk, None, "project", {"deleted": True})
        return [event, live.close_message(project.pk)]

    # ------------------------------------------------------------------ imports

    async def import_project(
        self, org: Org, name: str, description: str | None, created_at: float | None
    ) -> Project:
        """The project ``name`` of ``org``, created with the given creation time if missing."""
        return await self.ensure_project(org.name, name, description, created_at)

    async def import_run(self, project: Project, data: ImportedRun) -> Run | None:
        """Add a finished run copied from elsewhere, with its timestamps, series, logs and
        files; None if the project already has a run of that name (so imports can resume).

        Series are stored as one chunk per metric straight away, and its lifetime is entered
        in the usage ledger as if it had sent heartbeats throughout.
        """
        if data.state in Run.ACTIVE_STATES:
            raise ValueError(f"cannot import a {data.state} run")
        for name in data.files:
            if self.file_parts(name) is None:
                raise ValueError(f"invalid file name {name!r}")
        gpu_type, gpu_count = None, None
        if self.METADATA_FILE in data.files:
            gpu_type, gpu_count = self._read_gpus(data.files[self.METADATA_FILE])
        series = {}
        for stream, lines in (("history", data.history), ("system", data.events)):
            hist = History()
            hist.append_rows(0, parse_rows(lines))
            series[stream] = hist
        heartbeat_at = max(data.heartbeat_at, data.created_at)
        ledger = gpus.split_by_month(data.created_at, heartbeat_at)
        history, events, logs = (
            self._pg_text(lines) for lines in (data.history, data.events, data.logs)
        )
        async with self.db.pool.acquire() as conn, conn.transaction():
            await self._lock_projects(conn, [project])
            pk = await conn.fetchval(
                """
                INSERT INTO runs (project_pk, name, display_name, state, created_at,
                    updated_at, heartbeat_at, config, summary, tags, notes, group_name,
                    job_type, host, program, commit_hash, sweep, exitcode,
                    history_line_count, events_line_count, log_line_count, owner_pk,
                    gpu_type, gpu_count, metric_defs)
                VALUES ($1, $2, $3, $4, to_timestamp($5), to_timestamp($6), to_timestamp($6),
                    $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21,
                    $22, $23, $24::jsonb)
                ON CONFLICT (project_pk, name) WHERE deleted_at IS NULL DO NOTHING
                RETURNING pk
                """,
                project.pk,
                data.name,
                data.display_name,
                data.state,
                data.created_at,
                heartbeat_at,
                orjson.dumps(data.config).decode() if data.config is not None else None,
                orjson.dumps(data.summary).decode() if data.summary is not None else None,
                data.tags,
                data.notes,
                data.group,
                data.job_type,
                data.host,
                data.program,
                data.commit,
                data.sweep,
                data.exitcode,
                len(history),
                len(events),
                len(logs),
                data.owner,
                gpu_type,
                gpu_count,
                orjson.dumps(parse_metric_defs(data.config) if data.config else []).decode(),
            )
            if pk is None:
                return None
            for table, lines in (("history", history), ("events", events), ("logs", logs)):
                if lines:
                    await conn.copy_records_to_table(
                        table,
                        records=[(pk, i, line) for i, line in enumerate(lines)],
                        columns=("run_pk", "line", "data"),
                    )
            for stream, hist in series.items():
                await write_chunks(conn, pk, stream, 0, hist.series)
            runtime = sum(seconds for _, seconds in ledger)
            imported = {
                "name": data.name,
                "display_name": data.display_name,
                "state": data.state,
                "created_at": data.created_at,
                "updated_at": heartbeat_at,
                "heartbeat_at": heartbeat_at,
                "tags": list(data.tags),
                "group": data.group,
                "job_type": data.job_type,
                "notes": data.notes,
                "host": data.host,
                "program": data.program,
                "commit": data.commit,
                "sweep": data.sweep,
                "exitcode": data.exitcode,
                "owner": data.owner,
                "gpu_type": gpu_type,
                "gpu_count": gpu_count,
                "gpu_hours": values.gpu_hours(runtime, gpu_count),
                **(values.config_columns(data.config) if data.config else {}),
                **(values.summary_columns(data.summary) if data.summary else {}),
            }
            await values.write(conn, project.pk, [(pk, values.diff({}, imported))])
            event = {"run": data.name, "display_name": data.display_name, "state": data.state}
            await live.publish(conn, [live.event_message(project.pk, None, "created", event)])
            if ledger:
                await conn.copy_records_to_table(
                    "gpu_usage",
                    records=[
                        (project.org.pk, project.pk, pk, data.name, month, seconds)
                        + (gpu_type, gpu_count)
                        for month, seconds in ledger
                    ],
                    columns=(
                        "org_pk",
                        "project_pk",
                        "run_pk",
                        "run_name",
                        "month",
                        "seconds",
                        "gpu_type",
                        "gpu_count",
                    ),
                )
            if data.files:
                # Written before the commit: if the commit fails, the purge removes the files
                # of a run that doesn't exist.
                for name, content in data.files.items():
                    await self.files.put(pk, name, content)
                await conn.executemany(
                    "INSERT INTO run_files (run_pk, name, size, updated_at) "
                    "VALUES ($1, $2, $3, to_timestamp($4))",
                    [
                        (pk, name, len(content), heartbeat_at)
                        for name, content in data.files.items()
                    ],
                )
        return await self.project_run(project, data.name)

    # ------------------------------------------------------------------ organizations

    async def create_org(self, name: str, display_name: str | None = None) -> Org:
        """Create the organization ``name`` (its ID, which never changes), shown as
        ``display_name`` (``name`` if not given)."""
        display_name = display_name if display_name is not None else name
        # Raising inside the transaction rolls the insert back.
        async with self.db.pool.acquire() as conn, conn.transaction():
            record = await conn.fetchrow(
                "INSERT INTO orgs (name, display_name, created_at, updated_at) "
                "VALUES ($1, $2, now(), now()) "
                "ON CONFLICT (name) WHERE deleted_at IS NULL DO NOTHING "
                "RETURNING pk, name, extract(epoch FROM created_at)::float8, "
                "extract(epoch FROM updated_at)::float8",
                name,
                display_name,
            )
            if record is None:
                raise Conflict(f"organization {name} already exists")
            # S3 bucket names start with the org's pk in five digits (D53).
            if record[0] > self.MAX_ORG_PK:
                raise Conflict("this server can't hold more organizations")
        return Org(*record, display_name=display_name)

    async def set_org_display_name(self, org: Org, display_name: str) -> None:
        """Change what an organization is called. Its ID (``name``), which its projects, runs
        and clients address it by, never changes."""
        if display_name == org.display_name:
            return
        found = await self.db.fetchval(
            "UPDATE orgs SET display_name = $2, updated_at = now() "
            "WHERE pk = $1 AND deleted_at IS NULL RETURNING pk",
            org.pk,
            display_name,
        )
        if found is None:
            raise NotFound(f"organization {org.name} not found")
        org.display_name = display_name

    async def delete_org(self, org: Org, force: bool = False) -> None:
        """Delete an organization with all its projects and runs; refused while runs are
        active unless ``force``. A background purge removes the rows and files (D80)."""
        # Its import stops first: it would wait on the locks taken below.
        await self.imports.cancel(org)
        now = time.time()
        async with self.db.pool.acquire() as conn, conn.transaction():
            await self._lock_org(conn, org, update=True)
            projects = await self.projects([org])
            await self._lock_projects(conn, projects, update=True)
            await self._delete_projects(conn, projects, force, now, org=True)
            # Its S3 buckets outlive it until the background purge removes them upstream, no
            # longer anyone's (as the org's row will be gone).
            await self.s3.delete_org_buckets(conn, org)
            await conn.execute("UPDATE s3_buckets SET org_pk = NULL WHERE org_pk = $1", org.pk)
            # Its avatar is released now: the row waits for the purge without it.
            await conn.execute(
                "UPDATE orgs SET deleted_at = to_timestamp($2), avatar = NULL WHERE pk = $1",
                org.pk,
                now,
            )
        self.s3.purge_later()
        await self.avatars.release(org.avatar)
        self._work_wanted.set()

    async def edit_runs(
        self,
        project: Project,
        runs: Sequence[Run],
        edits: dict[str, Any],
        add_tags: Sequence[str] = (),
        remove_tags: Sequence[str] = (),
    ) -> list[Run]:
        """Set ``edits`` (``EDITABLE`` attributes; None clears) on runs, and add or remove tags;
        returns the runs that still existed.

        Last write wins: a client that later sends tags or notes overwrites these edits.
        """
        if not runs:
            return []
        columns = {**self.FIELD_COLUMNS, **self.OVERRIDES}
        sets = {columns[key]: v for key, v in edits.items() if key != "tags"}
        assignments = "".join(f", {col} = ${i}" for i, col in enumerate(sets, start=4))
        now = time.time()
        # Edited fields by run attribute, overrides under their own name.
        fields = {self.OVERRIDES.get(key, key): v for key, v in edits.items() if key != "tags"}
        async with self.db.pool.acquire() as conn, conn.transaction():
            rows = await conn.fetch(
                "SELECT pk, display_name, notes, tags, group_name, job_type, description, "
                "extract(epoch FROM updated_at)::float8, gpu_type, gpu_count, "
                "gpu_type_override, gpu_count_override, state, history_line_count, "
                "(SELECT coalesce(sum(seconds), 0) FROM gpu_usage u WHERE u.run_pk = r.pk) "
                "FROM runs r WHERE pk = ANY($1) AND project_pk = $2 AND deleted_at IS NULL "
                "ORDER BY pk FOR UPDATE",
                [run.pk for run in runs],
                project.pk,
            )
            found = {row[0]: row for row in rows}
            runs = [run for run in runs if run.pk in found]
            if not runs:
                return []
            tags, effective, olds, news = [], [], [], []
            for run in runs:
                row = found[run.pk]
                current = (
                    [str(tag) for tag in edits.get("tags") or []]
                    if "tags" in edits
                    else list(row[3])
                )
                current = list(dict.fromkeys([*current, *add_tags]))
                tags.append([tag for tag in current if tag not in remove_tags])
                old = dict(
                    zip(
                        ("display_name", "notes", "tags", "group", "job_type", "description"),
                        row[1:7],
                        strict=True,
                    )
                )
                old["tags"] = list(old["tags"])
                old["gpu_type"] = row[10] if row[10] is not None else row[8]
                old["gpu_count"] = row[11] if row[11] is not None else row[9]
                old["gpu_hours"] = values.gpu_hours(row[14], old["gpu_count"])
                old["updated_at"] = row[7]
                # What each run's GPUs will be, for its usage ledger rows too.
                gpu_type = fields.get("gpu_type_override", row[10])
                count = fields.get("gpu_count_override", row[11])
                gpus_after = (
                    gpu_type if gpu_type is not None else row[8],
                    count if count is not None else row[9],
                )
                effective.append(gpus_after)
                new = {**old, **{k: v for k, v in fields.items() if k in old}}
                new["tags"] = tags[-1]
                new["gpu_type"], new["gpu_count"] = gpus_after
                new["gpu_hours"] = values.gpu_hours(row[14], gpus_after[1])
                new["updated_at"] = now
                olds.append(old)
                news.append(new)
            usage = ""
            if self.OVERRIDES.keys() & edits.keys():
                usage = (
                    "WITH usage AS (UPDATE gpu_usage u SET gpu_type = g.gpu_type, "
                    "gpu_count = g.gpu_count FROM unnest($1::bigint[], "
                    f"${len(sets) + 4}::text[], ${len(sets) + 5}::int[]) "
                    "AS g(pk, gpu_type, gpu_count) WHERE u.run_pk = g.pk) "
                )
            # One statement for the whole selection; each run's tags travel as JSON.
            await conn.execute(
                f"{usage}UPDATE runs SET tags = "
                "ARRAY(SELECT jsonb_array_elements_text(t.tags::jsonb)), "
                f"updated_at = to_timestamp($3){assignments} "
                "FROM unnest($1::bigint[], $2::text[]) AS t(pk, tags) WHERE runs.pk = t.pk",
                [run.pk for run in runs],
                [orjson.dumps(run_tags).decode() for run_tags in tags],
                now,
                *sets.values(),
                *([[t for t, _ in effective], [c for _, c in effective]] if usage else []),
            )
            await values.write(
                conn,
                project.pk,
                [
                    (run.pk, values.diff(old, new))
                    for run, old, new in zip(runs, olds, news, strict=True)
                ],
            )
            messages = []
            for run, old, new in zip(runs, olds, news, strict=True):
                row = found[run.pk]
                status = {"run": run.name, "state": row[12], "lines": row[13]}
                status["updated_at"] = now
                edited = ("display_name", "notes", "tags", "group", "job_type", "description")
                changed = {key: new[key] for key in edited if new[key] != old[key]}
                overrides = {"gpu_type": row[10], "gpu_count": row[11]}
                for attribute, field_name in self.OVERRIDES.items():
                    if field_name in fields and fields[field_name] != overrides[attribute]:
                        changed[attribute] = new[attribute]  # the effective value
                gpus = ("gpu_type", "gpu_count", "gpu_hours")
                if any(new[key] != old[key] for key in gpus):
                    attributes = {key: new[key] for key in gpus}
                    event = {**status, "attributes": attributes}
                    messages.append(live.event_message(project.pk, run.name, "run", event))
                    changed.pop("gpu_type", None)
                    changed.pop("gpu_count", None)
                if changed:
                    event = {**status, "attributes": changed}
                    messages.append(live.event_message(project.pk, run.name, "run", event))
            await live.publish(conn, messages)
        return runs

    # Deletes of at most this many runs are purged before they return, so their columns'
    # counts are right at once; larger ones in the background (D80).
    PURGE_INLINE: ClassVar[int] = 100

    async def delete_runs(
        self, project: Project, runs: Sequence[Run], force: bool = False
    ) -> list[str]:
        """Delete runs with everything they logged; active runs only with ``force``."""
        if not runs:
            return []
        now = time.time()
        async with self.db.pool.acquire() as conn, conn.transaction():
            await self._lock_projects(conn, [project])
            rows = await conn.fetch(
                "SELECT pk, name, state FROM runs WHERE pk = ANY($1) AND project_pk = $2 "
                "AND deleted_at IS NULL ORDER BY pk FOR UPDATE",
                [run.pk for run in runs],
                project.pk,
            )
            active = [name for _, name, state in rows if state in ACTIVE_STATES]
            if active and not force:
                order = {run.pk: i for i, run in enumerate(runs)}
                ordered = sorted(rows, key=lambda row: order[row[0]])
                active = [name for _, name, state in ordered if state in ACTIVE_STATES]
                raise Conflict("cannot delete active runs (pass force=true)", active)
            if not rows:
                return []
            pks = [row[0] for row in rows]
            await conn.execute(
                "UPDATE runs SET deleted_at = to_timestamp($2) WHERE pk = ANY($1)", pks, now
            )
            await self._mark_discarded(
                conn, [(project.entity, project.name, name) for name in active]
            )
            alive = {row[0] for row in rows}
            names = [run.name for run in runs if run.pk in alive]
            message = live.event_message(project.pk, None, "deleted", {"runs": names})
            await live.publish(conn, [message])
        if len(pks) <= self.PURGE_INLINE:
            await self.purge(pks)
        else:
            self._work_wanted.set()
        return names

    async def move_runs(self, source: Project, runs: Sequence[Run], target: Project) -> list[str]:
        """Move finished runs to another project, keeping everything they logged."""
        if target.pk == source.pk or not runs:
            return []
        async with self.db.pool.acquire() as conn, conn.transaction():
            await self._lock_projects(conn, [source, target])
            rows = await conn.fetch(
                "SELECT pk, name, state, display_name FROM runs WHERE pk = ANY($1) "
                "AND project_pk = $2 AND deleted_at IS NULL ORDER BY pk FOR UPDATE",
                [run.pk for run in runs],
                source.pk,
            )
            order = {run.pk: i for i, run in enumerate(runs)}
            rows = sorted(rows, key=lambda row: order[row[0]])
            if not rows:
                return []
            active = [name for _, name, state, _ in rows if state in ACTIVE_STATES]
            if active:
                # Their clients address them by the old project in every request.
                raise Conflict("cannot move active runs", active)
            names = [name for _, name, _, _ in rows]
            clashes = await conn.fetch(
                "SELECT name FROM runs WHERE project_pk = $1 AND name = ANY($2) "
                "AND deleted_at IS NULL",
                target.pk,
                names,
            )
            if clashes:
                taken = {name for (name,) in clashes}
                raise Conflict(
                    f"{target.name} already has runs with these ids",
                    [name for name in names if name in taken],
                )
            pks = [row[0] for row in rows]
            await values.shift(conn, pks, source.pk, target.pk)
            await conn.execute(
                "WITH usage AS (UPDATE gpu_usage SET project_pk = $2 WHERE run_pk = ANY($1)),"
                " moved AS (UPDATE run_values SET project_pk = $2 WHERE run_pk = ANY($1)) "
                "UPDATE runs SET project_pk = $2 WHERE pk = ANY($1)",
                pks,
                target.pk,
            )
            gone = {"runs": names, "moved_to": target.name}
            messages = [live.event_message(source.pk, None, "deleted", gone)]
            for _, name, state, display_name in rows:
                event = {"run": name, "display_name": display_name, "state": state}
                messages.append(live.event_message(target.pk, None, "created", event))
            await live.publish(conn, messages)
        return names

    # ------------------------------------------------------------------ deleted while logging

    async def discarding(self, entity: str, project: str, name: str) -> bool:
        """Whether writes to this run come from the client of a run deleted while it was running.

        That client keeps sending (wandb even re-creates its run with UpsertBucket when it
        finishes); its writes are acknowledged and dropped. The mark lasts until the client has
        been quiet for ``crash_timeout``, after which the run id is free again.
        """
        found = await self.db.fetchval(
            "UPDATE discarded_runs SET last_seen = now() "
            "WHERE entity = $1 AND project = $2 AND name = $3 "
            "AND last_seen > now() - make_interval(secs => $4) RETURNING true",
            entity,
            project,
            name,
            self.settings.crash_timeout,
        )
        return bool(found)

    @staticmethod
    async def _mark_discarded(
        conn: asyncpg.Connection, runs: Sequence[tuple[str, str, str]]
    ) -> None:
        """Note runs deleted while active (D80's ``discarded_runs``)."""
        if runs:
            await conn.execute(
                "INSERT INTO discarded_runs (entity, project, name, last_seen) "
                "SELECT entity, project, name, now() "
                "FROM unnest($1::text[], $2::text[], $3::text[]) AS t(entity, project, name) "
                "ON CONFLICT (entity, project, name) DO UPDATE SET last_seen = now()",
                [run[0] for run in runs],
                [run[1] for run in runs],
                [run[2] for run in runs],
            )

    # ------------------------------------------------------------------ the purge (D80, D81)

    PURGE_BATCH: ClassVar[int] = 200

    async def purge(self, pks: Sequence[int] | None = None) -> int:
        """Remove deleted runs (``pks``, or a batch of any) with what they logged, then
        deleted projects and orgs left empty; returns how many runs. Runs are claimed, so
        replicas purging at once don't wait on each other."""
        async with self.db.pool.acquire() as conn, conn.transaction():
            rows = await conn.fetch(
                "SELECT r.pk, r.project_pk, p.deleted_at IS NULL FROM runs r "
                "JOIN projects p ON p.pk = r.project_pk "
                "WHERE r.deleted_at IS NOT NULL AND ($1::bigint[] IS NULL OR r.pk = ANY($1)) "
                "ORDER BY r.pk LIMIT $2 FOR UPDATE OF r SKIP LOCKED",
                None if pks is None else list(pks),
                self.PURGE_BATCH if pks is None else len(pks),
            )
            by_project: dict[int, list[int]] = {}
            for pk, project_pk, project_alive in rows:
                if project_alive:  # a deleted project's counts go with it
                    by_project.setdefault(project_pk, []).append(pk)
            for project_pk, project_pks in sorted(by_project.items()):
                await values.shift(conn, project_pks, project_pk, None)
            purged = [row[0] for row in rows]
            if purged:
                await conn.execute("DELETE FROM runs WHERE pk = ANY($1)", purged)
            if pks is None:
                await conn.execute(
                    "DELETE FROM projects p WHERE p.deleted_at IS NOT NULL "
                    "AND NOT EXISTS (SELECT FROM runs r WHERE r.project_pk = p.pk)"
                )
                await conn.execute(
                    "DELETE FROM orgs o WHERE o.deleted_at IS NOT NULL "
                    "AND NOT EXISTS (SELECT FROM projects p WHERE p.org_pk = o.pk)"
                )
        if purged:
            await self._remove_files(purged)
        return len(purged)

    async def _remove_files(self, pks: Iterable[int]) -> None:
        await self.files.delete_runs(pks)

    # How often each replica looks for run files without a run (a crash between a purge's
    # commit and its file removal): listing every run's directory isn't free.
    FILE_SWEEP_INTERVAL: ClassVar[float] = 3600.0

    async def _sweep_files(self) -> int:
        """Remove the files of runs that don't exist; returns of how many."""
        dirs = await self.files.runs()
        if not dirs:
            return 0
        existing = {
            pk for (pk,) in await self.db.fetch("SELECT pk FROM runs WHERE pk = ANY($1)", dirs)
        }
        orphans = [pk for pk in dirs if pk not in existing]
        await self._remove_files(orphans)
        return len(orphans)

    async def _cleanup(self) -> None:
        """Expired rows nothing reads any more (D81: idempotent, so every replica runs it)."""
        await self.db.execute(
            "DELETE FROM discarded_runs WHERE last_seen < now() - make_interval(secs => $1)",
            self.settings.crash_timeout,
        )
        # Large notifications are read as soon as they are sent: a minute is plenty.
        await self.db.execute(
            "DELETE FROM bus_messages WHERE created_at < now() - interval '1 minute'"
        )
        await self.accounts.sweep()
        await self.imports.sweep()

    # ------------------------------------------------------------------ series

    # Every chunk of the runs $1 in stream $2, of the keys $3 (all if NULL), oldest first, and
    # the names of the runs' other metrics (with NULL chunks).
    HISTORIES: ClassVar[str] = """
        SELECT m.run_pk, m.key, c.first_line, c.n, c.data
        FROM run_metrics m
        LEFT JOIN series_chunks c ON c.run_pk = m.run_pk AND c.stream = m.stream
            AND c.key = m.key AND ($3::text[] IS NULL OR m.key = ANY($3))
        WHERE m.run_pk = ANY($1) AND m.stream = $2
        ORDER BY m.run_pk, m.key, c.first_line
    """

    # The chunks of the keys $3 of the runs $1 in stream $2, oldest first.
    CHUNKS: ClassVar[str] = """
        SELECT run_pk, key, first_line, n, data FROM series_chunks
        WHERE run_pk = ANY($1) AND stream = $2 AND key = ANY($3)
        ORDER BY run_pk, key, first_line
    """

    async def histories(
        self, runs: Sequence[Run], keys: Iterable[str] | None = None, stream: str = "history"
    ) -> list[History]:
        """Series of ``runs`` in ``stream`` ("history" or "system"), with ``keys`` loaded
        (every metric when None), from their chunks in one query (D78)."""
        if stream not in self.STREAMS:
            raise ValueError(f"unknown series stream {stream!r}")
        wanted = None if keys is None else list(dict.fromkeys(keys))
        pks = [run.pk for run in runs]
        if wanted:  # only these: the other metrics' names aren't needed
            records = await self.db.fetch(self.CHUNKS, pks, stream, wanted)
        else:
            records = await self.db.fetch(self.HISTORIES, pks, stream, wanted)
        chunks: dict[int, dict[str, list[Series]]] = {}
        for run_pk, key, first_line, n, data in records:
            parts = chunks.setdefault(run_pk, {}).setdefault(key, [])
            if data is not None:
                parts.append(Series.decode(data, n, first_line))
        out = []
        for run in runs:
            found = chunks.get(run.pk, {})
            hist = History(
                run.lines(stream),
                (key for key, parts in found.items() if not parts and wanted is not None),
            )
            for key, parts in found.items():
                if parts:
                    hist.series[key] = Series.concat(parts)
            out.append(hist)
        return out

    # ------------------------------------------------------------------ compaction (D78, D81)

    # A run whose chunks need merging: a finished one with any appended, or an active one with
    # COMPACT_AFTER of them. Claimed so that only one replica merges it, and locked like a write.
    CLAIM_COMPACTION: ClassVar[str] = """
        SELECT pk FROM runs
        WHERE uncompacted > 0 AND deleted_at IS NULL
            AND (state NOT IN ('running', 'preempting') OR uncompacted >= $1)
        ORDER BY pk LIMIT 1
        FOR UPDATE SKIP LOCKED
    """
    COMPACT_AFTER: ClassVar[int] = 64
    # How often each replica looks for background work, when nothing wakes it sooner.
    WORK_INTERVAL: ClassVar[float] = 5.0

    async def compact(self) -> bool:
        """Merge the chunks of one run that needs it into one chunk per metric; returns
        whether there was one."""
        async with self.db.pool.acquire() as conn, conn.transaction():
            pk = await conn.fetchval(self.CLAIM_COMPACTION, self.COMPACT_AFTER)
            if pk is None:
                return False
            groups: dict[tuple[str, str], list[tuple[int, Series]]] = {}
            for stream, key, first_line, n, data in await conn.fetch(
                "SELECT stream, key, first_line, n, data FROM series_chunks WHERE run_pk = $1 "
                "ORDER BY stream, key, first_line",
                pk,
            ):
                groups.setdefault((stream, key), []).append(
                    (first_line, Series.decode(data, n, first_line))
                )
            merged = [(stream, key, parts) for (stream, key), parts in groups.items()]
            merged = [item for item in merged if len(item[2]) > 1]
            if merged:
                series = [Series.concat([part for _, part in parts]) for *_, parts in merged]
                firsts = [parts[0][0] for *_, parts in merged]
                streams, keys = [stream for stream, _, _ in merged], [key for _, key, _ in merged]
                # Two statements: one statement's insert would still see the rows it deletes.
                await conn.execute(
                    "DELETE FROM series_chunks c USING unnest($2::text[], $3::text[]) "
                    "AS t(stream, key) WHERE c.run_pk = $1 AND c.stream = t.stream "
                    "AND c.key = t.key",
                    pk,
                    streams,
                    keys,
                )
                await conn.execute(
                    """
                    INSERT INTO series_chunks (run_pk, stream, key, first_line, n, data)
                    SELECT $1, stream, key, first_line, n, data
                    FROM unnest($2::text[], $3::text[], $4::int[], $5::int[], $6::bytea[])
                        AS t(stream, key, first_line, n, data)
                    """,
                    pk,
                    streams,
                    keys,
                    firsts,
                    [item.n for item in series],
                    [item.encode(first) for item, first in zip(series, firsts, strict=True)],
                )
            await conn.execute("UPDATE runs SET uncompacted = 0 WHERE pk = $1", pk)
        return True

    # Column count changes that make a round's writes a bulk load (thousands of new runs).
    BULK_CHANGES: ClassVar[int] = 100_000

    async def _after_bulk_load(self) -> None:
        """Clean up after a bulk load without waiting for autovacuum: the folded changes leave
        dead rows that every column read would wade through, filters and the budget panel need
        statistics that cover the new runs (without them, they read every run row by row), and
        index-only scans need the new rows marked all-visible (else each one reads the table)."""
        await self.db.execute(
            "VACUUM (ANALYZE) project_key_changes, project_keys, runs, run_values, gpu_usage"
        )

    async def work(self, sweep_files: bool = False) -> None:
        """One round of background work (D81), until there is none left: fold column count
        changes, compact chunks, purge deletes and remove expired rows (and, now and then or
        with ``sweep_files``, orphaned run files)."""
        folded = 0
        while count := await values.fold(self.db.pool):
            folded += count
        if folded >= self.BULK_CHANGES:
            await self._after_bulk_load()
        while await self.compact():
            pass
        while await self.purge():
            pass
        await self._cleanup()
        if sweep_files or time.monotonic() - self._files_swept > self.FILE_SWEEP_INTERVAL:
            self._files_swept = time.monotonic()
            if removed := await self._sweep_files():
                log.info("removed files of %d deleted runs", removed)

    async def _work_loop(self) -> None:
        """Every replica does background work every ``WORK_INTERVAL``, or sooner when one of
        its requests leaves some."""
        while True:
            self._work_wanted.clear()
            try:
                await self.work()
            except Exception:
                log.exception("background work failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._work_wanted.wait(), self.WORK_INTERVAL)

    async def flush(self) -> None:
        """Wait for background tasks, and do all background work there is (tests)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
        await self.accounts.flush()
        await self.work(sweep_files=True)
