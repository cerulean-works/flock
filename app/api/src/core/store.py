import asyncio
import contextlib
import datetime as dt
import logging
import secrets
import shutil
import string
import time
from collections import OrderedDict
from collections.abc import AsyncGenerator, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

import numpy as np
import orjson

from app.api.src.core import gpus
from app.api.src.core.accounts import Accounts
from app.api.src.core.avatars import Avatars
from app.api.src.core.budgets import Budgets, DeletedProject, Dismissal, Usage
from app.api.src.core.errors import Conflict, Discarded, Forbidden, NotFound
from app.api.src.core.feed import Feed
from app.api.src.core.history import History, PointCounter, Series
from app.api.src.core.imports import Imports
from app.api.src.core.live import Hub
from app.api.src.core.metric_defs import parse_metric_defs
from app.api.src.core.names import ADJECTIVES, NOUNS
from app.api.src.core.s3 import S3
from app.api.src.core.table import ColumnTable
from app.api.src.db.database import Database
from app.api.src.settings import Settings
from app.api.src.utils import encode_id, flatten, loads_lenient

log = logging.getLogger(__name__)

DEFAULT_PROJECT = "uncategorized"


@dataclass(eq=False)
class Run:
    # States in which a client is still logging to the run.
    ACTIVE_STATES: ClassVar[frozenset[str]] = frozenset({"running", "preempting"})
    # Attributes stored in the run table alongside config.* and summary.* columns.
    ATTRIBUTES: ClassVar[tuple[str, ...]] = (
        "name",
        "display_name",
        "state",
        "created_at",
        "updated_at",
        "heartbeat_at",
        "tags",
        "group",
        "job_type",
        "notes",
        "description",
        "host",
        "program",
        "commit",
        "sweep",
        "exitcode",
        "owner",
        "gpu_type",
        "gpu_count",
        "gpu_hours",
    )

    pk: int
    project: Project
    name: str
    row: int
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
    # Seconds alive per UTC month: the run's entries in its org's usage ledger.
    usage: dict[dt.date, Usage] = field(default_factory=dict)
    history_line_count: int = 0
    events_line_count: int = 0
    log_line_count: int = 0
    # Per series stream ("history", "system"): lines covered by the columnar copy in
    # history_columns, and the metrics it holds, so a compacted stream can load lazily.
    compacted: dict[str, int] = field(default_factory=lambda: {"history": 0, "system": 0})
    compacted_keys: dict[str, list[str]] = field(
        default_factory=lambda: {"history": [], "system": []}
    )
    # Flattened summary of an active run, kept to diff successive summary updates.
    summary_flat: dict[str, Any] | None = None
    # What ``wandb.define_metric`` set for plotting: step metrics and hidden metrics.
    metric_defs: list[dict[str, Any]] = field(default_factory=list)
    # Plottable metrics this run logged, per stream (dashboards chart those of a page's runs).
    keys: dict[str, set[str]] = field(default_factory=lambda: {"history": set(), "system": set()})
    # Set once the run's delete commits: code still holding the run must not write to it.
    deleted: bool = False

    @property
    def active(self) -> bool:
        return self.state in self.ACTIVE_STATES

    def note_metrics(self, keys: Iterable[str], stream: str = "history") -> None:
        """Record metrics the run logged to a series stream."""
        # Skip wandb bookkeeping (_step, _runtime, _wandb, ...), which every row carries.
        self.keys[stream].update(key for key in keys if not key.startswith("_"))

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
    def runtime(self) -> float:
        """Seconds the run was alive (sending heartbeats), over all its resumes."""
        return sum(usage.seconds for usage in self.usage.values())

    @property
    def gpu_hours(self) -> float | None:
        count = self.gpu_count
        return round(self.runtime * count / 3600, 6) if count is not None else None

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
    """An organization: the wandb entity its projects and runs are logged under.

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
        # GPU prices: lowercased type -> (type as set, hourly cost of one GPU).
        self.rates: dict[str, tuple[str, float]] = {}
        # Monthly budgets, by the month they apply from (None: no budget), in month order.
        self.budgets: list[tuple[dt.date, float | None]] = []
        # The usage ledger, by month, including entries of deleted runs and projects.
        self.usage: dict[dt.date, list[Usage]] = {}
        # Deleted projects with ledger entries, by pk (see DeletedProject).
        self.deleted_projects: dict[int, DeletedProject] = {}
        # Budget warnings dismissed for everyone, by (month, kind).
        self.dismissals: dict[tuple[dt.date, str], Dismissal] = {}
        self.deleted = False


class Project:
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
        # Indexed by table row: runs are kept in (created_at, pk) order.
        self.runs: list[Run] = []
        self.by_name: dict[str, Run] = {}
        self.table = ColumnTable()
        self.active: dict[str, Run] = {}
        # Held to create runs, move runs in or out, delete runs, and rename or delete the project.
        self.lock = asyncio.Lock()
        self.deleted = False

    @property
    def entity(self) -> str:
        return self.org.name

    @property
    def key(self) -> tuple[str, str]:
        return (self.entity, self.name)

    def add(self, run: Run) -> None:
        self.runs.append(run)
        self.by_name[run.name] = run

    def info(self) -> dict[str, Any]:
        return {
            "entity": self.entity,
            "name": self.name,
            "description": self.description,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "run_count": len(self.runs),
            "active_run_count": len(self.active),
            "column_count": len(self.table.columns),
        }


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
    # Tables holding one row per line of a wandb file.
    LINE_TABLES: ClassVar[frozenset[str]] = frozenset({"history", "events", "logs"})
    # Plottable series streams -> the table holding their raw lines.
    STREAMS: ClassVar[dict[str, str]] = {"history": "history", "system": "events"}
    # Org pks stay below 100000 so S3 bucket names have room for them (D53).
    MAX_ORG_PK: ClassVar[int] = 99999

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.files_dir = settings.files_dir
        self.db = Database(settings.database_url, max_size=settings.pool_size)
        self.orgs: dict[str, Org] = {}
        self.orgs_by_pk: dict[int, Org] = {}
        self.projects: dict[tuple[str, str], Project] = {}
        self.projects_by_pk: dict[int, Project] = {}
        self.runs_by_pk: dict[int, Run] = {}
        self.active: dict[int, Run] = {}
        self._locks: dict[int, asyncio.Lock] = {}  # per run, by pk
        # Serializes changes to which name maps to which org, and (entity, name) to which project.
        self._catalog = asyncio.Lock()
        # Runs deleted while a client was still logging to them: (entity, project, run) -> when
        # that client was last heard from (see ``discarding``).
        self._deleted_live: dict[tuple[str, str, str], float] = {}
        # Series of active and recently read runs, keyed by (run pk, stream), in LRU order.
        self._history: OrderedDict[tuple[int, str], History] = OrderedDict()
        self._history_points = PointCounter()
        self._rebuilding: dict[tuple[int, str], asyncio.Future[History]] = {}
        self._tasks: set[asyncio.Task] = set()
        self.live = Hub()
        self.secret = b""
        self.accounts = Accounts(self)
        self.budgets = Budgets(self)
        self.avatars = Avatars(self)
        self.feed = Feed(self)
        self.imports = Imports(self)
        self.s3 = S3(self)

    @staticmethod
    def _config_columns(config: dict[str, Any]) -> dict[str, Any]:
        """Flatten a wandb config (``{"lr": {"value": 0.1}}``) into ``config.*`` columns."""
        out: dict[str, Any] = {}
        for key, item in config.items():
            if key == "_wandb":
                continue
            value = item["value"] if isinstance(item, dict) and "value" in item else item
            if isinstance(value, dict) and "_type" not in value and value:
                flatten(value, f"config.{key}.", out)
            else:
                out[f"config.{key}"] = value
        return out

    @staticmethod
    def _summary_columns(summary: dict[str, Any]) -> dict[str, Any]:
        return flatten({k: v for k, v in summary.items() if k != "_wandb"}, "summary.")

    @staticmethod
    def _pg_text(lines: list[str]) -> list[str]:
        """Postgres text cannot hold NUL bytes, which raw console output occasionally contains."""
        return [line.replace("\x00", "") if "\x00" in line else line for line in lines]

    @staticmethod
    def _parse_rows(lines: Iterable[str]) -> list[dict]:
        """History lines as dicts; unparseable lines become empty rows so line numbers hold."""
        rows = []
        for line in lines:
            try:
                row = loads_lenient(line)
            except ValueError:
                row = None
            rows.append(row if isinstance(row, dict) else {})
        return rows

    # ------------------------------------------------------------------ lifecycle

    async def open(self) -> None:
        """Connect, check the schema is migrated and load every run into memory."""
        self.files_dir.mkdir(parents=True, exist_ok=True)
        await self.db.open(migrate=self.settings.migrate_on_startup)
        try:
            secret = await self.db.fetchval("SELECT value FROM meta WHERE key = 'upload_secret'")
            self.secret = bytes.fromhex(secret)
            await self._load()
            await self.accounts.load()
            await self.budgets.load()
            await self.imports.load()
            await self.s3.load()
            await self._detect_missing_gpus()
        except BaseException:
            await self.db.close()
            raise

    async def close(self) -> None:
        await self.s3.close()
        await self.imports.close()
        await self.avatars.flush()
        await self.flush()
        await self.db.close()

    async def _load(self) -> None:
        started = time.perf_counter()
        for record in await self.db.fetch(
            "SELECT pk, name, extract(epoch FROM created_at)::float8, "
            "extract(epoch FROM updated_at)::float8, currency, avatar, display_name, "
            "s3_ephemeral_ttl, s3_key_max_ttl FROM orgs"
        ):
            *fields, avatar, display_name, ephemeral_ttl, max_ttl = record
            avatar = bytes(avatar) if avatar else None
            self._add_org(
                Org(
                    *fields,
                    avatar=avatar,
                    display_name=display_name,
                    s3_ephemeral_ttl=ephemeral_ttl,
                    s3_key_max_ttl=max_ttl,
                )
            )
        for pk, org_pk, *rest in await self.db.fetch(
            "SELECT pk, org_pk, name, description, extract(epoch FROM created_at)::float8, "
            "extract(epoch FROM updated_at)::float8 FROM projects"
        ):
            self._add_project(Project(pk, self.orgs_by_pk[org_pk], *rest))
        pending: dict[int, dict[str, tuple[list[int], list[Any]]]] = {}
        query = """
            SELECT pk, project_pk, name, display_name, state,
                   extract(epoch FROM created_at)::float8, extract(epoch FROM updated_at)::float8,
                   extract(epoch FROM heartbeat_at)::float8, tags, notes, group_name, job_type,
                   host, program, commit_hash, description, sweep, exitcode, history_line_count,
                   events_line_count, log_line_count, history_compacted_line_count,
                   system_compacted_line_count, config, summary, owner_pk, gpu_type, gpu_count,
                   gpu_type_override, gpu_count_override
            FROM runs
            ORDER BY created_at, pk
        """
        async with self.db.pool.acquire() as conn, conn.transaction():
            async for record in conn.cursor(query, prefetch=1000):
                project = self.projects_by_pk[record[1]]
                run = Run(
                    pk=record[0],
                    project=project,
                    name=record[2],
                    row=project.table.add_row(),
                    display_name=record[3],
                    state=record[4],
                    created_at=record[5],
                    updated_at=record[6],
                    heartbeat_at=record[7],
                    tags=list(record[8]),
                    notes=record[9],
                    group=record[10],
                    job_type=record[11],
                    host=record[12],
                    program=record[13],
                    commit=record[14],
                    description=record[15],
                    sweep=record[16],
                    exitcode=record[17],
                    history_line_count=record[18],
                    events_line_count=record[19],
                    log_line_count=record[20],
                    compacted={"history": record[21], "system": record[22]},
                    owner=record[25],
                    gpu_type_detected=record[26],
                    gpu_count_detected=record[27],
                    gpu_type_override=record[28],
                    gpu_count_override=record[29],
                )
                project.add(run)
                self.runs_by_pk[run.pk] = run
                values = run.attributes()
                config_raw, summary_raw = record[23], record[24]
                if config_raw:
                    config = loads_lenient(config_raw)
                    values.update(self._config_columns(config))
                    run.metric_defs = parse_metric_defs(config)
                if summary_raw:
                    values.update(self._summary_columns(loads_lenient(summary_raw)))
                columns = pending.setdefault(project.pk, {})
                for name, value in values.items():
                    if value is None:
                        continue
                    bucket = columns.get(name)
                    if bucket is None:
                        bucket = columns[name] = ([], [])
                    bucket[0].append(run.row)
                    bucket[1].append(value)
                if run.active:
                    self._mark_active(run, project)
        for project_pk, columns in pending.items():
            table = self.projects_by_pk[project_pk].table
            for name, (rows, vals) in columns.items():
                table.bulk_set(name, rows, vals)
        for pk, stream, keys in await self.db.fetch(
            "SELECT run_pk, stream, array_agg(key) FROM history_columns GROUP BY run_pk, stream"
        ):
            run = self.runs_by_pk[pk]
            run.compacted_keys[stream] = list(keys)
            run.note_metrics(keys, stream)
        # Active runs are always kept in memory. Streams that stopped before being compacted
        # (a crash, or system metrics logged before migration 0002) are rebuilt so their metric
        # keys are known, one run at a time so memory stays bounded while they compact.
        for stream in self.STREAMS:
            for run in list(self.runs_by_pk.values()):
                if run.active or run.compacted[stream] < run.lines(stream):
                    (hist,) = await self.histories([run], (), stream)
                    run.note_metrics(hist.series, stream)
                    await self.flush()

        # Files of runs whose delete committed but whose files were not removed (a crash).
        def sweep() -> int:
            orphans = [
                path
                for path in self.files_dir.iterdir()
                if path.is_dir() and path.name.isdigit() and int(path.name) not in self.runs_by_pk
            ]
            for path in orphans:
                shutil.rmtree(path, ignore_errors=True)
            return len(orphans)

        if removed := await asyncio.to_thread(sweep):
            log.info("removed files of %d deleted runs", removed)
        log.info(
            "loaded %d runs in %d projects in %.2fs",
            len(self.runs_by_pk),
            len(self.projects),
            time.perf_counter() - started,
        )

    # ------------------------------------------------------------------ lookups

    def project(self, entity: str, name: str) -> Project | None:
        return self.projects.get((entity, name))

    def get_run(self, entity: str, project: str, name: str) -> Run | None:
        proj = self.projects.get((entity, project))
        return proj.by_name.get(name) if proj is not None else None

    def _add_org(self, org: Org) -> Org:
        self.orgs[org.name] = org
        self.orgs_by_pk[org.pk] = org
        return org

    def _org_projects(self, org: Org) -> list[Project]:
        return [project for project in self.projects_by_pk.values() if project.org is org]

    def _add_project(self, project: Project) -> Project:
        self.projects[project.key] = project
        self.projects_by_pk[project.pk] = project
        return project

    def _run_lock(self, run: Run) -> asyncio.Lock:
        lock = self._locks.get(run.pk)
        if lock is None:
            lock = self._locks[run.pk] = asyncio.Lock()
        return lock

    @contextlib.asynccontextmanager
    async def _locked(
        self, projects: Iterable[Project], runs: Iterable[Run] = (), all_runs: bool = False
    ) -> AsyncGenerator[None]:
        """Hold the projects' locks, then the runs' locks, each in pk order, so that lockers
        never deadlock. ``all_runs`` locks every run of the projects, listed once they are
        locked (no run can be created or moved in meanwhile)."""
        projects = sorted(set(projects), key=lambda p: p.pk)
        async with contextlib.AsyncExitStack() as stack:
            for project in projects:
                await stack.enter_async_context(project.lock)
            if all_runs:
                runs = [run for project in projects for run in project.runs]
            for run in sorted(set(runs), key=lambda r: r.pk):
                await stack.enter_async_context(self._run_lock(run))
            yield

    def org(self, name: str) -> Org:
        org = self.orgs.get(name)
        if org is None:
            raise NotFound(f"organization {name} not found")
        return org

    async def _insert_project(
        self, org: Org, name: str, description: str | None = None, created_at: float | None = None
    ) -> Project | None:
        """Insert a project row (call with ``_catalog`` held); None if the name is taken.

        ``created_at`` is for projects copied from elsewhere, which keep their creation time."""
        # Stamped with this process's clock, like runs: the database's can run ahead of it, which
        # would date a project created by a run's upload after that run started.
        record = await self.db.fetchrow(
            "INSERT INTO projects (org_pk, name, description, created_at, updated_at) "
            "VALUES ($1, $2, $3, to_timestamp($4), to_timestamp($4)) "
            "ON CONFLICT (org_pk, name) DO NOTHING "
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
        return self._add_project(Project(pk, org, *rest))

    async def ensure_project(self, entity: str, name: str) -> Project:
        """The project ``entity/name``, created if it doesn't exist (as ``wandb.init`` does).

        Organizations are never created implicitly: an unknown ``entity`` is ``NotFound``.
        """
        project = self.projects.get((entity, name))
        if project is not None:
            return project
        async with self._catalog:
            project = self.projects.get((entity, name))
            if project is None:
                project = await self._insert_project(self.org(entity), name)
                assert project is not None  # only created under _catalog, and it was missing
            return project

    @contextlib.asynccontextmanager
    async def _writable_project(self, entity: str, name: str) -> AsyncGenerator[Project]:
        """The project ``entity/name`` (created if missing), with its lock held."""
        while True:
            project = await self.ensure_project(entity, name)
            async with project.lock:
                if project.deleted or project.key != (entity, name):
                    continue  # deleted or renamed while we waited: resolve the name again
                yield project
                return

    def _mark_active(self, run: Run, project: Project) -> None:
        self.active[run.pk] = run
        project.active[run.name] = run

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
        parts = self.file_parts(name)
        return None if parts is None else self.files_dir / str(run.pk) / Path(*parts)

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
        if self.discarding(entity, project_name, name):
            raise Discarded(project_name, name)
        async with self._writable_project(entity, project_name) as project:
            run = project.by_name.get(name)
            if run is None:
                return await self._create_run(project, name, fields, owner), True
            self._check_owner(run, owner, admin)
            async with self._run_lock(run):
                await self._update_run(run, fields)
            return run, False

    @staticmethod
    def _check_owner(run: Run, owner: int | None, admin: bool) -> None:
        if not admin and run.owner != owner:
            raise Forbidden(f"run {run.path} belongs to another user", [run.name])

    async def _create_run(
        self, project: Project, name: str, fields: dict[str, Any], owner: int | None = None
    ) -> Run:
        now = time.time()
        # wandb-style "adjective-noun-N" unless the client named the run.
        display_name = (
            fields.get("display_name")
            or f"{secrets.choice(ADJECTIVES)}-{secrets.choice(NOUNS)}-{len(project.runs) + 1}"
        )
        values = {col: fields.get(key) for key, col in self.FIELD_COLUMNS.items()}
        values["display_name"] = display_name
        values["state"] = fields.get("state") or "running"
        tags = [str(tag) for tag in fields.get("tags") or []]
        config = fields.get("config")
        summary = fields.get("summary")
        pk = await self.db.fetchval(
            """
            INSERT INTO runs (project_pk, name, display_name, state, created_at, updated_at,
                              heartbeat_at, config, summary, tags, notes, group_name, job_type,
                              host, program, commit_hash, description, sweep, owner_pk)
            VALUES ($1, $2, $3, $4, to_timestamp($5), to_timestamp($5), to_timestamp($5),
                    $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17)
            RETURNING pk
            """,
            project.pk,
            name,
            display_name,
            values["state"],
            now,
            orjson.dumps(config).decode() if config is not None else None,
            orjson.dumps(summary).decode() if summary is not None else None,
            tags,
            values["notes"],
            values["group_name"],
            values["job_type"],
            values["host"],
            values["program"],
            values["commit_hash"],
            values["description"],
            values["sweep"],
            owner,
        )
        run = Run(
            pk=pk,
            project=project,
            name=name,
            row=project.table.add_row(),
            display_name=display_name,
            state=values["state"],
            created_at=now,
            updated_at=now,
            heartbeat_at=now,
            tags=tags,
            notes=values["notes"],
            group=values["group_name"],
            job_type=values["job_type"],
            host=values["host"],
            program=values["program"],
            commit=values["commit_hash"],
            description=values["description"],
            sweep=values["sweep"],
            owner=owner,
        )
        project.add(run)
        self.runs_by_pk[pk] = run
        if run.active:
            self._mark_active(run, project)
        table_values = run.attributes()
        if config:
            table_values.update(self._config_columns(config))
            run.metric_defs = parse_metric_defs(config)
        if summary:
            table_values.update(self._summary_columns(summary))
            if run.active:
                run.summary_flat = self._summary_columns(summary)
        project.table.set_many(run.row, table_values)
        for stream in self.STREAMS:
            self._cache_history((run.pk, stream), History())
        self.live.broadcast(
            project.pk,
            Hub.event("created", {"run": name, "display_name": display_name, "state": run.state}),
        )
        return run

    async def _update_run(self, run: Run, fields: dict[str, Any]) -> None:
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
        assignments = ", ".join(f"{col} = ${i}" for i, col in enumerate(sets, start=3))
        await self.db.execute(
            f"UPDATE runs SET updated_at = to_timestamp($2){', ' if sets else ''}{assignments} "
            "WHERE pk = $1",
            run.pk,
            now,
            *sets.values(),
        )
        project = run.project
        run.updated_at = now
        for key in self.FIELD_COLUMNS:
            if fields.get(key) is not None:
                setattr(run, key, fields[key])
        if tags is not None:
            run.tags = tags
        self._sync_state(run, project)
        table = project.table
        table.set_many(run.row, run.attributes())
        defs = None
        if config is not None:
            self._replace_columns(table, run.row, "config.", self._config_columns(config))
            defs = parse_metric_defs(config)
            if defs == run.metric_defs:
                defs = None
            else:
                run.metric_defs = defs
        changed = self._apply_summary(run, table, summary) if summary is not None else None
        self._publish_run(run, changed, metric_defs=defs)

    @staticmethod
    def _replace_columns(table: ColumnTable, row: int, prefix: str, values: dict[str, Any]):
        for name in table.row_column_names(row, prefix):
            if name not in values:
                table.clear(row, name)
        table.set_many(row, values)

    def _apply_summary(
        self, run: Run, table: ColumnTable, summary: dict[str, Any]
    ) -> dict[str, Any]:
        """Write a run's new summary into the table; returns the columns that changed."""
        flat = self._summary_columns(summary)
        previous = run.summary_flat
        if previous is None:
            self._replace_columns(table, run.row, "summary.", flat)
            changed = flat
        else:
            changed = {k: v for k, v in flat.items() if k not in previous or previous[k] != v}
            for name in previous:
                if name not in flat:
                    table.clear(run.row, name)
                    changed[name] = None
            table.set_many(run.row, changed)
        if run.active:
            run.summary_flat = flat
        return changed

    def _publish_run(
        self,
        run: Run,
        summary: dict[str, Any] | None = None,
        attributes: dict[str, Any] | None = None,
        metric_defs: list[dict[str, Any]] | None = None,
    ) -> None:
        """Tell dashboards showing ``run`` about its state, line count, summary, edited
        attributes and changed metric definitions."""
        subs = self.live.watching(run.project.pk, run.name)
        if not subs:
            return
        data: dict[str, Any] = {
            "run": run.name,
            "state": run.state,
            "lines": run.history_line_count,
            "updated_at": run.updated_at,
        }
        if summary:
            data["summary"] = summary
        if attributes:
            data["attributes"] = attributes
        if metric_defs is not None:
            data["metric_defs"] = metric_defs
        event = Hub.event("run", data)
        for sub in subs:
            sub.push(event)

    def _sync_state(self, run: Run, project: Project) -> None:
        if run.active:
            if run.pk not in self.active:
                self._mark_active(run, project)
        elif run.pk in self.active:
            self.active.pop(run.pk, None)
            project.active.pop(run.name, None)
            run.summary_flat = None
            for stream in self.STREAMS:
                self._schedule_compaction(run, stream)

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

        # Only UpsertBucket creates runs: a client still logging to a deleted run must not
        # bring back a partial copy of it.
        run = self.get_run(entity, project_name, name)
        if run is None:
            if self.discarding(entity, project_name, name):
                return
            raise NotFound(f"run {entity}/{project_name}/{name} not found")
        async with self._run_lock(run):
            if run.deleted or run.project.key != (entity, project_name):
                if self.discarding(entity, project_name, name):
                    return
                raise NotFound(f"run {entity}/{project_name}/{name} not found")
            self._check_owner(run, owner, admin)
            project = run.project
            pk = run.pk
            now = time.time()

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
                    log.warning("run %s: dropping unparseable summary", run.path)
                    summary_raw = summary_data = None
                elif isinstance(summary_raw, bytes):
                    summary_raw = summary_raw.decode()
            history_count = max(run.history_line_count, h_off + len(h_lines))
            events_count = max(run.events_line_count, e_off + len(e_lines))
            log_count = max(run.log_line_count, o_off + len(o_lines))
            # Retries can resend lines we already have; only append unseen ones. Series are
            # loaded before persisting so that rebuilding them from the database can't see the
            # new lines (which would then be appended twice).
            appends: dict[str, tuple[History, int, list[str]]] = {}
            for stream, offset, lines in (("history", h_off, h_lines), ("system", e_off, e_lines)):
                new_from = max(offset, run.lines(stream))
                if new_from < offset + len(lines):
                    (series,) = await self.histories([run], None, stream)
                    appends[stream] = (series, new_from, lines[new_from - offset :])
            accrued = self.budgets.accrual(run, now)

            # The whole request in one statement, so one round trip and one transaction. Lines
            # are upserted because clients resend chunks they could not confirm.
            await self.db.execute(
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
                    INSERT INTO gpu_usage (org_pk, project_pk, run_pk, run_name, month, seconds,
                                           gpu_type, gpu_count)
                    SELECT $15::bigint, $16::bigint, $1, $17::text, month, seconds, $18::text,
                           $19::int
                    FROM unnest($20::date[], $21::float8[]) AS t(month, seconds)
                    ON CONFLICT (run_pk, month)
                    DO UPDATE SET seconds = gpu_usage.seconds + excluded.seconds
                )
                UPDATE runs
                SET state = $8, exitcode = $9, updated_at = to_timestamp($10),
                    heartbeat_at = to_timestamp($10), summary = COALESCE($11, summary),
                    history_line_count = $12, events_line_count = $13, log_line_count = $14
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
                project.org.pk,
                project.pk,
                run.name,
                run.gpu_type,
                run.gpu_count,
                [month for month, _ in accrued],
                [seconds for _, seconds in accrued],
            )

            for stream, (series, new_from, lines) in appends.items():
                if self._history.get((pk, stream)) is not series:  # evicted while we wrote
                    self._cache_history((pk, stream), series)
                series.append_rows(new_from, self._parse_rows(lines))
                run.note_metrics(series.series, stream)
            previous_state = run.state
            run.history_line_count = history_count
            run.events_line_count = events_count
            run.log_line_count = log_count
            run.state = state
            run.exitcode = exitcode
            run.updated_at = run.heartbeat_at = now
            self.budgets.accrue(run, accrued)
            table = project.table
            changed = (
                self._apply_summary(run, table, summary_data) if summary_data is not None else None
            )
            table.set(run.row, "state", state)
            table.set(run.row, "exitcode", exitcode)
            table.set(run.row, "updated_at", now)
            table.set(run.row, "heartbeat_at", now)
            if accrued:
                table.set(run.row, "gpu_hours", run.gpu_hours)
            self._sync_state(run, project)
            subs = self.live.watching(project.pk, name)
            if subs:
                for stream, (series, new_from, _) in appends.items():
                    watchers = subs if stream == "history" else [s for s in subs if s.system]
                    x_key = "_step" if stream == "history" else "_runtime"
                    points = series.delta(new_from, x_key) if watchers else None
                    if points:
                        event = Hub.points_event(stream, name, run.lines(stream), points)
                        for sub in watchers:
                            sub.push(event)
                if changed or state != previous_state:
                    self._publish_run(run, changed)

    async def save_file(self, run: Run, name: str, data: bytes) -> None:
        path = self.file_path(run, name)
        if path is None:
            raise ValueError(f"invalid file name {name!r}")

        def write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(path)

        async with self._run_lock(run):
            if run.deleted:
                raise NotFound(f"run {run.path} not found")
            await asyncio.to_thread(write)
            await self.db.execute(
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
                await self._set_detected_gpus(run, *self._read_gpus(data))

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

    async def _set_detected_gpus(self, run: Run, gpu_type: str | None, count: int) -> None:
        """Record the GPUs found in a run's metadata (call with the run's lock held)."""
        if (run.gpu_type_detected, run.gpu_count_detected) == (gpu_type, count):
            return
        before = (run.gpu_type, run.gpu_count)
        run.gpu_type_detected, run.gpu_count_detected = gpu_type, count
        effective = (run.gpu_type, run.gpu_count)
        await self.db.execute(
            "WITH usage AS (UPDATE gpu_usage SET gpu_type = $4, gpu_count = $5 WHERE run_pk = $1) "
            "UPDATE runs SET gpu_type = $2, gpu_count = $3 WHERE pk = $1",
            run.pk,
            gpu_type,
            count,
            *effective,
        )
        if effective != before:
            self._gpus_changed(run)

    def _gpus_changed(self, run: Run) -> None:
        """Show a run's new effective GPUs in its ledger entries, table row and dashboards."""
        self.budgets.sync_gpus(run)
        changed: dict[str, Any] = {
            "gpu_type": run.gpu_type,
            "gpu_count": run.gpu_count,
            "gpu_hours": run.gpu_hours,
        }
        run.project.table.set_many(run.row, changed)
        self._publish_run(run, attributes=changed)

    async def _detect_missing_gpus(self) -> None:
        """Read the GPUs of runs whose metadata was uploaded before GPUs were tracked."""
        pks = await self.db.fetch(
            "SELECT r.pk FROM runs r JOIN run_files f ON f.run_pk = r.pk AND f.name = $1 "
            "WHERE r.gpu_count IS NULL",
            self.METADATA_FILE,
        )
        runs = [self.runs_by_pk[pk] for (pk,) in pks if pk in self.runs_by_pk]
        if not runs:
            return

        def read() -> list[tuple[str | None, int]]:
            found = []
            for run in runs:
                path = self.file_path(run, self.METADATA_FILE)
                try:
                    data = path.read_bytes() if path is not None else b""
                except OSError:
                    data = b""
                found.append(self._read_gpus(data))
            return found

        for run, (gpu_type, count) in zip(runs, await asyncio.to_thread(read), strict=True):
            async with self._run_lock(run):
                if not run.deleted:
                    await self._set_detected_gpus(run, gpu_type, count)
        log.info("read the GPUs of %d runs from their metadata", len(runs))

    async def set_state(self, run: Run, state: str) -> None:
        async with self._run_lock(run):
            if run.deleted:
                return
            await self.db.execute("UPDATE runs SET state = $2 WHERE pk = $1", run.pk, state)
            run.state = state
            project = run.project
            project.table.set(run.row, "state", state)
            self._sync_state(run, project)
            self._publish_run(run)

    async def mark_crashed_runs(self) -> list[Run]:
        """Mark active runs whose client stopped sending heartbeats as crashed."""
        cutoff = time.time() - self.settings.crash_timeout
        stale = [run for run in self.active.values() if run.heartbeat_at < cutoff]
        for run in stale:
            if run.heartbeat_at < cutoff and run.active:
                await self.set_state(run, "crashed")
        return stale

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

    async def create_project(
        self, entity: str, name: str, description: str | None = None
    ) -> Project:
        async with self._catalog:
            project = None
            if (entity, name) not in self.projects:
                project = await self._insert_project(self.org(entity), name, description)
            if project is None:
                raise Conflict(f"project {entity}/{name} already exists")
            return project

    async def update_project(self, project: Project, fields: dict[str, Any]) -> None:
        """Rename a project and/or change its description (the keys present in ``fields``)."""
        async with self._locked([project]):
            if project.deleted:
                raise NotFound(f"project {project.entity}/{project.name} not found")
            name = fields.get("name") or project.name
            renamed = name != project.name
            if renamed and project.active:
                # Their clients address them by the old name in every request.
                raise Conflict("cannot rename a project with active runs", project.active)
            description = fields.get("description", project.description)
            async with self._catalog:
                if renamed and (project.entity, name) in self.projects:
                    raise Conflict(f"project {project.entity}/{name} already exists")
                now = time.time()
                await self.db.execute(
                    "UPDATE projects SET name = $2, description = $3, "
                    "updated_at = to_timestamp($4) WHERE pk = $1",
                    project.pk,
                    name,
                    description,
                    now,
                )
                if renamed:
                    del self.projects[project.key]
                    project.name = name
                    self.projects[project.key] = project
                project.description = description
                project.updated_at = now
        if renamed:
            # Dashboards address the project by name: tell them, then end their streams.
            self.live.broadcast(project.pk, Hub.event("project", {"renamed_to": name}))
            self.live.close_project(project.pk)

    async def delete_project(self, project: Project, force: bool = False) -> None:
        """Delete a project with all its runs; refused while runs are active unless ``force``."""
        async with self._locked([project], all_runs=True):
            if project.deleted:
                raise NotFound(f"project {project.entity}/{project.name} not found")
            if project.active and not force:
                raise Conflict("project has active runs (pass force=true)", project.active)
            now = time.time()
            async with self.db.pool.acquire() as conn, conn.transaction():
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
                await conn.execute("DELETE FROM projects WHERE pk = $1", project.pk)
            if deleted is not None:
                self.budgets.retire_project(project, DeletedProject(deleted, project.name, now))
            pks = self._drop_project(project)
        self.live.broadcast(project.pk, Hub.event("project", {"deleted": True}))
        self.live.close_project(project.pk)
        await self._remove_files(pks)

    def _drop_project(self, project: Project) -> list[int]:
        """Forget a project whose delete committed; returns its runs' pks."""
        pks = [run.pk for run in project.runs]
        self.budgets.forget_project(project)
        self._mark_deleted_live(project.active.values())
        for run in project.runs:
            self._forget_run(run)
        del self.projects[project.key]
        del self.projects_by_pk[project.pk]
        project.deleted = True
        return pks

    # ------------------------------------------------------------------ imports

    async def import_project(
        self, org: Org, name: str, description: str | None, created_at: float | None
    ) -> Project:
        """The project ``name`` of ``org``, created with the given creation time if missing."""
        async with self._catalog:
            if org.deleted:
                raise NotFound(f"organization {org.name} not found")
            project = self.projects.get((org.name, name))
            if project is None:
                project = await self._insert_project(org, name, description, created_at)
                assert project is not None  # only created under _catalog, and it was missing
            return project

    async def import_run(self, project: Project, data: ImportedRun) -> Run | None:
        """Add a finished run copied from elsewhere, with its timestamps, series, logs and
        files; None if the project already has a run of that name (so imports can resume).

        Series are stored compacted straight away, and its lifetime is entered in the usage
        ledger as if it had sent heartbeats throughout.
        """
        if data.state in Run.ACTIVE_STATES:
            raise ValueError(f"cannot import a {data.state} run")
        paths: dict[str, tuple[str, ...]] = {}
        for name in data.files:
            parts = self.file_parts(name)
            if parts is None:
                raise ValueError(f"invalid file name {name!r}")
            paths[name] = parts
        gpu_type, gpu_count = None, None
        if self.METADATA_FILE in data.files:
            gpu_type, gpu_count = self._read_gpus(data.files[self.METADATA_FILE])
        series = {}
        for stream, lines in (("history", data.history), ("system", data.events)):
            hist = History()
            hist.append_rows(0, self._parse_rows(lines))
            series[stream] = hist
        columns = [
            (stream, key, s.n, s.encode())
            for stream, hist in series.items()
            for key, s in hist.series.items()
        ]
        heartbeat_at = max(data.heartbeat_at, data.created_at)
        ledger = gpus.split_by_month(data.created_at, heartbeat_at)
        history, events, logs = (
            self._pg_text(lines) for lines in (data.history, data.events, data.logs)
        )

        async with project.lock:
            if project.deleted:
                raise NotFound(f"project {project.entity}/{project.name} not found")
            if data.name in project.by_name:
                return None
            async with self.db.pool.acquire() as conn, conn.transaction():
                pk = await conn.fetchval(
                    """
                    INSERT INTO runs (project_pk, name, display_name, state, created_at,
                        updated_at, heartbeat_at, config, summary, tags, notes, group_name,
                        job_type, host, program, commit_hash, sweep, exitcode,
                        history_line_count, events_line_count, log_line_count,
                        history_compacted_line_count, system_compacted_line_count, owner_pk,
                        gpu_type, gpu_count)
                    VALUES ($1, $2, $3, $4, to_timestamp($5), to_timestamp($6), to_timestamp($6),
                        $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $18,
                        $19, $21, $22, $23)
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
                )
                for table, lines in (("history", history), ("events", events), ("logs", logs)):
                    if lines:
                        await conn.copy_records_to_table(
                            table,
                            records=[(pk, i, line) for i, line in enumerate(lines)],
                            columns=("run_pk", "line", "data"),
                        )
                if columns:
                    await conn.copy_records_to_table(
                        "history_columns",
                        records=[(pk, *column) for column in columns],
                        columns=("run_pk", "stream", "key", "n", "data"),
                    )
                if ledger:
                    await conn.copy_records_to_table(
                        "gpu_usage",
                        records=[
                            (
                                project.org.pk,
                                project.pk,
                                pk,
                                data.name,
                                month,
                                seconds,
                                gpu_type,
                                gpu_count,
                            )
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
                    # Written before the commit: if the commit fails, the startup sweep removes
                    # the files of a run that doesn't exist.
                    root = self.files_dir / str(pk)

                    def write() -> None:
                        for name, content in data.files.items():
                            path = root.joinpath(*paths[name])
                            path.parent.mkdir(parents=True, exist_ok=True)
                            path.write_bytes(content)

                    await asyncio.to_thread(write)
                    await conn.executemany(
                        "INSERT INTO run_files (run_pk, name, size, updated_at) "
                        "VALUES ($1, $2, $3, to_timestamp($4))",
                        [
                            (pk, name, len(content), heartbeat_at)
                            for name, content in data.files.items()
                        ],
                    )

            run = Run(
                pk=pk,
                project=project,
                name=data.name,
                row=project.table.add_row(),
                display_name=data.display_name,
                state=data.state,
                created_at=data.created_at,
                updated_at=heartbeat_at,
                heartbeat_at=heartbeat_at,
                tags=list(data.tags),
                notes=data.notes,
                group=data.group,
                job_type=data.job_type,
                host=data.host,
                program=data.program,
                commit=data.commit,
                sweep=data.sweep,
                exitcode=data.exitcode,
                owner=data.owner,
                gpu_type_detected=gpu_type,
                gpu_count_detected=gpu_count,
                history_line_count=len(history),
                events_line_count=len(events),
                log_line_count=len(logs),
                compacted={"history": len(history), "system": len(events)},
                compacted_keys={stream: list(hist.series) for stream, hist in series.items()},
            )
            project.add(run)
            self.runs_by_pk[pk] = run
            for month, seconds in ledger:
                self.budgets._add(
                    Usage(
                        project.org, project, run, pk, run.name, month, seconds, gpu_type, gpu_count
                    )
                )
            values = run.attributes()
            if data.config:
                values.update(self._config_columns(data.config))
            if data.summary:
                values.update(self._summary_columns(data.summary))
            project.table.set_many(run.row, values)
            for stream, hist in series.items():
                run.note_metrics(hist.series, stream)
            # Imported runs are older than most: keep the rows in creation order.
            self._reindex(project, sorted(project.runs, key=lambda r: (r.created_at, r.pk)))
        self.live.broadcast(
            project.pk,
            Hub.event(
                "created", {"run": run.name, "display_name": run.display_name, "state": run.state}
            ),
        )
        return run

    # ------------------------------------------------------------------ organizations

    async def create_org(self, name: str, display_name: str | None = None) -> Org:
        """Create the organization ``name`` (its ID, which never changes), shown as
        ``display_name`` (``name`` if not given)."""
        display_name = display_name if display_name is not None else name
        async with self._catalog:
            if name in self.orgs:
                raise Conflict(f"organization {name} already exists")
            # Raising inside the transaction rolls the insert back.
            async with self.db.pool.acquire() as conn, conn.transaction():
                record = await conn.fetchrow(
                    "INSERT INTO orgs (name, display_name, created_at, updated_at) "
                    "VALUES ($1, $2, now(), now()) ON CONFLICT (name) DO NOTHING RETURNING pk, "
                    "name, extract(epoch FROM created_at)::float8, "
                    "extract(epoch FROM updated_at)::float8",
                    name,
                    display_name,
                )
                if record is None:
                    raise Conflict(f"organization {name} already exists")
                # S3 bucket names start with the org's pk in five digits (D53).
                if record[0] > self.MAX_ORG_PK:
                    raise Conflict("this server can't hold more organizations")
            return self._add_org(Org(*record, display_name=display_name))

    @contextlib.asynccontextmanager
    async def _org_locked(self, org: Org, all_runs: bool = False) -> AsyncGenerator[list[Project]]:
        """Hold the locks of every project of ``org``, then ``_catalog`` (so no project can be
        created in it meanwhile); yields the projects."""
        while True:
            projects = self._org_projects(org)
            async with self._locked(projects, all_runs=all_runs), self._catalog:
                if org.deleted:
                    raise NotFound(f"organization {org.name} not found")
                if set(self._org_projects(org)) != set(projects):
                    continue  # a project was created while we waited: lock it too
                yield projects
                return

    async def set_org_display_name(self, org: Org, display_name: str) -> None:
        """Change what an organization is called. Its ID (``name``), which its projects, runs
        and clients address it by, never changes."""
        async with self._catalog:
            if org.deleted:
                raise NotFound(f"organization {org.name} not found")
            if display_name == org.display_name:
                return
            now = time.time()
            await self.db.execute(
                "UPDATE orgs SET display_name = $2, updated_at = to_timestamp($3) WHERE pk = $1",
                org.pk,
                display_name,
                now,
            )
            org.display_name = display_name
            org.updated_at = now

    async def delete_org(self, org: Org, force: bool = False) -> None:
        """Delete an organization with all its projects and runs; refused while runs are
        active unless ``force``."""
        async with self._org_locked(org, all_runs=True) as projects:
            active = [f"{p.name}/{run}" for p in projects for run in p.active]
            if active and not force:
                raise Conflict("organization has active runs (pass force=true)", active)
            # It waits on the locks held here, so it stops before writing anything more.
            await self.imports.cancel(org)
            async with self.db.pool.acquire() as conn, conn.transaction():
                # Its S3 buckets outlive it until the background purge removes them upstream.
                await self.s3.delete_org_buckets(conn, org)
                await conn.execute("DELETE FROM orgs WHERE pk = $1", org.pk)
            pks = [pk for project in projects for pk in self._drop_project(project)]
            del self.orgs[org.name]
            del self.orgs_by_pk[org.pk]
            org.deleted = True
            self.accounts.forget_org(org)
            self.imports.forget(org)
        self.s3.purge_later()
        await self.avatars.release(org.avatar)
        for project in projects:
            self.live.broadcast(project.pk, Hub.event("project", {"deleted": True}))
            self.live.close_project(project.pk)
        await self._remove_files(pks)

    def forget_owner(self, user: int) -> None:
        """Runs of a deleted user stay, without an owner (the database sets it to NULL)."""
        for run in self.runs_by_pk.values():
            if run.owner == user:
                run.owner = None
                run.project.table.clear(run.row, "owner")

    async def edit_runs(
        self,
        project: Project,
        runs: Sequence[Run],
        values: dict[str, Any],
        add_tags: Sequence[str] = (),
        remove_tags: Sequence[str] = (),
    ) -> list[Run]:
        """Set ``values`` (``EDITABLE`` attributes; None clears) on runs, and add or remove tags.

        Last write wins: a client that later sends tags or notes overwrites these edits.
        """
        async with self._locked([project], runs):
            runs = [run for run in runs if not run.deleted and run.project is project]
            if not runs:
                return []
            tags = []
            for run in runs:
                current = (
                    [str(tag) for tag in values.get("tags") or []] if "tags" in values else run.tags
                )
                current = list(dict.fromkeys([*current, *add_tags]))
                tags.append([tag for tag in current if tag not in remove_tags])
            columns = {**self.FIELD_COLUMNS, **self.OVERRIDES}
            sets = {columns[key]: v for key, v in values.items() if key != "tags"}
            assignments = "".join(f", {col} = ${i}" for i, col in enumerate(sets, start=4))
            now = time.time()
            # What each run's GPUs will be, for its usage ledger rows.
            fields = {self.OVERRIDES.get(key, key): v for key, v in values.items() if key != "tags"}
            effective = [
                (
                    fields.get("gpu_type_override", run.gpu_type_override),
                    fields.get("gpu_count_override", run.gpu_count_override),
                )
                for run in runs
            ]
            effective = [
                (
                    gpu_type if gpu_type is not None else run.gpu_type_detected,
                    count if count is not None else run.gpu_count_detected,
                )
                for run, (gpu_type, count) in zip(runs, effective, strict=True)
            ]
            usage = ""
            if self.OVERRIDES.keys() & values.keys():
                usage = (
                    "WITH usage AS (UPDATE gpu_usage u SET gpu_type = g.gpu_type, "
                    "gpu_count = g.gpu_count FROM unnest($1::bigint[], "
                    f"${len(sets) + 4}::text[], ${len(sets) + 5}::int[]) "
                    "AS g(pk, gpu_type, gpu_count) WHERE u.run_pk = g.pk) "
                )
            # One statement for the whole selection; each run's tags travel as a JSON array.
            await self.db.execute(
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
            for run, run_tags in zip(runs, tags, strict=True):
                changed = {
                    key: value for key, value in fields.items() if getattr(run, key) != value
                }
                if run_tags != run.tags:
                    changed["tags"] = run_tags
                gpus_before = (run.gpu_type, run.gpu_count)
                for key, value in changed.items():
                    setattr(run, key, value)
                for attribute, field_name in self.OVERRIDES.items():
                    if field_name in changed:  # report the effective value, not the override
                        changed[attribute] = getattr(run, attribute)
                        del changed[field_name]
                run.updated_at = now
                project.table.set_many(run.row, {**changed, "updated_at": now})
                if (run.gpu_type, run.gpu_count) != gpus_before:
                    self._gpus_changed(run)
                    changed.pop("gpu_type", None)
                    changed.pop("gpu_count", None)
                if changed:
                    self._publish_run(run, attributes=changed)
            return runs

    async def delete_runs(
        self, project: Project, runs: Sequence[Run], force: bool = False
    ) -> list[str]:
        """Delete runs with everything they logged; active runs only with ``force``."""
        async with self._locked([project], runs):
            runs = [run for run in runs if not run.deleted and run.project is project]
            active = [run.name for run in runs if run.active]
            if active and not force:
                raise Conflict("cannot delete active runs (pass force=true)", active)
            if not runs:
                return []
            pks = [run.pk for run in runs]
            await self.db.execute("DELETE FROM runs WHERE pk = ANY($1)", pks)
            self._mark_deleted_live(run for run in runs if run.active)
            for run in runs:
                self._forget_run(run)
            self._reindex(project, [run for run in project.runs if not run.deleted])
            names = [run.name for run in runs]
            self.live.broadcast(project.pk, Hub.event("deleted", {"runs": names}))
        await self._remove_files(pks)
        return names

    async def move_runs(self, source: Project, runs: Sequence[Run], target: Project) -> list[str]:
        """Move finished runs to another project, keeping everything they logged."""
        async with self._locked([source, target], runs):
            if target.deleted:
                raise NotFound(f"project {target.entity}/{target.name} not found")
            runs = [run for run in runs if not run.deleted and run.project is source]
            if target is source or not runs:
                return []
            active = [run.name for run in runs if run.active]
            if active:
                # Their clients address them by the old project in every request.
                raise Conflict("cannot move active runs", active)
            clashes = [run.name for run in runs if run.name in target.by_name]
            if clashes:
                raise Conflict(f"{target.name} already has runs with these ids", clashes)
            await self.db.execute(
                "WITH usage AS (UPDATE gpu_usage SET project_pk = $2 WHERE run_pk = ANY($1)) "
                "UPDATE runs SET project_pk = $2 WHERE pk = ANY($1)",
                [run.pk for run in runs],
                target.pk,
            )
            rows = np.fromiter((run.row for run in runs), np.int64, len(runs))
            new_rows = target.table.append_from(source.table, rows).tolist()
            moving = set(runs)
            self._reindex(source, [run for run in source.runs if run not in moving])
            for run, row in zip(runs, new_rows, strict=True):
                del source.by_name[run.name]
                run.project = target
                run.row = row
                self.budgets.moved(run)
                target.add(run)
            # Keep the target's rows in creation order, so moved runs don't look like new ones.
            self._reindex(target, sorted(target.runs, key=lambda r: (r.created_at, r.pk)))
            names = [run.name for run in runs]
        self.live.broadcast(
            source.pk, Hub.event("deleted", {"runs": names, "moved_to": target.name})
        )
        for run in runs:
            event = {"run": run.name, "display_name": run.display_name, "state": run.state}
            self.live.broadcast(target.pk, Hub.event("created", event))
        return names

    def discarding(self, entity: str, project: str, name: str) -> bool:
        """Whether writes to this run come from the client of a run deleted while it was running.

        That client keeps sending (wandb even re-creates its run with UpsertBucket when it
        finishes); its writes are acknowledged and dropped. The mark lasts until the client has
        been quiet for ``crash_timeout``, after which the run id is free again.
        """
        key = (entity, project, name)
        seen = self._deleted_live.get(key)
        if seen is None:
            return False
        now = time.time()
        if now - seen > self.settings.crash_timeout:
            del self._deleted_live[key]
            return False
        self._deleted_live[key] = now
        return True

    def _mark_deleted_live(self, runs: Iterable[Run]) -> None:
        now = time.time()
        cutoff = now - self.settings.crash_timeout
        for key in [k for k, seen in self._deleted_live.items() if seen < cutoff]:
            del self._deleted_live[key]  # their clients are long gone
        for run in runs:
            self._deleted_live[(run.entity, run.project.name, run.name)] = now

    def _forget_run(self, run: Run) -> None:
        """Drop a deleted run from memory (except its table row: see ``_reindex``)."""
        run.deleted = True
        self.budgets.forget_run(run)
        project = run.project
        project.by_name.pop(run.name, None)
        project.active.pop(run.name, None)
        self.active.pop(run.pk, None)
        self.runs_by_pk.pop(run.pk, None)
        self._locks.pop(run.pk, None)
        for stream in self.STREAMS:
            hist = self._history.pop((run.pk, stream), None)
            if hist is not None:
                hist.detach()

    @staticmethod
    def _reindex(project: Project, runs: list[Run]) -> None:
        """Make ``runs`` the project's table rows, in this order, renumbering them.

        Row numbers only hold until the next ``await``: readers turn rows into runs first.
        """
        project.table.take(np.fromiter((run.row for run in runs), np.int64, len(runs)))
        project.runs = runs
        for row, run in enumerate(runs):
            run.row = row

    async def _remove_files(self, pks: Iterable[int]) -> None:
        def remove() -> None:
            for pk in pks:
                shutil.rmtree(self.files_dir / str(pk), ignore_errors=True)

        await asyncio.to_thread(remove)

    # ------------------------------------------------------------------ series

    async def histories(
        self, runs: Sequence[Run], keys: Iterable[str] | None = None, stream: str = "history"
    ) -> list[History]:
        """Series of ``runs`` in ``stream`` ("history" or "system"), with ``keys`` loaded.

        Every metric is loaded when ``keys`` is None. Compacted series of all runs are fetched in
        one query; other keys may stay unloaded.
        """
        if stream not in self.STREAMS:  # stream names are interpolated into SQL
            raise ValueError(f"unknown series stream {stream!r}")
        wanted = None if keys is None else list(dict.fromkeys(keys))
        out: list[History | None] = []
        rebuilds: list[tuple[int, asyncio.Future[History]]] = []
        for run in runs:
            key = (run.pk, stream)
            hist = self._history.get(key)
            if hist is not None:
                self._history.move_to_end(key)
            elif run.lines(stream) == 0:
                hist = self._insert_history(run, stream, History())
            elif run.compacted[stream] >= run.lines(stream):
                hist = History(run.lines(stream), run.compacted_keys[stream])
                hist = self._insert_history(run, stream, hist)
            else:
                # Rebuild from raw lines; concurrent callers share one load.
                future = self._rebuilding.get(key)
                if future is None:
                    future = asyncio.ensure_future(self._rebuild_history(run, stream))
                    self._rebuilding[key] = future
                    future.add_done_callback(lambda _, key=key: self._rebuilding.pop(key, None))
                rebuilds.append((len(out), future))
            out.append(hist)
        for index, future in rebuilds:
            out[index] = await future
        histories: list[History] = [h for h in out if h is not None]

        missing = [(run.pk, h, h.missing(wanted)) for run, h in zip(runs, histories, strict=True)]
        missing = [(pk, h, m) for pk, h, m in missing if m]
        if missing:
            pks = [pk for pk, _, _ in missing]
            if wanted is None:
                rows = await self.db.fetch(
                    "SELECT run_pk, key, n, data FROM history_columns "
                    "WHERE run_pk = ANY($1) AND stream = $2",
                    pks,
                    stream,
                )
            else:
                keyset = list(set().union(*(m for _, _, m in missing)))
                rows = await self.db.fetch(
                    "SELECT run_pk, key, n, data FROM history_columns "
                    "WHERE run_pk = ANY($1) AND stream = $2 AND key = ANY($3)",
                    pks,
                    stream,
                    keyset,
                )
            by_run: dict[int, list] = {}
            for pk, key, n, data in rows:
                by_run.setdefault(pk, []).append((key, Series.decode(data, n)))
            for pk, hist, requested in missing:
                hist.preload(by_run.get(pk, ()), requested)
            self._evict_history()
        return histories

    def _insert_history(self, run: Run, stream: str, hist: History) -> History:
        if run.deleted:  # deleted while we read it: don't cache what nothing will evict
            return hist
        self._evict_history()
        self._cache_history((run.pk, stream), hist)
        return hist

    async def _rebuild_history(self, run: Run, stream: str) -> History:
        records = await self.db.fetch(
            f"SELECT line, data FROM {self.STREAMS[stream]} WHERE run_pk = $1 ORDER BY line",
            run.pk,
        )
        hist = History()
        start, batch, expected = None, [], 0
        for line, data in records:
            if start is None or line != expected:
                if batch and start is not None:
                    hist.append_rows(start, self._parse_rows(batch))
                start, batch = line, []
            batch.append(data)
            expected = line + 1
        if batch and start is not None:
            hist.append_rows(start, self._parse_rows(batch))
        hist.n_rows = max(hist.n_rows, run.lines(stream))
        cached = self._history.get((run.pk, stream))
        if cached is not None:
            return cached
        self._insert_history(run, stream, hist)
        if not run.active and run.compacted[stream] < run.lines(stream):
            self._schedule_compaction(run, stream)
        return hist

    def _schedule_compaction(self, run: Run, stream: str) -> None:
        """Store a finished run's series as one blob per metric, in the background."""
        hist = self._history.get((run.pk, stream))
        if hist is None or not hist.complete or run.lines(stream) == 0 or run.deleted:
            return
        line_count = run.lines(stream)
        pk = run.pk
        keys = list(hist.series)
        counts = [hist.series[key].n for key in keys]
        blobs = [hist.series[key].encode() for key in keys]

        async def compact() -> None:
            try:
                # Upsert every metric's blob and drop metrics the stream no longer has.
                await self.db.execute(
                    f"""
                    WITH upserted AS (
                        INSERT INTO history_columns (run_pk, stream, key, n, data)
                        SELECT $1, $2, key, n, data
                        FROM unnest($3::text[], $4::int[], $5::bytea[]) AS t(key, n, data)
                        ON CONFLICT (run_pk, stream, key)
                        DO UPDATE SET n = excluded.n, data = excluded.data
                    ), removed AS (
                        DELETE FROM history_columns
                        WHERE run_pk = $1 AND stream = $2 AND key <> ALL($3::text[])
                    )
                    UPDATE runs SET {stream}_compacted_line_count = $6 WHERE pk = $1
                    """,
                    pk,
                    stream,
                    keys,
                    counts,
                    blobs,
                    line_count,
                )
            except Exception:
                if not run.deleted:  # else the run's rows were deleted under us, as intended
                    log.exception("failed to compact %s of run %s", stream, run.path)
                return
            if line_count >= run.compacted[stream]:
                run.compacted[stream] = line_count
                run.compacted_keys[stream] = keys
            self._evict_history()  # the run's in-memory series are now evictable

        task = asyncio.get_running_loop().create_task(compact())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _cache_history(self, key: tuple[int, str], hist: History) -> None:
        previous = self._history.pop(key, None)
        if previous is not None and previous is not hist:
            previous.detach()
        if previous is not hist:
            hist.attach(self._history_points)
        self._history[key] = hist

    def _evict_history(self) -> None:
        """Drop least recently used series of finished runs until under the point budget.

        Active runs count towards the budget but are never evicted.
        """
        budget = self.settings.history_cache_points
        if self._history_points.total <= budget:
            return
        for key in list(self._history):
            if self._history_points.total <= budget:
                break
            run = self.runs_by_pk.get(key[0])
            if run is None:
                continue
            if run.active or run.compacted[key[1]] < run.lines(key[1]):
                continue  # active or not yet durable in compacted form
            self._history.pop(key).detach()

    async def flush(self) -> None:
        """Wait for background compactions; used by tests, on startup and on shutdown."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
