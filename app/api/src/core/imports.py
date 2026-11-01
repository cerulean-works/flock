"""Copying a W&B entity's projects and runs into an organization, in the background.

One import per org at a time, run by the replica that started it (D81): it claims the org's
``org_imports`` row, saves its progress there and heartbeats it, and any replica marks an
import whose heartbeat stopped interrupted. The W&B API key is not saved, so an interrupted
import is resumed by starting it again, which skips the runs already copied.
"""

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.api.src.core.errors import Conflict, NotFound
from app.api.src.core.wandb_source import (
    SourceProject,
    WandbAuthError,
    WandbError,
    WandbSource,
    parse_json,
    parse_time,
)
from app.api.src.utils import loads_lenient

if TYPE_CHECKING:
    from app.api.src.core.accounts import User
    from app.api.src.core.store import ImportedRun, Org, Store

log = logging.getLogger(__name__)

# W&B run states -> the finished state the copy gets. A run still running in W&B keeps
# logging there, not here, so here it is over: it is copied as crashed.
STATES = {
    "finished": "finished",
    "failed": "failed",
    "killed": "failed",
    "crashed": "crashed",
}

# The run files copied besides the series and console log (no media or model files).
FILES = ("wandb-metadata.json", "config.yaml", "requirements.txt")
LOG_FILE = "output.log"
EVENTS_FILE = "wandb-events.jsonl"
FILE_MAX_BYTES = 64 << 20


