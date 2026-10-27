"""S3 access (D53-D57): org buckets, S3 keys and the key lifetimes orgs set.

The S3 proxy (``app.api.src.s3_proxy``) serves the S3 API itself; this side keeps its tables.
Buckets and keys are read from the database when asked for, never kept in memory: the proxy
reads the same rows. The org's two lifetimes live on ``Org``.
"""

import asyncio
import contextlib
import logging
import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

import httpx
import orjson

from app.api.src import s3_credentials
from app.api.src.core.errors import BadGateway, Conflict, Invalid, NotFound
from app.api.src.s3_proxy.upstream import Upstream, UpstreamError, configured

if TYPE_CHECKING:
    import asyncpg

    from app.api.src.core.accounts import User
    from app.api.src.core.store import Org, Store

log = logging.getLogger(__name__)

# Lowercase letters, digits, hyphens and dots, starting and ending with a letter or digit.
BUCKET_NAME = re.compile(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?")
# Endings S3 reserves for its own kinds of buckets.
RESERVED_SUFFIXES = ("-s3alias", "--ol-s3", ".mrap", "--x-s3", "--table-s3")


@dataclass(frozen=True)
class Bucket:
    pk: int
    org: int | None  # None once the org is deleted
    name: str
    upstream_name: str
    created_at: float
    created_by: int | None
    deleted_at: float | None = None
    purged_at: float | None = None


@dataclass(frozen=True)
class Key:
    """An S3 key. ``session`` or ``api_key`` is the credential an ephemeral key was exchanged
    for; ``source`` names it (a machine or an API key)."""

    pk: int
    access_key_id: str
    user: int
    org: int
    name: str
    read_only: bool
    created_at: float
    expires_at: float | None
    session: int | None
    api_key: int | None
    last_used_at: float | None
    source: str | None = None

    @property
    def ephemeral(self) -> bool:
        return self.session is not None or self.api_key is not None


class S3:
    """Buckets, keys and lifetimes. Without the ``FLOCK_S3_*`` settings, S3 is off: the routes
    answer 404, and nothing here touches an S3 server."""

    NAME_LIMIT: ClassVar[int] = 63  # S3's limit on bucket names
    PK_DIGITS: ClassVar[int] = 5
    # The shortest lifetime an org may set, in seconds: clients renew keys 5 minutes early.
    MIN_TTL: ClassVar[int] = 600
    SWEEP_INTERVAL: ClassVar[float] = 3600.0
    # A purge that failed (the S3 server was down, say) is tried again this much later.
    PURGE_RETRY: ClassVar[float] = 60.0
    BUCKET_COLUMNS: ClassVar[str] = (
        "pk, org_pk, name, upstream_name, extract(epoch FROM created_at)::float8, created_by, "
        "extract(epoch FROM deleted_at)::float8, extract(epoch FROM purged_at)::float8"
    )

    def __init__(self, store: Store) -> None:
        self.store = store
        self.settings = store.settings
        self.enabled = configured(store.settings)
        self.secret = ""
        self.upstream: Upstream | None = None
        self._purge_wanted = asyncio.Event()
        self._tasks: list[asyncio.Task] = []

    async def load(self) -> None:
        self.secret = await self.store.db.fetchval(
            "SELECT value FROM meta WHERE key = 's3_key_secret'"
        )
        if self.enabled:
            if not self.settings.s3_public_url:
                raise RuntimeError("FLOCK_S3_PUBLIC_URL must be set when S3 is on")
            prefix = self.settings.s3_bucket_prefix
            if prefix and not re.fullmatch(r"[a-z0-9][a-z0-9.-]*", prefix):
                raise RuntimeError(
                    "FLOCK_S3_BUCKET_PREFIX may only hold lowercase letters, digits, hyphens "
                    "and dots, starting with a letter or digit"
                )
            if self.name_limit < 3:
                raise RuntimeError("FLOCK_S3_BUCKET_PREFIX leaves no room for bucket names")
            self.upstream = Upstream(self.settings)
            self._tasks.append(asyncio.create_task(self._purge_loop()))
        self._tasks.append(asyncio.create_task(self._sweep_loop()))

    async def close(self) -> None:
        for task in self._tasks:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()
        if self.upstream is not None:
            await self.upstream.close()
            self.upstream = None

    def require(self) -> Upstream:
        """The upstream server; raises ``NotFound`` when S3 is off."""
        if self.upstream is None:
            raise NotFound("S3 isn't enabled on this server")
        return self.upstream

    def config(self) -> dict[str, str] | None:
        """What clients need to reach the proxy (``auth/config``'s ``s3``), or None when off."""
        if not self.enabled:
            return None
        return {"endpoint": self.settings.s3_public_url or "", "region": self.region}

    @property
    def region(self) -> str:
        return self.settings.s3_upstream_region

    # ------------------------------------------------------------------ lifetimes (D56)

    async def set_ttls(self, org: Org, fields: dict[str, Any]) -> None:
        """Change ``s3_ephemeral_ttl`` and/or ``s3_key_max_ttl`` (None: no maximum). The
        ephemeral lifetime can't exceed the maximum; lowering only the maximum below it lowers
        it too."""
        ephemeral = fields.get("s3_ephemeral_ttl", org.s3_ephemeral_ttl)
        maximum = fields.get("s3_key_max_ttl", org.s3_key_max_ttl)
        for value in (ephemeral, maximum):
            if value is not None and value < self.MIN_TTL:
                raise Invalid(f"key lifetimes must be at least {self.MIN_TTL} seconds")
        if maximum is not None and ephemeral > maximum:
            if "s3_ephemeral_ttl" in fields:
                raise Invalid("the ephemeral key lifetime can't exceed the maximum key lifetime")
            ephemeral = maximum
        await self.store.db.execute(
            "UPDATE orgs SET s3_ephemeral_ttl = $2, s3_key_max_ttl = $3 WHERE pk = $1",
            org.pk,
            ephemeral,
            maximum,
        )
        org.s3_ephemeral_ttl, org.s3_key_max_ttl = ephemeral, maximum

    # ------------------------------------------------------------------ keys (D55, D57, D60)

    async def _insert_key(
        self,
        user: User,
        org: Org,
        name: str,
        read_only: bool,
        ttl: float | None,
        session: int | None = None,
        api_key: int | None = None,
    ) -> tuple[Key, str]:
        access_key_id = s3_credentials.new_access_key_id()
        record = await self.store.db.fetchrow(
            "INSERT INTO s3_keys (access_key_id, user_pk, org_pk, name, read_only, created_at, "
            "expires_at, session_pk, api_key_pk) VALUES ($1, $2, $3, $4, $5, now(), "
            "now() + make_interval(secs => $6), $7, $8) "
            "RETURNING pk, extract(epoch FROM created_at)::float8, "
            "extract(epoch FROM expires_at)::float8",
            access_key_id,
            user.pk,
            org.pk,
            name,
            read_only,
            ttl,
            session,
            api_key,
        )
        assert record is not None
        pk, created_at, expires_at = record
        key = Key(
            pk, access_key_id, user.pk, org.pk, name, read_only, created_at, expires_at,
            session, api_key, None,
        )  # fmt: skip
        return key, s3_credentials.secret_for(self.secret, access_key_id)

    async def issue_ephemeral(
        self,
        user: User,
        org: Org,
        name: str,
        ttl: float | None = None,
        read_only: bool = False,
        session: int | None = None,
        api_key: int | None = None,
    ) -> tuple[Key, str]:
        """A key for the credential that asked (a session or an API key), living ``ttl``
        seconds or the org's ephemeral lifetime, whichever is shorter. Returns it and its
        secret."""
        self.require()
        if ttl is not None and ttl <= 0:
            raise Invalid("ttl must be positive")
        lifetime = min(ttl, org.s3_ephemeral_ttl) if ttl is not None else org.s3_ephemeral_ttl
        return await self._insert_key(
            user, org, f"{name} (ephemeral)", read_only, lifetime, session, api_key
        )

    async def create_key(
        self, user: User, org: Org, name: str, read_only: bool, ttl: float | None
    ) -> tuple[Key, str]:
        """A long-lived key living ``ttl`` seconds (None: forever), within the org's maximum.
        Returns it and its secret, which is shown only now."""
        self.require()
        maximum = org.s3_key_max_ttl
        if ttl is not None and ttl <= 0:
            raise Invalid("ttl must be positive")
        if maximum is not None and (ttl is None or ttl > maximum):
            raise Invalid(f"{org.name}'s keys can live at most {maximum} seconds")
        return await self._insert_key(user, org, name, read_only, ttl)

    def expires_at(self, key: Key) -> float | None:
        """When the proxy stops taking ``key``: its expiry, or sooner if the org's maximum
        lifetime has been lowered since it was made."""
        org = self.store.orgs_by_pk.get(key.org)
        maximum = org.s3_key_max_ttl if org is not None else None
        ends = [t for t in (key.expires_at, maximum and key.created_at + maximum) if t]
        return min(ends) if ends else None

    async def keys(self, user: User) -> list[Key]:
        """``user``'s keys that still work, oldest first."""
        records = await self.store.db.fetch(
            "SELECT k.pk, k.access_key_id, k.user_pk, k.org_pk, k.name, k.read_only, "
            "extract(epoch FROM k.created_at)::float8, extract(epoch FROM k.expires_at)::float8, "
            "k.session_pk, k.api_key_pk, extract(epoch FROM k.last_used_at)::float8, "
            "coalesce(s.name, a.name) FROM s3_keys k "
            "LEFT JOIN sessions s ON s.pk = k.session_pk "
            "LEFT JOIN api_keys a ON a.pk = k.api_key_pk "
            "WHERE k.user_pk = $1 ORDER BY k.pk",
            user.pk,
        )
        now = time.time()
        keys = [Key(*record) for record in records]
        return [k for k in keys if (ends := self.expires_at(k)) is None or ends > now]

    async def delete_key(self, user: User, pk: int) -> None:
        deleted = await self.store.db.fetchval(
            "DELETE FROM s3_keys WHERE pk = $1 AND user_pk = $2 RETURNING pk", pk, user.pk
        )
        if deleted is None:
            raise NotFound(f"S3 key {pk} not found")

    async def sweep(self) -> int:
        """Delete expired keys; returns how many."""
        deleted = await self.store.db.fetch(
            "DELETE FROM s3_keys WHERE expires_at <= now() RETURNING pk"
        )
        return len(deleted)

    async def _sweep_loop(self) -> None:
        while True:
            try:
                if swept := await self.sweep():
                    log.info("deleted %d expired S3 keys", swept)
            except Exception:
                log.exception("sweeping expired S3 keys failed")
            await asyncio.sleep(self.SWEEP_INTERVAL)

    # ------------------------------------------------------------------ buckets (D53)

    @property
    def name_limit(self) -> int:
        """The longest bucket name an org may use: S3's 63 characters, less the prefix and
        the org's ``00017-``."""
        return self.NAME_LIMIT - len(self.settings.s3_bucket_prefix) - self.PK_DIGITS - 1

    def check_name(self, name: str) -> None:
        """Raises ``Invalid`` unless ``name`` is a bucket name S3 takes once prefixed."""
        limit = self.name_limit
        if not 1 <= len(name) <= limit:
            raise Invalid(f"bucket names are 1 to {limit} characters")
        if not BUCKET_NAME.fullmatch(name):
            raise Invalid(
                "bucket names hold lowercase letters, digits, hyphens and dots, and start and "
                "end with a letter or digit"
            )
        if ".." in name or name.endswith(RESERVED_SUFFIXES):
            raise Invalid("bucket names can't hold two dots in a row or end like S3's own")

    def upstream_name(self, org: Org, name: str) -> str:
        return f"{self.settings.s3_bucket_prefix}{org.pk:0{self.PK_DIGITS}d}-{name}"

    async def buckets(self, org: Org) -> list[Bucket]:
        records = await self.store.db.fetch(
            f"SELECT {self.BUCKET_COLUMNS} FROM s3_buckets "
            "WHERE org_pk = $1 AND deleted_at IS NULL ORDER BY name",
            org.pk,
        )
        return [Bucket(*record) for record in records]

    async def bucket(self, org: Org, name: str) -> Bucket:
        record = await self.store.db.fetchrow(
            f"SELECT {self.BUCKET_COLUMNS} FROM s3_buckets "
            "WHERE org_pk = $1 AND name = $2 AND deleted_at IS NULL",
            org.pk,
            name,
        )
        if record is None:
            raise NotFound(f"bucket {name} not found")
        return Bucket(*record)

    @staticmethod
    async def _event(
        conn: asyncpg.Connection, bucket: int, kind: str, user: int | None, detail: dict
    ) -> None:
        await conn.execute(
            "INSERT INTO s3_bucket_events (bucket_pk, kind, user_pk, at, detail) "
            "VALUES ($1, $2, $3, now(), $4::jsonb)",
            bucket,
            kind,
            user,
            orjson.dumps(detail).decode(),
        )

    async def create_bucket(self, org: Org, name: str, user: User, detail: dict) -> Bucket:
        """Create ``name`` in ``org``: its row and event, then the bucket upstream. If the S3
        server refuses, neither stays. ``detail`` says how it was asked for (see events)."""
        upstream = self.require()
        self.check_name(name)
        upstream_name = self.upstream_name(org, name)
        async with self.store.db.pool.acquire() as conn, conn.transaction():
            # Held until the transaction ends: two creates of one name wait for each other.
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended('s3:' || $1::text, 0))",
                upstream_name,
            )
            taken = await conn.fetchval(
                "SELECT deleted_at IS NOT NULL FROM s3_buckets "
                "WHERE upstream_name = $1 AND purged_at IS NULL",
                upstream_name,
            )
            if taken is not None:
                raise Conflict(
                    f"bucket {name} is still being deleted: try again in a moment"
                    if taken
                    else f"bucket {name} already exists"
                )
            record = await conn.fetchrow(
                "INSERT INTO s3_buckets (org_pk, name, upstream_name, created_at, created_by) "
                f"VALUES ($1, $2, $3, now(), $4) RETURNING {self.BUCKET_COLUMNS}",
                org.pk,
                name,
                upstream_name,
                user.pk,
            )
            assert record is not None
            bucket = Bucket(*record)
            await self._event(conn, bucket.pk, "created", user.pk, detail)
            # Raising here rolls the row and its event back.
            try:
                # Some servers answer creating a bucket that exists with a 200: look first, so
                # a bucket made outside Flock is never adopted.
                exists = await upstream.bucket_exists(upstream_name)
                if not exists:
                    await upstream.create_bucket(upstream_name)
            except UpstreamError as e:
                if e.code in ("BucketAlreadyExists", "BucketAlreadyOwnedByYou"):
                    raise Conflict(
                        f"a bucket {upstream_name} exists on the S3 server, outside Flock"
                    ) from None
                raise BadGateway(f"the S3 server refused to create the bucket: {e}") from None
            except httpx.HTTPError as e:
                raise BadGateway(f"the S3 server couldn't be reached: {e}") from None
            if exists:
                raise Conflict(f"a bucket {upstream_name} exists on the S3 server, outside Flock")
        return bucket

    async def delete_bucket(self, org: Org, name: str, user: User, detail: dict) -> None:
        """Delete ``name``: its name is free in the org at once, and the bucket is emptied and
        removed upstream in the background."""
        self.require()
        async with self.store.db.pool.acquire() as conn, conn.transaction():
            pk = await conn.fetchval(
                "UPDATE s3_buckets SET deleted_at = now() "
                "WHERE org_pk = $1 AND name = $2 AND deleted_at IS NULL RETURNING pk",
                org.pk,
                name,
            )
            if pk is None:
                raise NotFound(f"bucket {name} not found")
            await self._event(conn, pk, "deleted", user.pk, detail)
        self.purge_later()

    async def delete_org_buckets(self, conn: asyncpg.Connection, org: Org) -> None:
        """Delete every bucket of ``org``, in ``conn``'s transaction (the org's deletion); call
        ``purge_later`` after it commits."""
        pks = await conn.fetch(
            "UPDATE s3_buckets SET deleted_at = now() "
            "WHERE org_pk = $1 AND deleted_at IS NULL RETURNING pk",
            org.pk,
        )
        for (pk,) in pks:
            await self._event(conn, pk, "deleted", None, {"via": "org", "org": org.name})

    async def events(self, org: Org, name: str) -> list[dict[str, Any]]:
        """What happened to the bucket ``name`` of ``org``, oldest first."""
        bucket = await self.bucket(org, name)
        records = await self.store.db.fetch(
            "SELECT kind, user_pk, extract(epoch FROM at)::float8, detail FROM s3_bucket_events "
            "WHERE bucket_pk = $1 ORDER BY pk",
            bucket.pk,
        )
        return [
            {"kind": kind, "user": user, "at": at, "detail": orjson.loads(detail)}
            for kind, user, at, detail in records
        ]

    def purge_later(self) -> None:
        """Wake the purge up: deleted buckets get emptied and removed upstream."""
        self._purge_wanted.set()

    async def purge(self) -> int:
        """Empty and remove upstream every deleted bucket not yet purged. Returns how many
        were; raises the first failure after trying them all."""
        upstream = self.require()
        failure: Exception | None = None
        purged = 0
        for pk, upstream_name in await self.store.db.fetch(
            "SELECT pk, upstream_name FROM s3_buckets "
            "WHERE deleted_at IS NOT NULL AND purged_at IS NULL ORDER BY pk"
        ):
            try:
                objects = await upstream.purge_bucket(upstream_name)
            except (UpstreamError, httpx.HTTPError) as e:
                log.warning("could not purge bucket %s: %s", upstream_name, e)
                failure = failure or e
                continue
            async with self.store.db.pool.acquire() as conn, conn.transaction():
                await conn.execute("UPDATE s3_buckets SET purged_at = now() WHERE pk = $1", pk)
                await self._event(conn, pk, "purged", None, {"via": "purge", "objects": objects})
            log.info("purged bucket %s (%d objects)", upstream_name, objects)
            purged += 1
        if failure is not None:
            raise failure
        return purged

    async def _purge_loop(self) -> None:
        """Purges after each deletion, at startup (resuming what a restart cut short), and again
        after ``PURGE_RETRY`` while purges fail."""
        while True:
            self._purge_wanted.clear()
            try:
                await self.purge()
            except Exception:
                log.exception("purging deleted S3 buckets failed; retrying")
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._purge_wanted.wait(), self.PURGE_RETRY)
                continue
            await self._purge_wanted.wait()
