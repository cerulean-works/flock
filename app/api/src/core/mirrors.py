"""Hugging Face mirrors (D50): server-side imports of small HF datasets into the mirror org.

A mirror is one ``hub_mirrors`` row per (repo, config, sha). Importing runs ``python -m
flock.hub.importer`` as a subprocess, so a crash or a leak in ``datasets`` can't take the API
down, with mirror-writer credentials (D46) for the mirror dataset. Concurrent requests for the
same commit share one import; a restart marks running imports failed, and asking again starts
over.
"""

import asyncio
import json
import logging
import os
import shlex
import sys
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from app.api.src.core.errors import NotFound

if TYPE_CHECKING:
    from app.api.src.core.accounts import User
    from app.api.src.core.datasets import Dataset
    from app.api.src.core.store import Store

log = logging.getLogger(__name__)


class TooLarge(Exception):
    """The dataset is above ``FLOCK_HUB_AUTO_IMPORT_MAX_BYTES``; ``command`` imports it instead."""

    def __init__(self, message: str, command: str) -> None:
        super().__init__(message)
        self.message = message
        self.command = command


class UpstreamError(Exception):
    """Hugging Face couldn't be asked (network, rate limits); HTTP 502."""


@dataclass(eq=False)
class Mirror:
    pk: int
    repo: str
    config: str  # '' without a config, as stored
    sha: str
    dataset: int | None
    state: str  # running, finished or failed
    version: int | None
    error: str | None
    requested_by: int | None
    started_at: float
    finished_at: float | None
    # The importer's last progress line, while it runs (not stored).
    progress: dict[str, Any] | None = None


