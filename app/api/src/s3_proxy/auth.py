"""Who is calling and which buckets they reach (D54, D61), from Postgres with a short cache."""

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import asyncpg

from app.api.src import s3_credentials
from app.api.src.s3_proxy.sigv4 import SignedRequest, SigV4Error

log = logging.getLogger(__name__)

# The channel the database notifies (by triggers, see migration 0011) when keys,
# memberships, buckets or org policies change.
CHANNEL = "s3_invalidate"


@dataclass(frozen=True)
class Bucket:
    name: str
    upstream_name: str
    created_at: float


@dataclass(frozen=True)
class Org:
    pk: int
    max_ttl: int | None  # seconds; None: keys may live forever
    buckets: dict[str, Bucket]  # live buckets by name


@dataclass(frozen=True)
class Caller:
    """The holder of a verified S3 key, with what it may do in its org."""

    user: int
    org: Org
    role: str  # "admin" or "member"
    read_only: bool


@dataclass(frozen=True)
class _Key:
    pk: int
    user: int
    org: int
    read_only: bool
    created_at: float
    expires_at: float | None


class Directory:
    """Keys, roles and orgs' buckets and policies, cached for ``TTL`` seconds.

    Any notification on ``s3_invalidate`` drops the whole cache. While the listening
    connection is down nothing is cached, so a missed notification can't leave stale
    permissions behind. Keys are never cached as missing, so a new key works at once.
    """

    TTL = 10.0
    # s3_keys.last_used_at is written at most this often per key.
    LAST_USED_INTERVAL = 60.0

    def __init__(self, pool: asyncpg.Pool, database_url: str) -> None:
        self.pool = pool
        self.database_url = database_url
        self.server_secret = ""
        self._cache: dict[tuple, tuple[float, Any]] = {}
        self._generation = 0
        self._listening = False
        self._listener: asyncio.Task | None = None
        self._last_used: dict[int, float] = {}
        self._tasks: set[asyncio.Task] = set()

    async def start(self) -> None:
        self.server_secret = await self.pool.fetchval(
            "SELECT value FROM meta WHERE key = 's3_key_secret'"
        )
        if not self.server_secret:
            raise RuntimeError("the database has no s3_key_secret: start the API first")
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

    async def authenticate(self, signed: SignedRequest, now: float) -> Caller:
        """The caller behind a request's signature; raises ``SigV4Error`` if the key doesn't
        exist, the signature is wrong, the key has expired or its user left its org."""
        akid = signed.access_key_id
        key = None
        if akid.startswith(s3_credentials.PREFIX):
            key = await self._cached(("key", akid), lambda: self._load_key(akid))
        if key is None:
            raise SigV4Error(
                "InvalidAccessKeyId", "The AWS Access Key Id you provided does not exist"
            )
        if not signed.verify(s3_credentials.secret_for(self.server_secret, akid)):
            raise SigV4Error(
                "SignatureDoesNotMatch",
                "The request signature we calculated does not match the signature you provided",
            )
        org = await self._cached(("org", key.org), lambda: self._load_org(key.org))
        if org is None:  # deleted since the key was cached
            raise SigV4Error(
                "InvalidAccessKeyId", "The AWS Access Key Id you provided does not exist"
            )
        # Lowering the org's maximum lifetime applies to existing keys too (D56).
        too_old = org.max_ttl is not None and key.created_at + org.max_ttl <= now
        if (key.expires_at is not None and key.expires_at <= now) or too_old:
            raise SigV4Error("ExpiredToken", "The provided token has expired", 400)
        role = await self._cached(("role", key.user, key.org), lambda: self._load_role(key))
        if not role:  # removed from the org since the key was made
            raise SigV4Error("AccessDenied", "Access Denied")
        self._touch(key.pk)
        return Caller(key.user, org, role, key.read_only)

    async def _load_key(self, akid: str) -> _Key | None:
        row = await self.pool.fetchrow(
            "SELECT pk, user_pk, org_pk, read_only, extract(epoch FROM created_at)::float8, "
            "extract(epoch FROM expires_at)::float8 FROM s3_keys WHERE access_key_id = $1",
            akid,
        )
        return _Key(*row) if row is not None else None

    async def _load_org(self, pk: int) -> Org | None:
        max_ttl = await self.pool.fetchrow("SELECT s3_key_max_ttl FROM orgs WHERE pk = $1", pk)
        if max_ttl is None:
            return None
        rows = await self.pool.fetch(
            "SELECT name, upstream_name, extract(epoch FROM created_at)::float8 FROM s3_buckets "
            "WHERE org_pk = $1 AND deleted_at IS NULL",
            pk,
        )
        return Org(pk, max_ttl[0], {row[0]: Bucket(*row) for row in rows})

    async def _load_role(self, key: _Key) -> str | None:
        """The key's user's role in its org: super admins are admins of every org (D19).
        ``""`` (cached) for someone outside it."""
        row = await self.pool.fetchrow(
            "SELECT u.is_superadmin, m.role FROM users u "
            "LEFT JOIN memberships m ON m.user_pk = u.pk AND m.org_pk = $2 WHERE u.pk = $1",
            key.user,
            key.org,
        )
        if row is None:
            return ""
        return "admin" if row[0] else row[1] or ""

    def _touch(self, key_pk: int) -> None:
        now = time.monotonic()
        if now - self._last_used.get(key_pk, -self.LAST_USED_INTERVAL) < self.LAST_USED_INTERVAL:
            return
        self._last_used[key_pk] = now

        async def update() -> None:
            try:
                await self.pool.execute(
                    "UPDATE s3_keys SET last_used_at = now() WHERE pk = $1", key_pk
                )
            except Exception:
                log.exception("could not update last_used_at of S3 key %s", key_pk)

        task = asyncio.create_task(update())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
