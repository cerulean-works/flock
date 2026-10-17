import asyncio
import contextlib
import logging
import secrets
import shutil
import string
import time
from collections import Counter, OrderedDict
from collections.abc import AsyncGenerator, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

import numpy as np
import orjson

from src.core.history import History, PointCounter, Series
from src.core.live import Hub
from src.core.names import ADJECTIVES, NOUNS
from src.core.table import ColumnTable
from src.db.database import Database
from src.settings import Settings
from src.utils import encode_id, flatten, loads_lenient

log = logging.getLogger(__name__)


class NotFound(LookupError):
    """The run or project was deleted, renamed or moved (HTTP 404)."""


class Discarded(Exception):
    """A write from the client of a run deleted while it was running. It is acknowledged and
    dropped, so the client finishes normally and the run stays deleted."""

    def __init__(self, project: str, name: str) -> None:
        super().__init__(f"run {project}/{name} was deleted")
        self.project = project
        self.name = name


class Conflict(Exception):
    """A mutation that can't be applied as asked (HTTP 409); ``ids`` names the runs in the way."""

    def __init__(self, message: str, ids: Iterable[str] = ()) -> None:
        super().__init__(message)
        self.message = message
        self.ids = list(ids)


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
    # Plottable metrics this run logged, per stream (its share of the project's key counts).
    keys: dict[str, set[str]] = field(default_factory=lambda: {"history": set(), "system": set()})
    # Set once the run's delete commits: code still holding the run must not write to it.
    deleted: bool = False

    @property
    def active(self) -> bool:
        return self.state in self.ACTIVE_STATES

    @property
    def entity(self) -> str:
        return self.project.entity

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


