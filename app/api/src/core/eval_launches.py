"""Evals launched from the web app on Modal (026, D188 to D192).

Each launch is a row of ``eval_launches`` and one call of the deployed worker's ``run_eval`` on
an H100 (``app/api/eval_worker.py``). The replica that started it waits on the call and
heartbeats; any replica takes over a launch whose heartbeat stopped, since the call goes on
running on Modal through a deploy. The worker logs to the tracker with a wandb key made for the
launch and deleted when it ends: a wandb key reaches tracking only (D72), and only meanwhile.
"""

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

from app.api.src.core.errors import BadGateway, Conflict, Forbidden, Invalid, NotFound
from app.api.src.core.hf_imports import Hub, HubError

if TYPE_CHECKING:
    from app.api.src.core.accounts import User
    from app.api.src.core.store import Org, Store

log = logging.getLogger(__name__)

COLUMNS = (
    "pk, org_pk, project, model, revision, sha, suites, tasks, add_bos, run, state, call_id, "
    "api_key_pk, cap_usd, usd_per_hour, flock_commit, error, started_by, "
    "extract(epoch FROM started_at)::float8, extract(epoch FROM finished_at)::float8"
)


@dataclass(eq=False)
class EvalLaunch:
    pk: int
    org_pk: int | None  # None once its org is purged: the row stays for the month's count
    project: str
    model: str
    revision: str
    sha: str
    suites: list[str]
    tasks: list[str]
    add_bos: bool
    run: str
    state: str
    call_id: str | None
    api_key_pk: int | None
    cap_usd: float
    usd_per_hour: float
    flock_commit: str | None
    error: str | None
    started_by: int | None
    started_at: float
    finished_at: float | None = None
    task: asyncio.Task | None = field(default=None, repr=False)

    def info(self) -> dict[str, Any]:
        return {
            "id": self.pk,
            "project": self.project,
            "model": self.model,
            "revision": self.revision,
            "sha": self.sha,
            "suites": self.suites,
            "tasks": self.tasks,
            "add_bos": self.add_bos,
            "run": self.run,
            "state": self.state,
            "cap_usd": self.cap_usd,
            "flock_commit": self.flock_commit,
            "error": self.error,
            "started_by": self.started_by,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class Modal:
    """The deployed worker's functions, called with the app's own Modal token (D191). Tests put
    a fake with the same methods in ``store.eval_launches.modal``."""

    APP: ClassVar[str] = "flock-eval"

    def __init__(self, token_id: str, token_secret: str) -> None:
        self.token_id = token_id
        self.token_secret = token_secret
        self._client: Any = None

    async def _client_once(self) -> Any:
        import modal  # only on servers that launch evals

        if self._client is None:
            self._client = await modal.Client.from_credentials.aio(self.token_id, self.token_secret)
        return self._client

    async def catalogue(self) -> dict[str, list[str]]:
        """The suites and tasks a launch may run: every dataset of each is public (D189)."""
        import modal

        client = await self._client_once()
        function = modal.Function.from_name(self.APP, "catalogue", client=client)
        return await function.remote.aio()

    async def spawn(self, timeout: int, env: dict[str, str | None], **kwargs: Any) -> str:
        """Start ``run_eval`` with ``kwargs``, ``env`` in its environment and at most
        ``timeout`` seconds to run; returns the call's id."""
        import modal

        client = await self._client_once()
        function = modal.Function.from_name(self.APP, "run_eval", client=client)
        call = await function.with_options(timeout=timeout, env=env).spawn.aio(**kwargs)
        return call.object_id

    async def wait(self, call_id: str, timeout: float) -> str | None:
        """The call's result (the flock commit that scored), or None if it's still running after
        ``timeout`` seconds or Modal can't be reached now. Raises what the call raised."""
        import modal

        call = modal.FunctionCall.from_id(call_id, client=await self._client_once())
        try:
            return await call.get.aio(timeout=timeout)
        except TimeoutError:  # Python's own: the poll's deadline, not the call's time limit
            return None
        except (
            modal.exception.ConnectionError,
            modal.exception.ServiceError,
            modal.exception.InternalError,
        ):
            # Modal's own trouble, not the call's failure: it goes on running, so ask again at
            # the next heartbeat.
            log.warning(f"Modal couldn't be reached about call {call_id}")
            await asyncio.sleep(timeout)
            return None

    async def cancel(self, call_id: str) -> None:
        import modal

        call = modal.FunctionCall.from_id(call_id, client=await self._client_once())
        await call.cancel.aio(terminate_containers=True)  # the GPU stops being billed now


class EvalLaunches:
    """The evals launched from the web app, of every org (``store.eval_launches``)."""

    HEARTBEAT: ClassVar[float] = 10.0
    STALE_AFTER: ClassVar[float] = 60.0
    CATALOGUE_TTL: ClassVar[float] = 3600.0
    LIST_LIMIT: ClassVar[int] = 100
    LIST_DAYS: ClassVar[int] = 30
    # Modal's longest function timeout.
    MAX_SECONDS: ClassVar[int] = 24 * 3600
    # What this month's launches may cost, in US dollars, over every org (one Modal workspace):
    # a running launch at its cap, whenever it started, and an ended one that ran into this
    # month at its whole time from start to end, which includes any wait for a GPU. A launch
    # across the month's start counts in both months, so it never undercounts (D188).
    SPENT: ClassVar[str] = (
        "SELECT coalesce(sum(CASE WHEN state = 'running' THEN cap_usd ELSE least(cap_usd, "
        "extract(epoch FROM finished_at - started_at) / 3600 * usd_per_hour) END), 0) "
        "FROM eval_launches WHERE state = 'running' "
        "OR finished_at >= date_trunc('month', now())"
    )

    def __init__(self, store: Store) -> None:
        self.store = store
        self.jobs: dict[int, EvalLaunch] = {}  # the launches this replica watches, by pk
        s = store.settings
        self.modal: Any = None
        # Without the public API URL the worker's wandb SDK would send the key to W&B itself.
        token_id, secret = s.modal_token_id, s.modal_token_secret
        if token_id and secret and s.api_public_url and s.eval_launch_usd_per_hour > 0:
            self.modal = Modal(token_id, secret)
        self._catalogue: tuple[float, dict[str, list[str]]] | None = None
        # Stands in for the Hub in tests.
        self.transport: Any = None

    def require(self) -> Any:
        """The Modal functions; raises ``NotFound`` when this server doesn't launch evals."""
        if self.modal is None:
            raise NotFound("this server doesn't launch evals")
        return self.modal

    # ------------------------------------------------------------------ lifecycle

    async def sweep(self) -> None:
        """Take over the launches whose replica stopped heartbeating (a deploy, a crash): their
        calls are still running on Modal."""
        if self.modal is None:
            return
        records = await self.store.db.fetch(
            "UPDATE eval_launches SET heartbeat_at = now() WHERE pk IN ("
            "SELECT pk FROM eval_launches WHERE state = 'running' AND heartbeat_at < now() - "
            "make_interval(secs => $1) FOR UPDATE SKIP LOCKED) "
            f"RETURNING {COLUMNS}",
            self.STALE_AFTER,
        )
        for record in records:
            job = EvalLaunch(*record)
            self.jobs[job.pk] = job
            job.task = asyncio.get_running_loop().create_task(self._watch(job))

    async def close(self) -> None:
        """Stop watching (on shutdown). The launches go on running, and another replica takes
        them over."""
        for job in list(self.jobs.values()):
            if job.task is not None:
                job.task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await job.task

    # ------------------------------------------------------------------ reads

    async def get(self, org: Org, pk: int) -> EvalLaunch:
        record = await self.store.db.fetchrow(
            f"SELECT {COLUMNS} FROM eval_launches WHERE pk = $1 AND org_pk = $2", pk, org.pk
        )
        if record is None:
            raise NotFound(f"eval launch {pk} not found")
        return EvalLaunch(*record)

    async def recent(self, org: Org, project: str | None = None) -> list[EvalLaunch]:
        """The org's running launches and those of the last month, of ``project`` if given,
        newest first."""
        records = await self.store.db.fetch(
            f"SELECT {COLUMNS} FROM eval_launches WHERE org_pk = $1 "
            "AND ($4::text IS NULL OR project = $4) AND (state = 'running' "
            "OR started_at > now() - make_interval(days => $2)) ORDER BY pk DESC LIMIT $3",
            org.pk,
            self.LIST_DAYS,
            self.LIST_LIMIT,
            project,
        )
        return [EvalLaunch(*r) for r in records]

    async def catalogue(self) -> dict[str, list[str]]:
        """The suites and tasks a launch may run, from the deployed worker, kept for an hour:
        the list changes only with a deploy."""
        modal = self.require()
        now = time.monotonic()
        if self._catalogue is None or self._catalogue[0] < now:
            try:
                found = await modal.catalogue()
            except Exception as e:
                raise BadGateway(f"Modal couldn't list the suites and tasks: {e}") from e
            self._catalogue = (now + self.CATALOGUE_TTL, found)
        return self._catalogue[1]

    # ------------------------------------------------------------------ launching

    async def start(
        self,
        org: Org,
        user: User,
        project: str,
        model: str,
        revision: str,
        suites: list[str],
        tasks: list[str],
        add_bos: bool,
    ) -> EvalLaunch:
        """Check the launch and start it. Raises ``Invalid`` for a suite or task that isn't
        public, ``HubError`` for a model the Hub doesn't serve to anyone, and ``Conflict`` past
        the monthly cap."""
        modal = self.require()
        s = self.store.settings
        public = await self.catalogue()
        refused = [n for n in suites if n not in public["suites"]]
        refused += [n for n in tasks if n not in public["tasks"]]
        if refused:
            raise Invalid(
                f"{', '.join(refused)}: not a suite or task whose datasets are all public. "
                "Only those are launched on Modal."
            )

        # Asked without a token, as the worker reads it: a gated or private model can't load.
        async with Hub(s.hf_endpoint, s.hf_datasets_server, None, self.transport) as hub:
            info = await hub.info("model", model, revision)
        if info.get("gated") or info.get("private"):
            raise HubError(f"{model} is gated or private. Only public models are launched.", 403)

        # One launch at a time over the server, so two can't both fit under the cap's last
        # dollars. A running launch counts at its cap until it ends.
        cap = s.eval_launch_max_usd
        async with self.store.db.pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext('eval_launches'))")
            spent = await conn.fetchval(self.SPENT)
            if spent + cap > s.eval_launch_monthly_usd:
                raise Conflict(
                    f"This month's launches may cost up to ${spent:.2f} of "
                    f"${s.eval_launch_monthly_usd:.2f}, which leaves no room for another "
                    f"${cap:.2f} launch."
                )
            record = await conn.fetchrow(
                "INSERT INTO eval_launches (org_pk, project, model, revision, sha, suites, "
                "tasks, add_bos, run, state, cap_usd, usd_per_hour, started_by, started_at, "
                "heartbeat_at) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, '', 'running', $9, $10, "
                "$11, now(), now()) "
                "RETURNING pk",
                org.pk,
                project,
                model,
                revision,
                info["sha"],
                suites,
                tasks,
                add_bos,
                cap,
                s.eval_launch_usd_per_hour,
                user.pk,
            )
        pk = record[0]

        # The run is named now, so the launch links to it before the worker starts.
        api_key, key = await self.store.accounts.create_api_key(user, f"eval launch {pk}")
        await self.store.db.execute(
            "UPDATE eval_launches SET run = $2, api_key_pk = $3 WHERE pk = $1",
            pk,
            f"eval-launch-{pk}",
            api_key.pk,
        )
        job = await self.get(org, pk)
        env: dict[str, str | None] = {
            "WANDB_API_KEY": key,
            "WANDB_BASE_URL": s.api_public_url,
            "WANDB_ENTITY": org.name,
            "WANDB_RUN_ID": job.run,
        }
        # The cap is the timeout: a launch can't run past what it may cost.
        timeout = min(int(cap / s.eval_launch_usd_per_hour * 3600), self.MAX_SECONDS)
        try:
            job.call_id = await modal.spawn(
                timeout,
                env,
                model=f"{model}@{job.sha}",
                suites=suites,
                tasks=tasks,
                add_bos=add_bos,
                project=project,
            )
        except Exception as e:
            await self._end(job, "failed", error=f"Modal refused the launch: {e}")
            raise BadGateway(f"Modal refused the launch: {e}") from e
        # Taken over while the call was being made (a spawn slower than STALE_AFTER): the row
        # ended without it, so the call is stopped.
        attached = await self.store.db.fetchval(
            "UPDATE eval_launches SET call_id = $2 WHERE pk = $1 AND state = 'running' "
            "RETURNING pk",
            pk,
            job.call_id,
        )
        if attached is None:
            await modal.cancel(job.call_id)
            return await self.get(org, pk)
        self.jobs[pk] = job
        job.task = asyncio.get_running_loop().create_task(self._watch(job))
        return job

    # ------------------------------------------------------------------ cancelling

    async def cancel(self, org: Org, pk: int, user: User) -> EvalLaunch:
        """Stop a running launch (who started it, or an admin). What it logged so far stays."""
        job = await self.get(org, pk)
        if job.started_by != user.pk and self.store.accounts.role(user, org) != "admin":
            raise Forbidden("Only who launched an eval, or an admin, can cancel it.")
        if job.state != "running":
            return job
        # Its call is still being made: ending the row now would leave the call unwatched.
        if job.call_id is None:
            raise Conflict("The launch is still starting. Cancel it again in a moment.")
        try:
            await self.require().cancel(job.call_id)
        except Exception as e:
            raise BadGateway(f"Modal couldn't cancel the launch: {e}") from e
        await self._end(job, "cancelled")
        return await self.get(org, pk)

    # ------------------------------------------------------------------ running

    async def _end(
        self, job: EvalLaunch, state: str, error: str | None = None, commit: str | None = None
    ) -> None:
        """Record how ``job`` ended, unless another replica already did, and delete its key."""
        async with self.store.db.pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "UPDATE eval_launches SET state = $2, error = $3, flock_commit = $4, "
                "finished_at = now() WHERE pk = $1 AND state = 'running'",
                job.pk,
                state,
                error,
                commit,
            )
            # Whoever ended it, the key goes: the worker is done with it. By name too, for a
            # key made just before its replica stopped, before the row named it.
            await conn.execute(
                "DELETE FROM api_keys k USING eval_launches l WHERE l.pk = $1 "
                "AND (k.pk = l.api_key_pk OR (k.user_pk = l.started_by "
                "AND k.name = 'eval launch ' || l.pk))",
                job.pk,
            )

    async def _watch(self, job: EvalLaunch) -> None:
        try:
            if job.call_id is None:  # its replica stopped between the insert and the spawn
                await self._end(job, "failed", error="The launch was cut short before it started.")
                return
            while True:
                try:
                    commit = await self.modal.wait(job.call_id, self.HEARTBEAT)
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001 - the worker's own error, for the row
                    await self._end(job, "failed", error=str(e) or type(e).__name__)
                    return
                if commit is not None:
                    await self._end(job, "finished", commit=commit)
                    return
                # Still running. A launch cancelled or ended elsewhere isn't watched any more.
                beat = await self.store.db.fetchrow(
                    "UPDATE eval_launches l SET heartbeat_at = now() "
                    "WHERE pk = $1 AND state = 'running' RETURNING coalesce("
                    "(SELECT deleted_at IS NOT NULL FROM orgs WHERE pk = l.org_pk), true)",
                    job.pk,
                )
                if beat is None:
                    return
                # Its org was deleted: the call stops, retried every heartbeat until Modal
                # confirms, so a deletion never leaves a GPU running unwatched.
                if beat[0]:
                    try:
                        await self.modal.cancel(job.call_id)
                    except Exception:
                        log.exception(f"could not cancel eval launch {job.pk} of a deleted org")
                        continue
                    await self._end(job, "cancelled")
                    return
        except asyncio.CancelledError:
            raise  # this replica is stopping: the launch goes on, and another takes it over
        except Exception:
            log.exception("could not watch eval launch %d", job.pk)
        finally:
            if self.jobs.get(job.pk) is job:
                del self.jobs[job.pk]