@dataclass(eq=False)
class ImportJob:
    org: Org
    entity: str
    base_url: str
    state: str
    started_by: int | None
    started_at: float
    finished_at: float | None = None
    projects_total: int = 0
    projects_done: int = 0
    runs_total: int = 0
    runs_done: int = 0
    runs_skipped: int = 0  # already in the org (copied by an earlier attempt)
    runs_failed: int = 0
    current_project: str | None = None
    error: str | None = None
    task: asyncio.Task | None = field(default=None, repr=False)
    saved_at: float = field(default=0.0, repr=False)

    def info(self) -> dict[str, Any]:
        return {
            "entity": self.entity,
            "base_url": self.base_url,
            "state": self.state,
            "projects_total": self.projects_total,
            "projects_done": self.projects_done,
            "runs_total": self.runs_total,
            "runs_done": self.runs_done,
            "runs_skipped": self.runs_skipped,
            "runs_failed": self.runs_failed,
            "current_project": self.current_project,
            "error": self.error,
            "started_by": self.started_by,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class Imports:
    """Imports of every org (``store.imports``)."""

    # Runs fetched from W&B at once (writes still go one at a time per project).
    CONCURRENCY = 4
    # Seconds between progress saves while an import runs.
    SAVE_INTERVAL = 1.0
    # Seconds between heartbeats of a running import, and after which one without any is
    # taken for dead (its replica stopped) and marked interrupted.
    HEARTBEAT = 10.0
    STALE_AFTER = 60.0

    def __init__(self, store: Store) -> None:
        self.store = store
        self.jobs: dict[int, ImportJob] = {}  # this replica's running imports, by org pk

    async def load(self) -> None:
        await self.sweep()

    async def sweep(self) -> None:
        """Mark imports whose replica stopped heartbeating interrupted (D81)."""
        await self.store.db.execute(
            "UPDATE org_imports SET state = 'interrupted', finished_at = now() "
            "WHERE state = 'running' AND (heartbeat_at IS NULL "
            "OR heartbeat_at < now() - make_interval(secs => $1))",
            self.STALE_AFTER,
        )

    async def close(self) -> None:
        """Stop this replica's running imports (on shutdown); they are left interrupted."""
        for job in list(self.jobs.values()):
            await self._stop(job, "interrupted")

    async def get(self, org: Org) -> ImportJob | None:
        """The org's import, as this replica runs it or as its row says."""
        job = self.jobs.get(org.pk)
        if job is not None:
            return job
        record = await self.store.db.fetchrow(
            "SELECT entity, base_url, state, started_by, extract(epoch FROM started_at)::float8, "
            "extract(epoch FROM finished_at)::float8, projects_total, projects_done, "
            "runs_total, runs_done, runs_skipped, runs_failed, current_project, error "
            "FROM org_imports WHERE org_pk = $1",
            org.pk,
        )
        return ImportJob(org, *record) if record is not None else None

    async def start(
        self, org: Org, source: WandbSource, entity: str, user: User, members: dict[str, str]
    ) -> ImportJob:
        """Start copying ``entity`` into ``org`` (``members``: emails by W&B username, see
        ``WandbSource.check``). Takes ownership of ``source``, which it closes when done."""
        job = ImportJob(org, entity, source.base_url, "running", user.pk, time.time())
        try:
            if await self.store.org_by_pk(org.pk) is None:
                raise NotFound(f"organization {org.name} not found")
            if not await self._claim(job):
                raise Conflict(f"an import into {org.name} is already running")
        except BaseException:
            await source.close()
            raise
        self.jobs[org.pk] = job
        job.task = asyncio.get_running_loop().create_task(self._run(job, source, user, members))
        return job

    async def _claim(self, job: ImportJob) -> bool:
        """Make the org's row this import's, unless an import is running (on any replica)."""
        claimed = await self.store.db.fetchval(
            """
            INSERT INTO org_imports (org_pk, entity, base_url, state, started_by, started_at,
                                     heartbeat_at)
            VALUES ($1, $2, $3, 'running', $4, to_timestamp($5), now())
            ON CONFLICT (org_pk) DO UPDATE SET
                entity = excluded.entity, base_url = excluded.base_url, state = 'running',
                started_by = excluded.started_by, started_at = excluded.started_at,
                finished_at = NULL, projects_total = 0, projects_done = 0, runs_total = 0,
                runs_done = 0, runs_skipped = 0, runs_failed = 0, current_project = NULL,
                error = NULL, heartbeat_at = now(), cancel_requested = false
            WHERE org_imports.state <> 'running'
            RETURNING true
            """,
            job.org.pk,
            job.entity,
            job.base_url,
            job.started_by,
            job.started_at,
        )
        return bool(claimed)

    async def cancel(self, org: Org) -> ImportJob | None:
        """Stop the org's import if it is running; returns it (None if there never was one).
        An import another replica runs stops before its next run (``cancel_requested``)."""
        job = self.jobs.get(org.pk)
        if job is not None:
            await self._stop(job, "cancelled")
            return job
        await self.store.db.execute(
            "UPDATE org_imports SET cancel_requested = true, state = 'cancelled', "
            "finished_at = now() WHERE org_pk = $1 AND state = 'running'",
            org.pk,
        )
        return await self.get(org)

    async def _stop(self, job: ImportJob, state: str) -> None:
        task = job.task
        if task is None or task.done():
            return
        job.state = state
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    # ------------------------------------------------------------------ the import

    async def _save(self, job: ImportJob, force: bool = False) -> None:
        now = time.time()
        if not force and now - job.saved_at < self.SAVE_INTERVAL:
            return
        job.saved_at = now
        # A cancel from another replica wins: its state stays, and this import stops.
        cancelled = await self.store.db.fetchval(
            """
            UPDATE org_imports SET state = CASE WHEN cancel_requested THEN state ELSE $2 END,
                projects_total = $3, projects_done = $4, runs_total = $5, runs_done = $6,
                runs_skipped = $7, runs_failed = $8, current_project = $9, error = $10,
                finished_at = coalesce(finished_at, to_timestamp($11)), heartbeat_at = now()
            WHERE org_pk = $1
            RETURNING cancel_requested
            """,
            job.org.pk,
            job.state,
            job.projects_total,
            job.projects_done,
            job.runs_total,
            job.runs_done,
            job.runs_skipped,
            job.runs_failed,
            job.current_project,
            job.error,
            job.finished_at,
        )
        if cancelled and job.state == "running":
            job.state = "cancelled"
            raise asyncio.CancelledError

    async def _heartbeat(self, job: ImportJob) -> None:
        """Keep the row claimed while W&B is slow to answer (D81)."""
        while True:
            await asyncio.sleep(self.HEARTBEAT)
            try:
                await self._save(job, force=True)
            except asyncio.CancelledError:
                if job.state == "cancelled" and job.task is not None:
                    job.task.cancel()  # cancelled from another replica
                raise
            except Exception:
                log.exception("could not save the state of the import into %s", job.org.name)

    async def _run(
        self, job: ImportJob, source: WandbSource, user: User, members: dict[str, str]
    ) -> None:
        # W&B usernames -> Flock users, matched by email; other runs belong to the importer.
        by_email = {u.email: u.pk for u in await self.store.accounts.users()}
        owners = {name: by_email[email] for name, email in members.items() if email in by_email}
        heartbeat = asyncio.get_running_loop().create_task(self._heartbeat(job))
        try:
            projects = await source.projects(job.entity)
            job.projects_total = len(projects)
            job.runs_total = sum(p.run_count for p in projects)
            await self._save(job, force=True)
            for found in projects:
                job.current_project = found.name
                await self._project(job, source, found, owners, user.pk)
                job.projects_done += 1
                await self._save(job)
            job.state = "finished"
            job.current_project = None
        except asyncio.CancelledError:
            if job.state == "running":
                job.state = "cancelled"
        except NotFound:  # the org was deleted
            job.state = "cancelled"
        except Exception as exc:
            if not isinstance(exc, WandbError):
                log.exception("import of %s into %s failed", job.entity, job.org.name)
            job.state = "failed"
            job.error = str(exc)
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            job.finished_at = time.time()
            await source.close()
            try:
                await self._save(job, force=True)
            except asyncio.CancelledError:
                pass  # cancelled from another replica, which saved its state
            except Exception:
                log.exception("could not save the state of the import into %s", job.org.name)
            if self.jobs.get(job.org.pk) is job:
                del self.jobs[job.org.pk]
            log.info(
                "import of %s into %s %s: %d runs copied, %d skipped, %d failed",
                job.entity, job.org.name, job.state, job.runs_done, job.runs_skipped,
                job.runs_failed,
            )  # fmt: skip

    async def _project(
        self,
        job: ImportJob,
        source: WandbSource,
        found: SourceProject,
        owners: dict[str, int],
        importer: int,
    ) -> None:
        project = await self.store.import_project(job.org, found.name, None, found.created_at)
        limit = asyncio.Semaphore(self.CONCURRENCY)
        seen = 0

        async def copy(node: dict[str, Any]) -> None:
            async with limit:
                if await self.store.project_run(project, node["name"]) is not None:
                    job.runs_skipped += 1
                    return
                try:
                    data = await self._fetch(job, source, project.name, node, owners, importer)
                    if await self.store.import_run(project, data) is None:
                        job.runs_skipped += 1
                    else:
                        job.runs_done += 1
                except WandbAuthError, NotFound:
                    raise
                except Exception as exc:
                    if not isinstance(exc, WandbError):
                        log.exception("could not import run %s/%s", project.name, node["name"])
                    job.runs_failed += 1
                    job.error = f"{project.name}/{node['name']}: {exc}"
                await self._save(job)

        try:
            async with asyncio.TaskGroup() as group:
                async for page in source.runs(job.entity, found.name):
                    for node in page:
                        seen += 1
                        group.create_task(copy(node))
        except ExceptionGroup as group_error:  # a fatal error in one run stops them all
            raise group_error.exceptions[0] from None
        # runCount can lag behind (or ahead of) the runs actually listed.
        job.runs_total += seen - found.run_count

    async def _fetch(
        self,
        job: ImportJob,
        source: WandbSource,
        project: str,
        node: dict[str, Any],
        owners: dict[str, int],
        importer: int,
    ) -> ImportedRun:
        from app.api.src.core.store import ImportedRun

        entity, name = job.entity, node["name"]
        created_at = parse_time(node.get("createdAt")) or time.time()
        heartbeat_at = parse_time(node.get("heartbeatAt")) or created_at
        state = STATES.get(node.get("state") or "", "crashed")

        found = await source.files(entity, project, name, [*FILES, LOG_FILE, EVENTS_FILE])
        files: dict[str, bytes] = {}
        for file_name in [*FILES, LOG_FILE, EVENTS_FILE]:
            file = found.get(file_name)
            # W&B lists files it never stored, such as an empty wandb-events.jsonl on runs
            # whose system metrics live elsewhere (``events`` below still has them).
            if file is None or not file.url or not 0 < file.size <= FILE_MAX_BYTES:
                continue
            # A file that can't be read leaves the run without it, rather than uncopied.
            try:
                content = await source.download(file.url, FILE_MAX_BYTES)
            except WandbError as exc:
                log.warning("run %s/%s: skipped %s: %s", project, name, file_name, exc)
                job.error = f"{project}/{name}: skipped {file_name}: {exc}"
                continue
            if content is not None:
                files[file_name] = content

        events_file = files.pop(EVENTS_FILE, None)
        if events_file is not None:
            events = _lines(events_file)
        else:
            events = await source.events(entity, project, name)
        logs = _lines(files[LOG_FILE]) if LOG_FILE in files else []

        history: list[str] = []
        if node.get("historyLineCount"):
            keys = parse_json(node.get("historyKeys")) or {}
            summary = parse_json(node.get("summaryMetrics")) or {}
            last_step = keys.get("lastStep") if isinstance(keys, dict) else None
            if not isinstance(last_step, int):
                step = summary.get("_step") if isinstance(summary, dict) else None
                last_step = step if isinstance(step, int) else node["historyLineCount"]
            history = await source.history(entity, project, name, last_step)

        metadata: dict[str, Any] = {}
        if "wandb-metadata.json" in files:
            with contextlib.suppress(ValueError):
                parsed = loads_lenient(files["wandb-metadata.json"])
                metadata = parsed if isinstance(parsed, dict) else {}
        git = metadata.get("git")
        git = git if isinstance(git, dict) else {}

        config = parse_json(node.get("config"))
        summary = parse_json(node.get("summaryMetrics"))
        username = (node.get("user") or {}).get("username")
        return ImportedRun(
            name=name,
            display_name=node.get("displayName") or None,
            state=state,
            created_at=created_at,
            heartbeat_at=heartbeat_at,
            config=config if isinstance(config, dict) else None,
            summary=summary if isinstance(summary, dict) else None,
            tags=[str(tag) for tag in node.get("tags") or []],
            notes=node.get("notes") or None,
            group=node.get("group") or None,
            job_type=node.get("jobType") or None,
            host=_text(metadata.get("host")),
            program=_text(metadata.get("program")),
            commit=node.get("commit") or _text(git.get("commit")),
            sweep=node.get("sweepName") or None,
            exitcode=0 if state == "finished" else None,
            owner=owners.get(username, importer) if username else importer,
            history=history,
            events=events,
            logs=logs,
            files=files,
        )


def _lines(content: bytes) -> list[str]:
    text = content.decode("utf-8", errors="replace")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return [line.removesuffix("\r") for line in lines]


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