class Project:
    def __init__(
        self,
        pk: int,
        entity: str,
        name: str,
        description: str | None = None,
        created_at: float = 0.0,
        updated_at: float = 0.0,
    ) -> None:
        self.pk = pk
        self.entity = entity
        self.name = name
        self.description = description
        self.created_at = created_at
        self.updated_at = updated_at
        # Indexed by table row: runs are kept in (created_at, pk) order.
        self.runs: list[Run] = []
        self.by_name: dict[str, Run] = {}
        self.table = ColumnTable()
        self.active: dict[str, Run] = {}
        # How many runs logged each plottable metric, per stream (dashboards chart these).
        self.metric_counts: dict[str, Counter[str]] = {"history": Counter(), "system": Counter()}
        # Held to create runs, move runs in or out, delete runs, and rename or delete the project.
        self.lock = asyncio.Lock()
        self.deleted = False

    @property
    def key(self) -> tuple[str, str]:
        return (self.entity, self.name)

    def metric_keys(self, stream: str = "history") -> list[str]:
        return sorted(self.metric_counts[stream])

    def note_metrics(self, run: Run, keys: Iterable[str], stream: str = "history") -> None:
        known = run.keys[stream]
        counts = self.metric_counts[stream]
        for key in keys:
            # Skip wandb bookkeeping (_step, _runtime, _wandb, ...), which every row carries.
            if key not in known and not key.startswith("_"):
                known.add(key)
                counts[key] += 1

    def count_metrics(self, run: Run, sign: int) -> None:
        """Add (``sign=1``) or remove (``-1``) a run's metrics from the project's key counts."""
        for stream, keys in run.keys.items():
            counts = self.metric_counts[stream]
            for key in keys:
                counts[key] += sign
                if counts[key] <= 0:
                    del counts[key]

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

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.files_dir = settings.files_dir
        self.db = Database(settings.database_url, max_size=settings.pool_size)
        self.projects: dict[tuple[str, str], Project] = {}
        self.projects_by_pk: dict[int, Project] = {}
        self.runs_by_pk: dict[int, Run] = {}
        self.active: dict[int, Run] = {}
        self._locks: dict[int, asyncio.Lock] = {}  # per run, by pk
        # Serializes changes to which (entity, name) maps to which project.
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
        except BaseException:
            await self.db.close()
            raise

    async def close(self) -> None:
        await self.flush()
        await self.db.close()

    async def _load(self) -> None:
        started = time.perf_counter()
        for record in await self.db.fetch(
            "SELECT pk, entity, name, description, extract(epoch FROM created_at)::float8, "
            "extract(epoch FROM updated_at)::float8 FROM projects"
        ):
            self._add_project(Project(*record))
        pending: dict[int, dict[str, tuple[list[int], list[Any]]]] = {}
        query = """
            SELECT pk, project_pk, name, display_name, state,
                   extract(epoch FROM created_at)::float8, extract(epoch FROM updated_at)::float8,
                   extract(epoch FROM heartbeat_at)::float8, tags, notes, group_name, job_type,
                   host, program, commit_hash, description, sweep, exitcode, history_line_count,
                   events_line_count, log_line_count, history_compacted_line_count,
                   system_compacted_line_count, config, summary
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
                )
                project.add(run)
                self.runs_by_pk[run.pk] = run
                values = run.attributes()
                config_raw, summary_raw = record[23], record[24]
                if config_raw:
                    values.update(self._config_columns(loads_lenient(config_raw)))
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
            run.project.note_metrics(run, keys, stream)
        # Active runs are always kept in memory. Streams that stopped before being compacted
        # (a crash, or system metrics logged before migration 0002) are rebuilt so their metric
        # keys are known, one run at a time so memory stays bounded while they compact.
        for stream in self.STREAMS:
            for run in list(self.runs_by_pk.values()):
                if run.active or run.compacted[stream] < run.lines(stream):
                    (hist,) = await self.histories([run], (), stream)
                    run.project.note_metrics(run, hist.series, stream)
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

    async def ensure_project(self, entity: str, name: str) -> Project:
        """The project ``entity/name``, created if it doesn't exist (as ``wandb.init`` does)."""
        project = self.projects.get((entity, name))
        if project is not None:
            return project
        async with self._catalog:
            project = self.projects.get((entity, name))
            if project is None:
                record = await self.db.fetchrow(
                    "INSERT INTO projects (entity, name, created_at, updated_at) "
                    "VALUES ($1, $2, now(), now()) "
                    "ON CONFLICT (entity, name) DO UPDATE SET name = excluded.name "
                    "RETURNING pk, entity, name, description, "
                    "extract(epoch FROM created_at)::float8, extract(epoch FROM updated_at)::float8",
                    entity,
                    name,
                )
                assert record is not None
                project = self._add_project(Project(*record))
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

    def file_path(self, run: Run, name: str) -> Path | None:
        parts = Path(name).parts
        if not parts or Path(name).is_absolute() or any(p in ("..", "") for p in parts):
            return None
        return self.files_dir / str(run.pk) / Path(*parts)

    # ------------------------------------------------------------------ mutations

    async def upsert_run(
        self, entity: str, project_name: str | None, name: str | None, fields: dict[str, Any]
    ) -> tuple[Run, bool]:
        """Create or update a run. ``fields`` holds only the values the client provided.

        Recognised fields: the keys of ``FIELD_COLUMNS`` plus ``tags`` (list), ``config``
        (wandb-style config dict) and ``summary`` (dict).
        """
        project_name = project_name or self.settings.default_project
        # Clients that don't name the run get a random 8 character id, as wandb does.
        alphabet = string.ascii_lowercase + string.digits
        name = name or "".join(secrets.choice(alphabet) for _ in range(8))
        if self.discarding(entity, project_name, name):
            raise Discarded(project_name, name)
        async with self._writable_project(entity, project_name) as project:
            run = project.by_name.get(name)
            if run is None:
                return await self._create_run(project, name, fields), True
            async with self._run_lock(run):
                await self._update_run(run, fields)
            return run, False

    async def _create_run(self, project: Project, name: str, fields: dict[str, Any]) -> Run:
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
                              host, program, commit_hash, description, sweep)
            VALUES ($1, $2, $3, $4, to_timestamp($5), to_timestamp($5), to_timestamp($5),
                    $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)
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
        )
        project.add(run)
        self.runs_by_pk[pk] = run
        if run.active:
            self._mark_active(run, project)
        table_values = run.attributes()
        if config:
            table_values.update(self._config_columns(config))
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
        if config is not None:
            self._replace_columns(table, run.row, "config.", self._config_columns(config))
        changed = self._apply_summary(run, table, summary) if summary is not None else None
        self._publish_run(run, changed)

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
    ) -> None:
        """Tell dashboards showing ``run`` about its state, line count, summary and edited
        attributes."""
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
        self, entity: str, project_name: str, name: str, payload: dict[str, Any]
    ) -> None:
        """Apply one wandb ``file_stream`` request."""
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
            )

            for stream, (series, new_from, lines) in appends.items():
                if self._history.get((pk, stream)) is not series:  # evicted while we wrote
                    self._cache_history((pk, stream), series)
                series.append_rows(new_from, self._parse_rows(lines))
                project.note_metrics(run, series.series, stream)
            previous_state = run.state
            run.history_line_count = history_count
            run.events_line_count = events_count
            run.log_line_count = log_count
            run.state = state
            run.exitcode = exitcode
            run.updated_at = run.heartbeat_at = now
            table = project.table
            changed = (
                self._apply_summary(run, table, summary_data) if summary_data is not None else None
            )
            table.set(run.row, "state", state)
            table.set(run.row, "exitcode", exitcode)
            table.set(run.row, "updated_at", now)
            table.set(run.row, "heartbeat_at", now)
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
    )

    async def create_project(
        self, entity: str, name: str, description: str | None = None
    ) -> Project:
        async with self._catalog:
            if (entity, name) in self.projects:
                raise Conflict(f"project {entity}/{name} already exists")
            record = await self.db.fetchrow(
                "INSERT INTO projects (entity, name, description, created_at, updated_at) "
                "VALUES ($1, $2, $3, now(), now()) ON CONFLICT (entity, name) DO NOTHING "
                "RETURNING pk, entity, name, description, "
                "extract(epoch FROM created_at)::float8, extract(epoch FROM updated_at)::float8",
                entity,
                name,
                description,
            )
            if record is None:
                raise Conflict(f"project {entity}/{name} already exists")
            return self._add_project(Project(*record))

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
            await self.db.execute("DELETE FROM projects WHERE pk = $1", project.pk)
            pks = [run.pk for run in project.runs]
            self._mark_deleted_live(project.active.values())
            for run in project.runs:
                self._forget_run(run)
            del self.projects[project.key]
            del self.projects_by_pk[project.pk]
            project.deleted = True
        self.live.broadcast(project.pk, Hub.event("project", {"deleted": True}))
        self.live.close_project(project.pk)
        await self._remove_files(pks)

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
            sets = {self.FIELD_COLUMNS[key]: v for key, v in values.items() if key != "tags"}
            assignments = "".join(f", {col} = ${i}" for i, col in enumerate(sets, start=4))
            now = time.time()
            # One statement for the whole selection; each run's tags travel as a JSON array.
            await self.db.execute(
                "UPDATE runs SET tags = ARRAY(SELECT jsonb_array_elements_text(t.tags::jsonb)), "
                f"updated_at = to_timestamp($3){assignments} "
                "FROM unnest($1::bigint[], $2::text[]) AS t(pk, tags) WHERE runs.pk = t.pk",
                [run.pk for run in runs],
                [orjson.dumps(run_tags).decode() for run_tags in tags],
                now,
                *sets.values(),
            )
            for run, run_tags in zip(runs, tags, strict=True):
                changed = {
                    key: value
                    for key, value in values.items()
                    if key != "tags" and getattr(run, key) != value
                }
                if run_tags != run.tags:
                    changed["tags"] = run_tags
                for key, value in changed.items():
                    setattr(run, key, value)
                run.updated_at = now
                project.table.set_many(run.row, {**changed, "updated_at": now})
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
                source.count_metrics(run, -1)
                run.project = target
                run.row = row
                target.add(run)
                target.count_metrics(run, 1)
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
        project = run.project
        project.by_name.pop(run.name, None)
        project.active.pop(run.name, None)
        project.count_metrics(run, -1)
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
