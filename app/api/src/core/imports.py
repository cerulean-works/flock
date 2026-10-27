"""Copying a W&B entity's projects and runs into an organization, in the background.

One import per org at a time. Its progress is kept in memory and saved to ``org_imports``;
the W&B API key is not saved, so an import cut short by a restart is resumed by starting it
again, which skips the runs already copied.
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

    def __init__(self, store: Store) -> None:
        self.store = store
        self.jobs: dict[int, ImportJob] = {}  # by org pk

    async def load(self) -> None:
        store = self.store
        # Nothing runs before startup: imports still marked running were cut short.
        await store.db.execute(
            "UPDATE org_imports SET state = 'interrupted', finished_at = now() "
            "WHERE state = 'running'"
        )
        for record in await store.db.fetch(
            "SELECT org_pk, entity, base_url, state, started_by, "
            "extract(epoch FROM started_at)::float8, extract(epoch FROM finished_at)::float8, "
            "projects_total, projects_done, runs_total, runs_done, runs_skipped, runs_failed, "
            "current_project, error FROM org_imports"
        ):
            org_pk, entity, base_url, state, started_by, started_at, finished_at, *rest = record
            job = ImportJob(
                store.orgs_by_pk[org_pk], entity, base_url, state, started_by, started_at,
                finished_at, *rest,
            )  # fmt: skip
            self.jobs[org_pk] = job

    async def close(self) -> None:
        """Stop running imports (on shutdown); they are left interrupted."""
        for job in list(self.jobs.values()):
            await self._stop(job, "interrupted")

    def get(self, org: Org) -> ImportJob | None:
        return self.jobs.get(org.pk)

    async def start(
        self, org: Org, source: WandbSource, entity: str, user: User, members: dict[str, str]
    ) -> ImportJob:
        """Start copying ``entity`` into ``org`` (``members``: emails by W&B username, see
        ``WandbSource.check``). Takes ownership of ``source``, which it closes when done."""
        current = self.jobs.get(org.pk)
        if org.deleted or (current is not None and current.state == "running"):
            await source.close()
            if org.deleted:
                raise NotFound(f"organization {org.name} not found")
            raise Conflict(f"an import into {org.name} is already running")
        job = ImportJob(org, entity, source.base_url, "running", user.pk, time.time())
        self.jobs[org.pk] = job  # before awaiting, so a second start is refused
        try:
            await self._insert(job)
        except BaseException:
            await source.close()
            if current is None:
                del self.jobs[org.pk]
            else:
                self.jobs[org.pk] = current
            raise
        job.task = asyncio.get_running_loop().create_task(self._run(job, source, user, members))
        return job

    async def _insert(self, job: ImportJob) -> None:
        await self.store.db.execute(
            """
            INSERT INTO org_imports (org_pk, entity, base_url, state, started_by, started_at)
            VALUES ($1, $2, $3, 'running', $4, to_timestamp($5))
            ON CONFLICT (org_pk) DO UPDATE SET
                entity = excluded.entity, base_url = excluded.base_url, state = 'running',
                started_by = excluded.started_by, started_at = excluded.started_at,
                finished_at = NULL, projects_total = 0, projects_done = 0, runs_total = 0,
                runs_done = 0, runs_skipped = 0, runs_failed = 0, current_project = NULL,
                error = NULL
            """,
            job.org.pk,
            job.entity,
            job.base_url,
            job.started_by,
            job.started_at,
        )

    async def cancel(self, org: Org) -> ImportJob | None:
        """Stop the org's import if it is running; returns it (None if there never was one)."""
        job = self.jobs.get(org.pk)
        if job is not None:
            await self._stop(job, "cancelled")
        return job

    def forget(self, org: Org) -> None:
        """Drop a deleted org's import (its row went with the org)."""
        self.jobs.pop(org.pk, None)

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
        await self.store.db.execute(
            """
            UPDATE org_imports SET state = $2, projects_total = $3, projects_done = $4,
                runs_total = $5, runs_done = $6, runs_skipped = $7, runs_failed = $8,
                current_project = $9, error = $10, finished_at = to_timestamp($11)
            WHERE org_pk = $1
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

    async def _run(
        self, job: ImportJob, source: WandbSource, user: User, members: dict[str, str]
    ) -> None:
        # W&B usernames -> Flock users, matched by email; other runs belong to the importer.
        by_email = {u.email: u.pk for u in await self.store.accounts.users()}
        owners = {name: by_email[email] for name, email in members.items() if email in by_email}
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
            job.finished_at = time.time()
            await source.close()
            if not job.org.deleted:
                try:
                    await self._save(job, force=True)
                except Exception:
                    log.exception("could not save the state of the import into %s", job.org.name)
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
        if job.org.deleted:
            raise NotFound(f"organization {job.org.name} not found")
        project = await self.store.import_project(job.org, found.name, None, found.created_at)
        limit = asyncio.Semaphore(self.CONCURRENCY)
        seen = 0

        async def copy(node: dict[str, Any]) -> None:
            async with limit:
                if project.deleted:
                    raise NotFound(f"project {project.name} not found")
                if node["name"] in project.by_name:
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
