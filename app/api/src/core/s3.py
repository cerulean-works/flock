"""S3 access (D53-D57, D65, D69): org buckets, S3 keys, the key lifetimes orgs set, and the
objects the dashboard's file browser lists, deletes and presigns URLs for.

The S3 proxy (``app.api.src.s3_proxy``) serves the S3 API itself; this side keeps its tables.
Buckets and keys are read from the database when asked for, never kept in memory: the proxy
reads the same rows. The org's two lifetimes live on ``Org``.
"""

import asyncio
import contextlib
import logging
import math
import re
import time
from collections.abc import Awaitable
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Any, ClassVar
from urllib.parse import quote, urlsplit

import httpx
import orjson

from app.api.src import s3_credentials
from app.api.src.core.errors import BadGateway, Conflict, Forbidden, Invalid, NotFound
from app.api.src.s3_proxy import sigv4
from app.api.src.s3_proxy.upstream import DELETE_BATCH, Upstream, UpstreamError, configured

if TYPE_CHECKING:
    import asyncpg

    from app.api.src.core.accounts import User
    from app.api.src.core.store import Org, Store

log = logging.getLogger(__name__)

# Lowercase letters, digits, hyphens and dots, starting and ending with a letter or digit.
BUCKET_NAME = re.compile(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?")
# Endings S3 reserves for its own kinds of buckets.
RESERVED_SUFFIXES = ("-s3alias", "--ol-s3", ".mrap", "--x-s3", "--table-s3")
MIB = 1024 * 1024
# Types a preview may ask the proxy to answer with (``response-content-type``): never HTML, or
# anything else a browser would run as a page.
PREVIEW_TYPES = re.compile(
    r"image/(png|jpeg|gif|webp|svg\+xml)|application/pdf|text/plain|(video|audio)/[\w.+-]+"
)


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
    # "datasets" for the org's flock-datasets (D85), "models" for its flock-models (D97)
    managed: str | None = None


@dataclass(frozen=True)
class Key:
    """An S3 key. ``session`` or ``api_key`` is the credential an ephemeral key was exchanged
    for (a wandb key's only before D72); ``source`` names it (a machine or a wandb key)."""

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


class TooMany(Exception):
    """A folder delete of more than ``S3.FOLDER_DELETE_LIMIT`` objects, refused (HTTP 409)."""

    def __init__(self, message: str, limit: int, command: str) -> None:
        super().__init__(message)
        self.message = message
        self.limit = limit
        self.command = command


class S3:
    """Buckets, keys and lifetimes. Without the ``FLOCK_S3_*`` settings, S3 is off: the routes
    answer 404, and nothing here touches an S3 server."""

    NAME_LIMIT: ClassVar[int] = 63  # S3's limit on bucket names
    PK_DIGITS: ClassVar[int] = 5
    # The shortest lifetime an org may set, in seconds: clients renew keys 5 minutes early.
    MIN_TTL: ClassVar[int] = 600
    SWEEP_INTERVAL: ClassVar[float] = 3600.0
    # A purge that failed (the S3 server was down, say) is tried again this much later, and
    # every replica looks for work this often anyway, in case it missed a notification.
    PURGE_RETRY: ClassVar[float] = 60.0
    PURGE_CHANNEL: ClassVar[str] = "flock_s3_purge"
    # The file browser (D65, D69).
    OBJECT_PAGE: ClassVar[int] = 200
    FOLDER_DELETE_LIMIT: ClassVar[int] = 10_000
    BROWSER_KEY: ClassVar[str] = "browser"
    # A browser key is reused while it has this long left (or the URL's lifetime, if longer).
    BROWSER_KEY_MARGIN: ClassVar[int] = 900
    URL_TTL: ClassVar[int] = 900  # uploads, downloads and previews
    LINK_TTL: ClassVar[int] = 3600  # "Copy presigned link"
    SINGLE_PUT_LIMIT: ClassVar[int] = 100 * MIB
    PART_SIZE: ClassVar[int] = 16 * MIB
    UPLOAD_LIMIT: ClassVar[int] = 50 * 1024 * MIB
    BUCKET_COLUMNS: ClassVar[str] = (
        "pk, org_pk, name, upstream_name, extract(epoch FROM created_at)::float8, created_by, "
        "extract(epoch FROM deleted_at)::float8, extract(epoch FROM purged_at)::float8, managed"
    )
    # The buckets of each org that Flock creates and owns (D85, D97), by kind, and the prefix
    # new bucket names can't start with, so Flock can add more of its own later.
    DATASETS: ClassVar[str] = "flock-datasets"
    MODELS: ClassVar[str] = "flock-models"
    MANAGED: ClassVar[dict[str, str]] = {"datasets": DATASETS, "models": MODELS}
    RESERVED_PREFIX: ClassVar[str] = "flock-"

    def __init__(self, store: Store) -> None:
        self.store = store
        self.settings = store.settings
        self.enabled = configured(store.settings)
        self.secret = ""
        self.upstream: Upstream | None = None
        self._purge_wanted = asyncio.Event()
        self._tasks: list[asyncio.Task] = []
        self._notifying: set[asyncio.Task] = set()
        # A bucket deleted on any replica wakes every replica's purge (D81).
        store.bus.listen(self.PURGE_CHANNEL, lambda _: self._purge_wanted.set())

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
        """A key for the credential that asked (a session; a wandb key before D72), living ``ttl``
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

    async def expires_at(self, key: Key) -> float | None:
        """When the proxy stops taking ``key``: its expiry, or sooner if the org's maximum
        lifetime has been lowered since it was made."""
        maximum = await self.store.db.fetchval(
            "SELECT s3_key_max_ttl FROM orgs WHERE pk = $1 AND deleted_at IS NULL", key.org
        )
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
        return [k for k in keys if (ends := await self.expires_at(k)) is None or ends > now]

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

    def check_name(self, name: str, reserved: bool = False) -> None:
        """Raises ``Invalid`` unless ``name`` is a bucket name S3 takes once prefixed, and (unless
        ``reserved``, for Flock's own buckets) doesn't start with ``flock-``."""
        if not reserved and name.startswith(self.RESERVED_PREFIX):
            raise Invalid(f"Names starting with {self.RESERVED_PREFIX} are reserved.")
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

    async def buckets(self, org: Org, deleting: bool = False) -> list[Bucket]:
        """The org's buckets by name; with ``deleting``, also those deleted but not yet purged
        (whose names can't be used again until they are)."""
        gone = " OR (deleted_at IS NOT NULL AND purged_at IS NULL)" if deleting else ""
        records = await self.store.db.fetch(
            f"SELECT {self.BUCKET_COLUMNS} FROM s3_buckets "
            f"WHERE org_pk = $1 AND (deleted_at IS NULL{gone}) ORDER BY name, pk",
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
        self.check_name(name)
        return await self._create(org, name, user.pk, detail)

    async def _create(
        self, org: Org, name: str, user: int | None, detail: dict, managed: str | None = None
    ) -> Bucket:
        upstream = self.require()
        self.check_name(name, reserved=True)
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
                "INSERT INTO s3_buckets (org_pk, name, upstream_name, created_at, created_by, "
                f"managed) VALUES ($1, $2, $3, now(), $4, $5) RETURNING {self.BUCKET_COLUMNS}",
                org.pk,
                name,
                upstream_name,
                user,
                managed,
            )
            assert record is not None
            bucket = Bucket(*record)
            await self._event(conn, bucket.pk, "created", user, detail)
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
            pk, managed = await conn.fetchrow(
                "SELECT pk, managed FROM s3_buckets "
                "WHERE org_pk = $1 AND name = $2 AND deleted_at IS NULL FOR UPDATE",
                org.pk,
                name,
            ) or (None, None)
            if pk is None:
                raise NotFound(f"bucket {name} not found")
            if managed is not None:
                raise Conflict(f"{name} is managed by Flock and can't be deleted")
            await conn.execute("UPDATE s3_buckets SET deleted_at = now() WHERE pk = $1", pk)
            await self._event(conn, pk, "deleted", user.pk, detail)
        self.purge_later()

    async def managed_bucket(self, org: Org, kind: str = "datasets") -> Bucket | None:
        """The org's live managed bucket of ``kind``, if it has one."""
        record = await self.store.db.fetchrow(
            f"SELECT {self.BUCKET_COLUMNS} FROM s3_buckets "
            "WHERE org_pk = $1 AND managed = $2 AND deleted_at IS NULL",
            org.pk,
            kind,
        )
        return Bucket(*record) if record is not None else None

    async def ensure_managed(self, org: Org, kind: str = "datasets") -> Bucket | None:
        """The org's managed bucket of ``kind`` (``flock-datasets``, D85, or ``flock-models``,
        D97), created if it has none: by Flock, with no user, its event saying ``via: flock``. A
        bucket of that name the org already has is marked managed instead. None when S3 is off.
        Raises as ``create_bucket`` does."""
        if self.upstream is None:
            return None
        if (found := await self.managed_bucket(org, kind)) is not None:
            return found
        name = self.MANAGED[kind]
        adopted = await self.store.db.fetchrow(
            "UPDATE s3_buckets SET managed = $3 "
            "WHERE org_pk = $1 AND name = $2 AND deleted_at IS NULL AND managed IS NULL "
            "AND NOT EXISTS (SELECT 1 FROM s3_buckets WHERE org_pk = $1 AND managed = $3 "
            f"AND deleted_at IS NULL) RETURNING {self.BUCKET_COLUMNS}",
            org.pk,
            name,
            kind,
        )
        if adopted is not None:
            return Bucket(*adopted)
        try:
            return await self._create(org, name, None, {"via": "flock"}, kind)
        except Conflict:
            # Another replica (or the startup pass) made it meanwhile.
            if (found := await self.managed_bucket(org, kind)) is not None:
                return found
            raise

    async def ensure_every_managed(self, org: Org) -> None:
        """Both of the org's managed buckets (at its creation). A failure is logged and leaves
        the org be: startup and the tabs try again."""
        for kind, name in self.MANAGED.items():
            try:
                await self.ensure_managed(org, kind)
            except Exception:
                log.exception("could not create %s for %s", name, org.name)

    async def ensure_all_managed(self) -> None:
        """``ensure_managed`` for every org that lacks one of its buckets (at startup, in the
        background: existing orgs, and S3 turned on later). Failures are logged and left for
        the Datasets and Models tabs to retry."""
        for org in await self.store.orgs():
            await self.ensure_every_managed(org)

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
        """Wake the purge up, on every replica: deleted buckets get emptied and removed
        upstream."""
        self._purge_wanted.set()

        async def notify() -> None:
            with contextlib.suppress(Exception):
                await self.store.db.execute("SELECT pg_notify($1, '')", self.PURGE_CHANNEL)

        task = asyncio.get_running_loop().create_task(notify())
        self._notifying.add(task)
        task.add_done_callback(self._notifying.discard)

    async def purge(self) -> int:
        """Empty and remove upstream every deleted bucket not yet purged. Returns how many
        were; raises the first failure after trying them all. Each bucket is claimed while it
        is purged, so replicas purging at once take different ones (D81)."""
        upstream = self.require()
        failure: Exception | None = None
        purged = 0
        failed: list[int] = []
        while True:
            async with self.store.db.pool.acquire() as conn, conn.transaction():
                record = await conn.fetchrow(
                    "SELECT pk, upstream_name FROM s3_buckets WHERE deleted_at IS NOT NULL "
                    "AND purged_at IS NULL AND pk <> ALL($1) ORDER BY pk LIMIT 1 "
                    "FOR UPDATE SKIP LOCKED",
                    failed,
                )
                if record is None:
                    break
                pk, upstream_name = record
                try:
                    objects = await upstream.purge_bucket(upstream_name)
                except (UpstreamError, httpx.HTTPError) as e:
                    log.warning("could not purge bucket %s: %s", upstream_name, e)
                    failure = failure or e
                    failed.append(pk)
                    continue
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
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._purge_wanted.wait(), self.PURGE_RETRY)

    # ------------------------------------------------------------------ objects (D65, D67, D69)

    @staticmethod
    def check_key(key: str, folder: bool = False) -> None:
        """Raises ``Invalid`` unless ``key`` is an object key S3 takes (``folder``: one ending
        in ``/``)."""
        if not key or len(key.encode()) > 1024:
            raise Invalid("object keys are 1 to 1024 bytes")
        if folder and (not key.endswith("/") or key.startswith("/")):
            raise Invalid("folder names end with / and don't start with one")
        if any(ord(c) < 32 or ord(c) == 127 for c in key):
            raise Invalid("object keys can't hold control characters")

    @staticmethod
    async def _call[T](call: Awaitable[T]) -> T:
        """An upstream call, its failures as ``BadGateway`` (and a missing bucket as 404)."""
        try:
            return await call
        except UpstreamError as e:
            if e.code == "NoSuchBucket":
                raise NotFound("the bucket is missing on the S3 server") from None
            raise BadGateway(f"the S3 server refused: {e}") from None
        except httpx.HTTPError as e:
            raise BadGateway(f"the S3 server couldn't be reached: {e}") from None

    async def list_objects(
        self, org: Org, name: str, prefix: str, cursor: str | None, limit: int
    ) -> dict[str, Any]:
        """One level of ``name`` under ``prefix``: its folders (common prefixes) and objects, and
        the cursor of the next page. A folder's own marker (``prefix`` itself) isn't listed."""
        upstream = self.require()
        bucket = await self.bucket(org, name)
        listing = await self._call(
            upstream.list_objects(bucket.upstream_name, prefix, "/", cursor, limit)
        )
        return {
            "prefix": prefix,
            "folders": listing.prefixes,
            "objects": [
                {
                    "key": o.key,
                    "size": o.size,
                    "last_modified": o.last_modified,
                    "etag": o.etag.strip('"'),
                }
                for o in listing.objects
                if o.key != prefix
            ],
            "cursor": listing.cursor,
        }

    async def head_object(self, org: Org, name: str, key: str) -> dict[str, Any]:
        """An object's details (D67), from a HEAD."""
        upstream = self.require()
        self.check_key(key)
        bucket = await self.bucket(org, name)
        response = await self._call(upstream.head(bucket.upstream_name, key))
        if response is None:
            raise NotFound(f"object {key} not found")
        headers = response.headers
        modified = headers.get("last-modified")
        return {
            "key": key,
            "size": int(headers.get("content-length", 0)),
            "last_modified": parsedate_to_datetime(modified).timestamp() if modified else None,
            "etag": headers.get("etag", "").strip('"'),
            "content_type": headers.get("content-type"),
            "metadata": {
                header.removeprefix("x-amz-meta-"): value
                for header, value in headers.items()
                if header.startswith("x-amz-meta-")
            },
        }

    @staticmethod
    def _check_writable(bucket: Bucket) -> None:
        """flock-models is written only through upload sessions and emptied only through the
        model routes (D97, D109), so the dashboard's own writes are refused there, as the proxy
        refuses every other client's."""
        if bucket.managed == "models":
            raise Forbidden(
                "Files in flock-models change only through flock models push and the Models tab."
            )

    async def create_folder(self, org: Org, name: str, prefix: str) -> None:
        """The empty ``prefix`` object (ending in ``/``) that makes a folder show up."""
        upstream = self.require()
        self.check_key(prefix, folder=True)
        bucket = await self.bucket(org, name)
        self._check_writable(bucket)
        await self._call(upstream.put_empty(bucket.upstream_name, prefix))

    async def delete_objects(
        self, org: Org, name: str, keys: list[str], prefixes: list[str]
    ) -> int:
        """Delete ``keys``, and every object under each of ``prefixes``; returns how many were
        asked to go. More than ``FOLDER_DELETE_LIMIT`` in all is refused before any is deleted,
        naming the CLI command that can do it."""
        upstream = self.require()
        for key in keys:
            self.check_key(key)
        for prefix in prefixes:
            self.check_key(prefix, folder=True)
        bucket = await self.bucket(org, name)
        self._check_writable(bucket)
        limit = self.FOLDER_DELETE_LIMIT
        doomed = dict.fromkeys(keys)
        for prefix in prefixes:
            cursor = None
            while True:
                page = await self._call(
                    upstream.list_objects(bucket.upstream_name, prefix, None, cursor)
                )
                doomed.update(dict.fromkeys(o.key for o in page.objects))
                if len(doomed) > limit:
                    command = f"aws s3 rm --recursive s3://{name}/{prefix} --profile flock"
                    raise TooMany(
                        f"This would delete more than {limit:,} objects. Delete them from the "
                        f"command line instead with {command}",
                        limit,
                        command,
                    )
                cursor = page.cursor
                if cursor is None:
                    break
        listed = list(doomed)
        for start in range(0, len(listed), DELETE_BATCH):
            batch = listed[start : start + DELETE_BATCH]
            await self._call(upstream.delete_keys(bucket.upstream_name, batch))
        return len(listed)

    # ------------------------------------------------------------------ presigned URLs (D69)

    async def browser_key(
        self,
        user: User,
        org: Org,
        lifetime: float,
        session: int | None,
        api_key: int | None,
    ) -> tuple[Key, str, float | None]:
        """The browser key of this session (or wandb key) and org: the newest one, if it has more
        than ``BROWSER_KEY_MARGIN`` seconds (or ``lifetime``, if longer) still to live, else a new
        one. Returns it, its secret and when the proxy stops taking it."""
        now = time.time()
        record = await self.store.db.fetchrow(
            "SELECT pk, access_key_id, user_pk, org_pk, name, read_only, "
            "extract(epoch FROM created_at)::float8, extract(epoch FROM expires_at)::float8, "
            "session_pk, api_key_pk, extract(epoch FROM last_used_at)::float8 FROM s3_keys "
            "WHERE user_pk = $1 AND org_pk = $2 AND session_pk IS NOT DISTINCT FROM $3 "
            "AND api_key_pk IS NOT DISTINCT FROM $4 AND name = $5 AND NOT read_only "
            "ORDER BY expires_at DESC NULLS FIRST LIMIT 1",
            user.pk,
            org.pk,
            session,
            api_key,
            f"{self.BROWSER_KEY} (ephemeral)",
        )
        if record is not None:
            key = Key(*record)
            ends = await self.expires_at(key)
            if ends is None or ends - now > max(self.BROWSER_KEY_MARGIN, lifetime):
                return key, s3_credentials.secret_for(self.secret, key.access_key_id), ends
        key, secret = await self.issue_ephemeral(
            user, org, self.BROWSER_KEY, session=session, api_key=api_key
        )
        return key, secret, await self.expires_at(key)

    def _presigned(
        self,
        key: Key,
        secret: str,
        method: str,
        bucket: str,
        object_key: str,
        params: list[tuple[str, str]],
        expires: int,
    ) -> str:
        """A URL to the proxy (``FLOCK_S3_PUBLIC_URL``) for one request on an object."""
        public = urlsplit(self.settings.s3_public_url or "")
        path = f"{public.path.rstrip('/')}/{sigv4.uri_encode(bucket, slash=False)}/"
        path += sigv4.uri_encode(object_key)
        query = sigv4.presign(
            method, public.netloc, path, params, key.access_key_id, secret, self.region,
            time.time(), expires,
        )  # fmt: skip
        return f"{public.scheme}://{public.netloc}{path}?{query}"

    async def presign(
        self,
        user: User,
        org: Org,
        name: str,
        body: dict[str, Any],
        session: int | None,
        api_key: int | None,
    ) -> dict[str, Any]:
        """Presigned URLs to the proxy for the browser (D69), signed with the browser key.

        ``body["method"]`` is ``get`` (with ``disposition``, ``link`` and ``content_type``),
        ``put`` (``size``), ``multipart`` (creates an upload of ``size`` bytes), ``parts``
        (``upload_id`` and the ``parts`` to sign), ``complete`` or ``abort`` (``upload_id``).
        """
        upstream = self.require()
        method, object_key = body["method"], body["key"]
        self.check_key(object_key)
        bucket = await self.bucket(org, name)
        if method == "abort":
            await self._call(
                upstream.abort_upload(bucket.upstream_name, object_key, body["upload_id"])
            )
            return {}
        if method == "multipart":
            size = body.get("size")
            if not size or size <= 0:
                raise Invalid("a multipart upload needs the file's size")
            if size > self.UPLOAD_LIMIT:
                raise Invalid(
                    f"files over {self.UPLOAD_LIMIT // (1024 * MIB)} GiB can't be uploaded "
                    "here: use the command line (aws s3 cp)"
                )
            upload_id = await self._call(
                upstream.create_upload(bucket.upstream_name, object_key, body.get("content_type"))
            )
            return {
                "upload_id": upload_id,
                "part_size": self.PART_SIZE,
                "part_count": math.ceil(size / self.PART_SIZE),
            }
        if method == "put" and (body.get("size") or 0) > self.SINGLE_PUT_LIMIT:
            raise Invalid(f"files over {self.SINGLE_PUT_LIMIT // MIB} MiB are uploaded in parts")

        wanted = self.LINK_TTL if method == "get" and body.get("link") else self.URL_TTL
        key, secret, ends = await self.browser_key(user, org, wanted, session, api_key)
        now = time.time()
        # A URL can't outlive its key: with an ephemeral lifetime this short, it lives less.
        expires = int(min(wanted, ends - now)) if ends is not None else wanted
        if expires < 60:
            raise Invalid("the browser's S3 key has too little time left: try again")

        def url(http: str, params: list[tuple[str, str]]) -> str:
            return self._presigned(key, secret, http, name, object_key, params, expires)

        out: dict[str, Any] = {"expires_at": now + expires}
        if method == "get":
            filename = quote(object_key.rpartition("/")[2] or "download", safe="")
            disposition = "inline" if body.get("disposition") == "inline" else "attachment"
            params = [
                ("response-content-disposition", f"{disposition}; filename*=UTF-8''{filename}")
            ]
            content_type = body.get("content_type")
            if content_type is not None:
                if disposition != "inline" or not PREVIEW_TYPES.fullmatch(content_type):
                    raise Invalid(f"objects can't be shown as {content_type}")
                params.append(("response-content-type", content_type))
            out["url"] = url("GET", params)
        elif method == "put":
            out["url"] = url("PUT", [])
        elif method == "parts":
            upload_id = body["upload_id"]
            out["parts"] = {
                str(n): url("PUT", [("partNumber", str(n)), ("uploadId", upload_id)])
                for n in body["parts"]
            }
        elif method == "complete":
            out["url"] = url("POST", [("uploadId", body["upload_id"])])
        return out
