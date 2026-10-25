"""Who is calling and what they may do (D44, D46), from Postgres with a short cache."""

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import asyncpg

from app.api.src import hub_credentials
from app.api.src.hub_proxy.sigv4 import SignedRequest, SigV4Error

log = logging.getLogger(__name__)

# The channel the API notifies when keys, memberships or datasets change.
CHANNEL = "hub_invalidate"


@dataclass(frozen=True)
class Caller:
    """The holder of a verified S3 credential."""

    user: int
    read_only: bool
    # The only org a long-lived key may reach, if it is scoped to one.
    org_pk: int | None = None
    mirror_writer: bool = False


@dataclass(frozen=True)
class Dataset:
    pk: int
    uuid: str
    org_pk: int
    org: str
    name: str
    visibility: str


@dataclass(frozen=True)
class Access:
    read: bool = False
    write: bool = False
    delete: bool = False


@dataclass(frozen=True)
class _Key:
    pk: int
    user: int
    read_only: bool
    org_pk: int | None


@dataclass(frozen=True)
class _User:
    superadmin: bool
    roles: dict[int, str]  # org pk -> role


class Directory:
    """Keys, users' roles and datasets, cached for ``TTL`` seconds.

    The API sends ``NOTIFY hub_invalidate`` whenever a key is deleted, a membership changes or
    a dataset is renamed, deleted or has its visibility changed, which drops the whole cache.
    While the listening connection is down nothing is cached, so a missed notification can't
    leave stale permissions behind. Things that only start existing (new keys, datasets) are
    never cached as missing, so they work at once without a notification.
    """

    TTL = 10.0
    # hub_keys.last_used_at is written at most this often per key.
    LAST_USED_INTERVAL = 60.0

    def __init__(self, pool: asyncpg.Pool, database_url: str, mirror_org: str) -> None:
        self.pool = pool
        self.database_url = database_url
        self.mirror_org = mirror_org
        self.server_secret = ""
        self._cache: dict[tuple, tuple[float, Any]] = {}
        self._generation = 0
        self._listening = False
        self._listener: asyncio.Task | None = None
        self._last_used: dict[int, float] = {}
        self._tasks: set[asyncio.Task] = set()

    async def start(self) -> None:
        self.server_secret = await self.pool.fetchval(
            "SELECT value FROM meta WHERE key = 'hub_key_secret'"
        )
        if not self.server_secret:
            raise RuntimeError("the database has no hub_key_secret; apply the migrations first")
        connected = asyncio.Event()
        self._listener = asyncio.create_task(self._listen(connected))
        # Serve as soon as the first connection is listening (or failed to; then uncached).
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(connected.wait(), 10)

    async def close(self) -> None:
        if self._listener is not None:
            self._listener.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._listener
        for task in list(self._tasks):
            with contextlib.suppress(Exception):
                await task

    def invalidate(self, *_: object) -> None:
        self._generation += 1
        self._cache.clear()

    async def _listen(self, connected: asyncio.Event) -> None:
        while True:
            closed = asyncio.Event()
            conn = None
            try:
                conn = await asyncpg.connect(self.database_url)
                conn.add_termination_listener(lambda _, closed=closed: closed.set())
                await conn.add_listener(CHANNEL, self.invalidate)
                self.invalidate()  # anything cached before now may have missed a notification
                self._listening = True
                connected.set()
                await closed.wait()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("listening for %s failed; retrying", CHANNEL)
                connected.set()
            finally:
                self._listening = False
                self.invalidate()
                if conn is not None and not conn.is_closed():
                    await conn.close()
            await asyncio.sleep(1)

    async def _cached(self, key: tuple, load: Callable[[], Awaitable[Any]]) -> Any:
        hit = self._cache.get(key)
        now = time.monotonic()
        if hit is not None and hit[0] > now:
            return hit[1]
        generation = self._generation
        value = await load()
        # Not if a notification arrived while loading: the value may predate the change.
        if value is not None and self._listening and generation == self._generation:
            self._cache[key] = (now + self.TTL, value)
        return value

    # ------------------------------------------------------------------ authentication

    async def authenticate(self, signed: SignedRequest, now: float) -> Caller:
        """The caller behind a request's signature; raises ``SigV4Error`` if it isn't valid."""
        akid = signed.access_key_id
        if akid.startswith(hub_credentials.TEMP_PREFIX):
            if signed.session_token is None:
                raise SigV4Error("InvalidToken", "temporary credentials need a session token", 400)
            session = hub_credentials.read_session_token(
                self.server_secret, signed.session_token, now
            )
            if session is None or session.access_key_id != akid:
                raise SigV4Error("InvalidToken", "the session token is invalid or expired", 400)
            caller = Caller(session.user, session.read_only, None, session.mirror_writer)
            key = None
        elif akid.startswith(hub_credentials.KEY_PREFIX):
            key = await self._cached(("key", akid), lambda: self._load_key(akid))
            if key is None:
                raise SigV4Error("InvalidAccessKeyId", "the access key id does not exist")
            caller = Caller(key.user, key.read_only, key.org_pk)
        else:
            raise SigV4Error("InvalidAccessKeyId", "the access key id does not exist")
        if not signed.verify(hub_credentials.secret_for(self.server_secret, akid)):
            raise SigV4Error(
                "SignatureDoesNotMatch",
                "the request signature we calculated does not match the signature you provided",
            )
        if key is not None:
            self._touch(key.pk)
        return caller

    async def _load_key(self, akid: str) -> _Key | None:
        row = await self.pool.fetchrow(
            "SELECT pk, user_pk, read_only, org_pk FROM hub_keys WHERE access_key_id = $1", akid
        )
        return _Key(row["pk"], row["user_pk"], row["read_only"], row["org_pk"]) if row else None

    def _touch(self, key_pk: int) -> None:
        now = time.monotonic()
        if now - self._last_used.get(key_pk, -self.LAST_USED_INTERVAL) < self.LAST_USED_INTERVAL:
            return
        self._last_used[key_pk] = now

        async def update() -> None:
            try:
                await self.pool.execute(
                    "UPDATE hub_keys SET last_used_at = now() WHERE pk = $1", key_pk
                )
            except Exception:
                log.exception("could not update last_used_at of hub key %s", key_pk)

        task = asyncio.create_task(update())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # ------------------------------------------------------------------ authorization

    async def dataset(self, org: str, name: str) -> Dataset | None:
        """The live dataset ``org/name``, if there is one."""
        return await self._cached(("dataset", org, name), lambda: self._load_dataset(org, name))

    async def _load_dataset(self, org: str, name: str) -> Dataset | None:
        row = await self.pool.fetchrow(
            """
            SELECT d.pk, d.uuid, d.org_pk, d.visibility
            FROM datasets d JOIN orgs o ON o.pk = d.org_pk
            WHERE o.name = $1 AND d.name = $2 AND d.deleted_at IS NULL
            """,
            org,
            name,
        )
        if row is None:
            return None
        return Dataset(row["pk"], str(row["uuid"]), row["org_pk"], org, name, row["visibility"])

    async def _load_user(self, user: int) -> _User | None:
        superadmin = await self.pool.fetchval("SELECT is_superadmin FROM users WHERE pk = $1", user)
        if superadmin is None:
            return None
        rows = await self.pool.fetch(
            "SELECT org_pk, role FROM memberships WHERE user_pk = $1", user
        )
        return _User(superadmin, {row["org_pk"]: row["role"] for row in rows})

    async def access(self, caller: Caller, dataset: Dataset) -> Access:
        """What ``caller`` may do to ``dataset`` over S3."""
        if caller.org_pk is not None and caller.org_pk != dataset.org_pk:
            return Access()
        user = await self._cached(("user", caller.user), lambda: self._load_user(caller.user))
        if user is None:  # the account was deleted
            return Access()
        role = "admin" if user.superadmin else user.roles.get(dataset.org_pk)
        mirror = caller.mirror_writer and dataset.org == self.mirror_org
        writer = not caller.read_only and (role is not None or mirror)
        return Access(
            read=role is not None or mirror or dataset.visibility == "internal",
            write=writer,
            delete=writer and role == "admin",
        )