class Mirrors:
    """Hugging Face mirrors (``store.mirrors``). Rows are read from the database when asked;
    running imports are kept here with their progress."""

    COLUMNS: ClassVar[str] = (
        "pk, repo, config, sha, dataset_pk, state, version, error, requested_by, "
        "extract(epoch FROM started_at)::float8, extract(epoch FROM finished_at)::float8"
    )
    # Characters of the importer's stderr kept as a failed mirror's error.
    ERROR_CHARS: ClassVar[int] = 2000

    def __init__(self, store: Store) -> None:
        self.store = store
        # Held from the dedupe lookup to the insert, so concurrent requests share one import.
        self._lock = asyncio.Lock()
        self._running: dict[int, Mirror] = {}
        self._tasks: set[asyncio.Task] = set()

    async def load(self) -> None:
        """Imports don't survive a restart: their subprocess died with the server."""
        await self.store.db.execute(
            "UPDATE hub_mirrors SET state = 'failed', finished_at = now(), "
            "error = 'the server restarted during the import; ask again to start over' "
            "WHERE state = 'running'"
        )

    async def close(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def flush(self) -> None:
        """Wait for running imports; used by tests."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    # ------------------------------------------------------------------ reading

    @property
    def org_name(self) -> str:
        return self.store.settings.hub_mirror_org

    @staticmethod
    def dataset_name(repo: str, config: str | None) -> str:
        """``<owner>--<name>``, plus ``--<config>`` (as ``flock.hub.importer.mirror_name``)."""
        name = repo.replace("/", "--")
        return f"{name}--{config}" if config else name

    def info(self, mirror: Mirror) -> dict[str, Any]:
        dataset = self.store.datasets.by_pk.get(mirror.dataset) if mirror.dataset else None
        name = (
            f"{dataset.org.name}/{dataset.name}"
            if dataset is not None
            else f"{self.org_name}/{self.dataset_name(mirror.repo, mirror.config)}"
        )
        running = self._running.get(mirror.pk)
        return {
            "id": mirror.pk,
            "state": mirror.state,
            "repo": mirror.repo,
            "config": mirror.config or None,
            "sha": mirror.sha,
            "dataset": name,
            "version": mirror.version,
            "error": mirror.error,
            "progress": running.progress if running is not None else None,
            "started_at": mirror.started_at,
            "finished_at": mirror.finished_at,
        }

    async def get(self, pk: int) -> Mirror:
        record = await self.store.db.fetchrow(
            f"SELECT {self.COLUMNS} FROM hub_mirrors WHERE pk = $1", pk
        )
        if record is None:
            raise NotFound(f"mirror {pk} not found")
        return Mirror(*record)

    async def of_repo(self, repo: str, config: str | None) -> list[Mirror]:
        """The imports of ``repo`` (and ``config``), newest first."""
        return [
            Mirror(*record)
            for record in await self.store.db.fetch(
                f"SELECT {self.COLUMNS} FROM hub_mirrors WHERE repo = $1 AND config = $2 "
                "ORDER BY started_at DESC",
                repo,
                config or "",
            )
        ]

    async def _find(self, repo: str, config: str | None, sha: str) -> Mirror | None:
        record = await self.store.db.fetchrow(
            f"SELECT {self.COLUMNS} FROM hub_mirrors WHERE repo = $1 AND config = $2 AND sha = $3",
            repo,
            config or "",
            sha,
        )
        return Mirror(*record) if record is not None else None

    # ------------------------------------------------------------------ importing

    async def request(
        self, user: User, repo: str, config: str | None, revision: str | None
    ) -> Mirror:
        """The mirror of ``repo`` at ``revision`` (default ``main``): an existing finished or
        running one, else a new import. Raises NotFound for unknown repos, ValueError for
        unknown configs, TooLarge and UpstreamError."""
        # Imported here: flock.hub pulls in `datasets`, which is slow to import.
        from flock.hub import importer

        if importer.is_sha(revision):  # known commits need no call to Hugging Face
            assert revision is not None
            found = await self._find(repo, config, revision)
            if found is not None and found.state != "failed":
                return found
        source = importer.source(self.store.settings.hub_hf_token)
        try:
            plan = await asyncio.to_thread(importer.plan, repo, config, revision, src=source)
        except importer.RepoNotFound as e:
            raise NotFound(str(e)) from e
        except ValueError:
            raise
        except Exception as e:
            raise UpstreamError(f"asking Hugging Face about {repo} failed: {e}") from e

        async with self._lock:
            found = await self._find(repo, config, plan.sha)
            if found is not None and found.state != "failed":
                return found
            limit = self.store.settings.hub_auto_import_max_bytes
            name = self.dataset_name(repo, config)
            if plan.total_bytes > limit:
                command = ["flock-hub", "import", "--repo", repo]
                if config:
                    command += ["--config", config]
                command += ["--revision", plan.sha, "--target", f"{self.org_name}/{name}"]
                command += ["--workdir", name]
                raise TooLarge(
                    f"{repo} has {plan.total_bytes} bytes of data files, more than the "
                    f"{limit} this server imports by itself",
                    shlex.join(command),
                )
            dataset = await self._dataset(name, repo, user)
            record = await self.store.db.fetchrow(
                "INSERT INTO hub_mirrors (repo, config, sha, dataset_pk, state, requested_by, "
                "started_at) VALUES ($1, $2, $3, $4, 'running', $5, now()) "
                "ON CONFLICT (repo, config, sha) DO UPDATE SET dataset_pk = EXCLUDED.dataset_pk, "
                "state = 'running', version = NULL, error = NULL, "
                "requested_by = EXCLUDED.requested_by, started_at = now(), finished_at = NULL "
                f"RETURNING {self.COLUMNS}",
                repo,
                config or "",
                plan.sha,
                dataset.pk,
                user.pk,
            )
            assert record is not None
            mirror = Mirror(*record)
            self._running[mirror.pk] = mirror
            task = asyncio.create_task(self._import(mirror, dataset, user))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        return mirror

    async def _dataset(self, name: str, repo: str, user: User) -> Dataset:
        """The mirror dataset, created (internal) on the first import of the repo."""
        datasets = self.store.datasets
        org = self.store.orgs[self.org_name]
        found = datasets.get(org, name)
        if found is not None:
            return found
        return await datasets.create(
            org,
            name,
            user,
            description=f"Mirror of https://huggingface.co/datasets/{repo}",
            visibility="internal",
        )

    async def _import(self, mirror: Mirror, dataset: Dataset, user: User) -> None:
        process = None
        try:
            uri, options = self.store.datasets.write_target(dataset, user, mirror_writer=True)
            job = {
                "repo": mirror.repo,
                "config": mirror.config or None,
                "sha": mirror.sha,
                "uri": uri,
                "storage_options": options,
            }
            env = dict(os.environ)
            if self.store.settings.hub_hf_token:
                env["HF_TOKEN"] = self.store.settings.hub_hf_token
            # The job goes through stdin: it holds credentials, which argv would show to `ps`.
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "flock.hub.importer",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            assert process.stdin and process.stdout and process.stderr
            process.stdin.write(json.dumps(job).encode())
            await process.stdin.drain()
            process.stdin.close()
            stderr = asyncio.create_task(process.stderr.read())
            result = None
            async for line in process.stdout:
                try:
                    message = json.loads(line)
                except ValueError:
                    continue  # not ours (a library printing to stdout)
                if isinstance(message, dict) and "versions" in message:
                    result = message
                elif isinstance(message, dict) and "stage" in message:
                    mirror.progress = message
            code = await process.wait()
            errors = (await stderr).decode(errors="replace")
            if code != 0 or result is None:
                # The importer's own message is its last line; libraries log above it.
                lines = [line for line in errors.splitlines() if line.strip()]
                raise RuntimeError(lines[-1] if lines else f"the importer exited with {code}")
            versions = [int(v) for v in result["versions"]]
            # Through the proxy, it records the author; written around it, nobody does.
            for version in versions:
                await self.store.datasets._record(dataset, version, user, None)
            await self._finish(mirror, "finished", versions[-1], None)
            dataset.mirror = {
                "repo": mirror.repo,
                "config": mirror.config or None,
                "sha": mirror.sha,
            }
        except asyncio.CancelledError:
            if process is not None and process.returncode is None:
                process.kill()
            raise
        except Exception as e:
            log.warning("mirroring %s@%s failed", mirror.repo, mirror.sha, exc_info=True)
            await self._finish(mirror, "failed", None, str(e)[-self.ERROR_CHARS :])
        finally:
            self._running.pop(mirror.pk, None)

    async def _finish(
        self, mirror: Mirror, state: str, version: int | None, error: str | None
    ) -> None:
        await self.store.db.execute(
            "UPDATE hub_mirrors SET state = $2, version = $3, error = $4, finished_at = now() "
            "WHERE pk = $1",
            mirror.pk,
            state,
            version,
            error,
        )
        mirror.state, mirror.version, mirror.error = state, version, error
        mirror.finished_at = time.time()
