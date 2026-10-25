"""The hub's datasets (D41-D44, D48, D49): Lance datasets in S3, one per ``datasets`` row.

Upstream, a dataset's objects live under ``datasets/<uuid>/`` in the hub bucket, so renaming a
dataset only changes Postgres and a reused name never sees an old dataset's objects. Lance
keeps the versions; ``dataset_commits`` keeps who wrote each one and a summary that outlives
Lance's cleanup of old manifests.

Reads go straight to the upstream with the global key. Writes (cards, tags, restore, optimize,
prune) go through the S3 proxy as the signed-in user, like any client's.
"""

import asyncio
import contextlib
import datetime as dt
import decimal
import hashlib
import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar
from urllib.parse import urlsplit

import boto3
import botocore.config
import lance

from app.api.src.core.errors import Conflict, Forbidden, NotFound

if TYPE_CHECKING:
    from app.api.src.core.accounts import User
    from app.api.src.core.store import Org, Store

log = logging.getLogger(__name__)


@dataclass(eq=False)
class Dataset:
    pk: int
    uuid: str
    org: Org
    name: str
    description: str
    visibility: str  # "private" (org members) or "internal" (anyone signed in)
    created_by: int | None
    created_at: float
    updated_at: float
    # The Hugging Face repo it mirrors ({repo, config, sha}), for datasets of the mirror org.
    mirror: dict[str, Any] | None = None
    deleted: bool = False

    @property
    def uri(self) -> str:
        """Its Lance URI for clients, through the proxy."""
        return f"s3://datasets/{self.org.name}/{self.name}"


@dataclass
class Commit:
    """A ``dataset_commits`` row: one Lance version."""

    version: int
    user: int | None
    created_at: float
    op: str | None
    message: str | None
    rows: int | None
    bytes: int | None
    fragments: int | None
    pruned: bool


class Datasets:
    """Every org's datasets (``store.datasets``), by org and name, in memory."""

    COLUMNS: ClassVar[str] = (
        "pk, uuid::text, org_pk, name, description, visibility, created_by, "
        "extract(epoch FROM created_at)::float8, extract(epoch FROM updated_at)::float8"
    )
    COMMIT_COLUMNS: ClassVar[str] = (
        "version, user_pk, extract(epoch FROM created_at)::float8, op, message, rows, bytes, "
        "fragments, pruned"
    )
    # Open Lance datasets kept, keyed by (uuid, version).
    CACHE_SIZE: ClassVar[int] = 64
    # Longer string cells are cut in row listings.
    CELL_CHARS: ClassVar[int] = 2000
    CARD_KEY: ClassVar[str] = "flock.card"
    MESSAGE_KEY: ClassVar[str] = "__lance_commit_message"
    # boto3 otherwise sends checksums as aws-chunked trailers, which the proxy doesn't take.
    S3_CONFIG: ClassVar[botocore.config.Config] = botocore.config.Config(
        s3={"addressing_style": "path"},
        request_checksum_calculation="when_required",
        response_checksum_validation="when_required",
    )

    def __init__(self, store: Store) -> None:
        self.store = store
        self.by_name: dict[tuple[int, str], Dataset] = {}  # by (org pk, name)
        self.by_pk: dict[int, Dataset] = {}
        # Held to change which name maps to which dataset.
        self._catalog = asyncio.Lock()
        # Serializes the API's own writes to a dataset (by pk), so they never race each other.
        self._writes: dict[int, asyncio.Lock] = {}
        # Open Lance datasets in LRU order; (uuid, None) is the handle that finds the latest
        # version. Used from worker threads, hence the threading lock.
        self._open: OrderedDict[tuple[str, int | None], lance.LanceDataset] = OrderedDict()
        self._open_lock = threading.Lock()
        self._purges: set[asyncio.Task] = set()

    @property
    def enabled(self) -> bool:
        """The hub is on once its S3 server is configured."""
        return bool(self.store.settings.hub_s3_endpoint)

    async def load(self) -> None:
        store = self.store
        if self.enabled and store.settings.hub_mirror_org not in store.orgs:
            await store.create_org(store.settings.hub_mirror_org)
        for record in await store.db.fetch(
            f"SELECT {self.COLUMNS} FROM datasets WHERE deleted_at IS NULL"
        ):
            pk, uuid, org_pk, *rest = record
            self._add(Dataset(pk, uuid, store.orgs_by_pk[org_pk], *rest))
        for pk, repo, config, sha in await store.db.fetch(
            "SELECT DISTINCT ON (dataset_pk) dataset_pk, repo, config, sha FROM hub_mirrors "
            "WHERE state = 'finished' AND dataset_pk IS NOT NULL "
            "ORDER BY dataset_pk, finished_at DESC"
        ):
            if pk in self.by_pk:
                self.by_pk[pk].mirror = {"repo": repo, "config": config or None, "sha": sha}
        if self.enabled:  # purges cut short by a restart
            for pk, uuid in await store.db.fetch(
                "SELECT pk, uuid::text FROM datasets WHERE deleted_at IS NOT NULL"
            ):
                self._purge_later(uuid, pk)

    async def close(self) -> None:
        """Stop purges (on shutdown); they start again at the next startup."""
        for task in list(self._purges):
            task.cancel()
        await asyncio.gather(*self._purges, return_exceptions=True)

    async def flush(self) -> None:
        """Wait for background purges; used by tests."""
        while self._purges:
            await asyncio.gather(*list(self._purges), return_exceptions=True)

    def _add(self, dataset: Dataset) -> Dataset:
        self.by_name[(dataset.org.pk, dataset.name)] = dataset
        self.by_pk[dataset.pk] = dataset
        return dataset

    async def invalidate(self) -> None:
        """Tell S3 proxies to drop their cached datasets, memberships and keys (D47)."""
        await self.store.db.execute("SELECT pg_notify('hub_invalidate', '')")

    # ------------------------------------------------------------------ lookups

    def get(self, org: Org, name: str) -> Dataset | None:
        return self.by_name.get((org.pk, name))

    def of_org(self, org: Org) -> list[Dataset]:
        return sorted((d for d in self.by_pk.values() if d.org is org), key=lambda d: d.name)

    def can_read(self, user: User, dataset: Dataset) -> bool:
        role = self.store.accounts.role(user, dataset.org)
        return role is not None or dataset.visibility == "internal"

    def info(self, dataset: Dataset, user: User, latest: Commit | None) -> dict[str, Any]:
        accounts = self.store.accounts
        creator = accounts.users.get(dataset.created_by) if dataset.created_by else None
        return {
            "id": dataset.uuid,
            "org": dataset.org.name,
            "name": dataset.name,
            "description": dataset.description,
            "visibility": dataset.visibility,
            "uri": dataset.uri,
            "created_by": accounts.brief(creator) if creator is not None else None,
            "created_at": dataset.created_at,
            "updated_at": dataset.updated_at,
            "latest_version": latest.version if latest is not None else None,
            "rows": latest.rows if latest is not None else None,
            "bytes": latest.bytes if latest is not None else None,
            "role": accounts.role(user, dataset.org),
            "mirror": dataset.mirror,
        }

    def commit_info(self, commit: Commit, tags: Iterable[str] = ()) -> dict[str, Any]:
        users = self.store.accounts.users
        author = users.get(commit.user) if commit.user is not None else None
        return {
            "version": commit.version,
            "created_at": commit.created_at,
            "author": self.store.accounts.brief(author) if author is not None else None,
            "op": commit.op,
            "message": commit.message,
            "rows": commit.rows,
            "bytes": commit.bytes,
            "fragments": commit.fragments,
            "tags": sorted(tags),
            "pruned": commit.pruned,
        }

    # ------------------------------------------------------------------ catalog

    async def create(
        self, org: Org, name: str, user: User, description: str = "", visibility: str = "private"
    ) -> Dataset:
        async with self._catalog:
            if org.deleted:
                raise NotFound(f"organization {org.name} not found")
            record = None
            if (org.pk, name) not in self.by_name:
                record = await self.store.db.fetchrow(
                    "INSERT INTO datasets (uuid, org_pk, name, description, visibility, "
                    "created_by, created_at, updated_at) "
                    "VALUES (gen_random_uuid(), $1, $2, $3, $4, $5, now(), now()) "
                    "ON CONFLICT (org_pk, name) WHERE deleted_at IS NULL DO NOTHING "
                    f"RETURNING {self.COLUMNS}",
                    org.pk,
                    name,
                    description,
                    visibility,
                    user.pk,
                )
            if record is None:
                raise Conflict(f"dataset {org.name}/{name} already exists")
            pk, uuid, _, *rest = record
            dataset = self._add(Dataset(pk, uuid, org, *rest))
        # A proxy may have cached the name as unknown, and would refuse the first writes.
        await self.invalidate()
        return dataset

    async def update(self, dataset: Dataset, fields: dict[str, Any]) -> None:
        """Change ``name``, ``description`` and/or ``visibility``."""
        async with self._catalog:
            if dataset.deleted:
                raise NotFound(f"dataset {dataset.org.name}/{dataset.name} not found")
            name = fields.get("name", dataset.name)
            if name != dataset.name and (dataset.org.pk, name) in self.by_name:
                raise Conflict(f"dataset {dataset.org.name}/{name} already exists")
            description = fields.get("description", dataset.description)
            visibility = fields.get("visibility", dataset.visibility)
            now = time.time()
            await self.store.db.execute(
                "UPDATE datasets SET name = $2, description = $3, visibility = $4, "
                "updated_at = to_timestamp($5) WHERE pk = $1",
                dataset.pk,
                name,
                description,
                visibility,
                now,
            )
            invalidate = name != dataset.name or visibility != dataset.visibility
            del self.by_name[(dataset.org.pk, dataset.name)]
            dataset.name, dataset.description, dataset.visibility = name, description, visibility
            dataset.updated_at = now
            self._add(dataset)
        if invalidate:
            await self.invalidate()

    async def delete(self, dataset: Dataset) -> None:
        """Free the name at once; the objects, then the row, go in the background."""
        async with self._catalog:
            if dataset.deleted:
                raise NotFound(f"dataset {dataset.org.name}/{dataset.name} not found")
            await self.store.db.execute(
                "UPDATE datasets SET deleted_at = now() WHERE pk = $1", dataset.pk
            )
            self._forget(dataset)
        await self.invalidate()
        self._purge_later(dataset.uuid, dataset.pk)

    def _forget(self, dataset: Dataset) -> None:
        del self.by_name[(dataset.org.pk, dataset.name)]
        del self.by_pk[dataset.pk]
        self._writes.pop(dataset.pk, None)
        dataset.deleted = True

    def forget_org(self, org: Org) -> None:
        """Drop a deleted org's datasets (the database cascades them) and purge their objects."""
        for dataset in self.of_org(org):
            self._forget(dataset)
            self._purge_later(dataset.uuid, None)

    def _purge_later(self, uuid: str, pk: int | None) -> None:
        if not self.enabled:
            return  # left for a startup with the hub configured
        task = asyncio.create_task(self._purge(uuid, pk))
        self._purges.add(task)
        task.add_done_callback(self._purges.discard)

    async def _purge(self, uuid: str, pk: int | None) -> None:
        try:
            await asyncio.to_thread(self._delete_objects, uuid)
            if pk is not None:
                await self.store.db.execute("DELETE FROM datasets WHERE pk = $1", pk)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("purging dataset %s failed; it is retried at the next startup", uuid)

    def _delete_objects(self, uuid: str) -> None:
        with self._open_lock:
            for key in [key for key in self._open if key[0] == uuid]:
                del self._open[key]
        settings = self.store.settings
        client = self._s3(self.upstream_options())
        pages = client.get_paginator("list_objects_v2").paginate(
            Bucket=settings.hub_bucket, Prefix=f"datasets/{uuid}/"
        )
        for page in pages:
            keys = [{"Key": item["Key"]} for item in page.get("Contents", [])]
            if keys:
                client.delete_objects(
                    Bucket=settings.hub_bucket, Delete={"Objects": keys, "Quiet": True}
                )

    # ------------------------------------------------------------------ where the bytes are

    def upstream_options(self) -> dict[str, str]:
        """Lance ``storage_options`` for the upstream S3 server, with the global key."""
        settings = self.store.settings
        assert settings.hub_s3_endpoint is not None
        options = {
            "aws_endpoint": settings.hub_s3_endpoint,
            "aws_access_key_id": settings.hub_s3_access_key_id or "",
            "aws_secret_access_key": settings.hub_s3_secret_access_key or "",
            "aws_region": settings.hub_s3_region,
        }
        if settings.hub_s3_endpoint.startswith("http://"):
            options["allow_http"] = "true"
        return options

    def upstream_uri(self, dataset: Dataset) -> str:
        return f"s3://{self.store.settings.hub_bucket}/datasets/{dataset.uuid}"

    def write_target(
        self, dataset: Dataset, user: User, mirror_writer: bool = False
    ) -> tuple[str, dict[str, str]]:
        """The Lance URI and ``storage_options`` the API writes ``dataset`` through as ``user``
        (D49): the proxy, with temporary credentials. ``mirror_writer`` credentials may write
        to the mirror org, for the server's Hugging Face imports (D50).

        Until the proxy exists, ``FLOCK_HUB_PROXY_URL`` may name the upstream itself; writes
        then use the global key and the upstream layout. (Delete this branch with step 3.)
        """
        settings = self.store.settings
        if not settings.hub_proxy_url:
            raise Conflict("dataset writes need FLOCK_HUB_PROXY_URL, which is not set")
        if settings.hub_proxy_url == settings.hub_s3_endpoint:
            return self.upstream_uri(dataset), self.upstream_options()
        credentials = self.store.hub_keys.temporary(user, mirror_writer=mirror_writer)
        options = {
            "aws_endpoint": settings.hub_proxy_url,
            "aws_access_key_id": credentials["access_key_id"],
            "aws_secret_access_key": credentials["secret_access_key"],
            "aws_session_token": credentials["session_token"],
            "aws_region": credentials["region"],
        }
        if settings.hub_proxy_url.startswith("http://"):
            options["allow_http"] = "true"
        return dataset.uri, options

    def _s3(self, options: dict[str, str]) -> Any:
        """A boto3 client with the same endpoint and credentials as Lance ``options``."""
        return boto3.client(
            "s3",
            endpoint_url=options["aws_endpoint"],
            aws_access_key_id=options["aws_access_key_id"],
            aws_secret_access_key=options["aws_secret_access_key"],
            aws_session_token=options.get("aws_session_token"),
            region_name=options["aws_region"],
            config=self.S3_CONFIG,
        )

    @staticmethod
    def _object(uri: str, path: str) -> tuple[str, str]:
        """The bucket and key of ``path`` inside the dataset at the ``s3://`` ``uri``."""
        parts = urlsplit(uri)
        return parts.netloc, f"{parts.path.strip('/')}/{path}"

    # ------------------------------------------------------------------ reading Lance

    def _latest(self, dataset: Dataset) -> lance.LanceDataset | None:
        """The dataset at its latest version, or None before its first write."""
        key = (dataset.uuid, None)
        with self._open_lock:
            finder = self._open.get(key)
        if finder is None:
            try:
                finder = lance.dataset(
                    self.upstream_uri(dataset), storage_options=self.upstream_options()
                )
            except ValueError, OSError:
                return None  # nothing written yet
            self._keep(key, finder)
            self._keep((dataset.uuid, finder.version), finder)
        return self._pinned(dataset, finder.latest_version, finder)

    def _pinned(
        self, dataset: Dataset, version: int, finder: lance.LanceDataset | None = None
    ) -> lance.LanceDataset:
        key = (dataset.uuid, version)
        with self._open_lock:
            found = self._open.get(key)
            if found is not None:
                self._open.move_to_end(key)
                return found
        try:
            if finder is not None:
                found = finder.checkout_version(version)
            else:
                found = lance.dataset(
                    self.upstream_uri(dataset),
                    version=version,
                    storage_options=self.upstream_options(),
                )
        except ValueError, OSError:
            raise NotFound(
                f"dataset {dataset.org.name}/{dataset.name} has no version {version}"
            ) from None
        self._keep(key, found)
        return found

    def _keep(self, key: tuple[str, int | None], handle: lance.LanceDataset) -> None:
        with self._open_lock:
            self._open[key] = handle
            self._open.move_to_end(key)
            while len(self._open) > self.CACHE_SIZE:
                self._open.popitem(last=False)

    def _version(self, dataset: Dataset, version: int | None) -> lance.LanceDataset:
        """``version`` of the dataset (the latest if None); NotFound if it has none."""
        if version is not None:
            return self._pinned(dataset, version)
        found = self._latest(dataset)
        if found is None:
            raise NotFound(f"dataset {dataset.org.name}/{dataset.name} has no versions yet")
        return found

    # ------------------------------------------------------------------ history

    async def _commits(self, dataset: Dataset) -> dict[int, Commit]:
        return {
            record[0]: Commit(*record)
            for record in await self.store.db.fetch(
                f"SELECT {self.COMMIT_COLUMNS} FROM dataset_commits WHERE dataset_pk = $1",
                dataset.pk,
            )
        }

    def _scan(
        self, dataset: Dataset, known: dict[int, Commit]
    ) -> tuple[list[int], list[tuple], dict[int, list[str]]]:
        """Lance's versions, summaries of those without one in ``known``, and tags."""
        latest = self._latest(dataset)
        if latest is None:
            return [], [], {}
        listed = latest.versions()
        summaries = []
        for item in listed:
            version = item["version"]
            if version in known and known[version].rows is not None:
                continue
            try:
                transaction = latest.read_transaction(version)
            except ValueError, OSError:
                transaction = None
            op = message = None
            if transaction is not None:
                op = type(transaction.operation).__name__
                if op == "BaseOperation":  # an operation pylance has no class for
                    op = None
                message = (transaction.transaction_properties or {}).get(self.MESSAGE_KEY)
            meta = item.get("metadata") or {}
            stamp = item["timestamp"]  # a naive local time
            created_at = stamp.timestamp() if isinstance(stamp, dt.datetime) else float(stamp)
            summaries.append(
                (
                    version,
                    created_at,
                    op,
                    message or None,
                    int(meta.get("total_rows", 0)),
                    int(meta.get("total_files_size", 0)),
                    int(meta.get("total_fragments", 0)),
                )
            )
        tags: dict[int, list[str]] = {}
        for tag, ref in latest.tags.list().items():
            tags.setdefault(ref["version"], []).append(tag)
        return [item["version"] for item in listed], summaries, tags

    async def history(self, dataset: Dataset) -> tuple[list[Commit], dict[int, list[str]]]:
        """Every version, newest first, with summaries filled in from Lance (D42), and the
        tags by version."""
        # Postgres first: a row seen here was written before Lance is listed below, so a
        # version missing from that listing really was pruned.
        known = await self._commits(dataset)
        listed, summaries, tags = await asyncio.to_thread(self._scan, dataset, known)
        if summaries:
            # Versions the proxy didn't record (written around it) get a row without author.
            await self.store.db.pool.executemany(
                "INSERT INTO dataset_commits (dataset_pk, version, created_at, op, message, "
                "rows, bytes, fragments) "
                "VALUES ($1, $2, to_timestamp($3), $4, $5, $6, $7, $8) "
                "ON CONFLICT (dataset_pk, version) DO UPDATE SET op = EXCLUDED.op, "
                "message = coalesce(dataset_commits.message, EXCLUDED.message), "
                "rows = EXCLUDED.rows, bytes = EXCLUDED.bytes, fragments = EXCLUDED.fragments",
                [(dataset.pk, *summary) for summary in summaries],
            )
        live = set(listed)
        gone = [v for v, c in known.items() if v not in live and not c.pruned]
        if gone:
            await self.store.db.execute(
                "UPDATE dataset_commits SET pruned = true "
                "WHERE dataset_pk = $1 AND version = ANY($2)",
                dataset.pk,
                gone,
            )
        if summaries or gone:
            known = await self._commits(dataset)
        return sorted(known.values(), key=lambda c: -c.version), tags

    async def latest(self, datasets: list[Dataset]) -> dict[int, Commit]:
        """The latest recorded version of each dataset, by dataset pk. Versions recorded
        without a summary (by the proxy) get one first."""
        query = (
            f"SELECT DISTINCT ON (dataset_pk) dataset_pk, {self.COMMIT_COLUMNS} "
            "FROM dataset_commits WHERE dataset_pk = ANY($1) ORDER BY dataset_pk, version DESC"
        )
        pks = [d.pk for d in datasets]
        found = {r[0]: Commit(*r[1:]) for r in await self.store.db.fetch(query, pks)}
        missing = [d for d in datasets if d.pk in found and found[d.pk].rows is None]
        if missing:
            await asyncio.gather(*(self.history(d) for d in missing))
            found = {r[0]: Commit(*r[1:]) for r in await self.store.db.fetch(query, pks)}
        return found

    async def latest_one(self, dataset: Dataset) -> Commit | None:
        """The latest version of ``dataset``, checked against Lance."""
        commits, _ = await self.history(dataset)
        return next((c for c in commits if not c.pruned), None)

    async def commit(self, dataset: Dataset, version: int) -> tuple[Commit, list[str]]:
        commits, tags = await self.history(dataset)
        found = next((c for c in commits if c.version == version), None)
        if found is None:
            raise NotFound(f"dataset {dataset.org.name}/{dataset.name} has no version {version}")
        return found, tags.get(version, [])

    async def set_message(
        self, dataset: Dataset, version: int, user: User, message: str
    ) -> tuple[Commit, list[str]]:
        """Give a version its message, once, for edits that can't carry one (D42): only its
        author or an org admin, and only while it has none."""
        found, _ = await self.commit(dataset, version)
        if found.message:
            raise Conflict(f"version {version} already has a message")
        admin = self.store.accounts.role(user, dataset.org) == "admin"
        if not admin and found.user != user.pk:
            raise Forbidden("only the version's author or an org admin can set its message")
        await self.store.db.execute(
            "UPDATE dataset_commits SET message = $3 "
            "WHERE dataset_pk = $1 AND version = $2 AND coalesce(message, '') = ''",
            dataset.pk,
            version,
            message,
        )
        return await self.commit(dataset, version)

    async def details(self, dataset: Dataset, version: int) -> dict[str, Any]:
        """A version's schema, its Hugging Face features and its table metadata (all None for
        pruned versions)."""

        def read() -> dict[str, Any]:
            from datasets import Features  # slow to import, and only needed here

            schema = self._pinned(dataset, version).schema
            return {
                "schema": [
                    {"name": f.name, "type": str(f.type), "nullable": f.nullable} for f in schema
                ],
                "features": Features.from_arrow_schema(schema).to_dict(),
                "metadata": self._pinned(dataset, version).metadata,
            }

        try:
            return await asyncio.to_thread(read)
        except NotFound:
            return {"schema": None, "features": None, "metadata": None}

    # ------------------------------------------------------------------ rows and cards

    async def rows(
        self,
        dataset: Dataset,
        version: int | None,
        offset: int,
        limit: int,
        columns: list[str] | None,
    ) -> dict[str, Any]:
        """A page of rows, as JSON cells (see ``_cell``). ValueError for unknown columns."""

        def read() -> dict[str, Any]:
            handle = self._version(dataset, version)
            schema = handle.schema
            if columns:
                unknown = [c for c in columns if schema.get_field_index(c) < 0]
                if unknown:
                    raise ValueError(f"unknown columns: {', '.join(unknown)}")
            names = columns or schema.names
            total = handle.count_rows()
            table = handle.to_table(columns=names, offset=offset, limit=limit)
            truncated: list[list[int]] = []
            cells = []
            for col, name in enumerate(names):
                values = []
                for row, value in enumerate(table.column(name).to_pylist()):
                    cell, cut = self._cell(value)
                    values.append(cell)
                    if cut:
                        truncated.append([row, col])
                cells.append(values)
            return {
                "version": handle.version,
                "offset": offset,
                "total": total,
                "columns": [{"name": n, "type": str(schema.field(n).type)} for n in names],
                "rows": [list(row) for row in zip(*cells, strict=True)] if cells else [],
                "truncated": sorted(truncated),
            }

        return await asyncio.to_thread(read)

    @classmethod
    def _cell(cls, value: Any) -> tuple[Any, bool]:
        """A cell as JSON, and whether it was cut: long strings are cut, binary values become
        ``{"$binary": size}``, times ISO 8601 strings, nested values JSON."""
        if isinstance(value, str) and len(value) > cls.CELL_CHARS:
            return value[: cls.CELL_CHARS], True
        return cls._json(value), False

    @classmethod
    def _json(cls, value: Any) -> Any:
        if value is None or isinstance(value, bool | int | float | str):
            return value
        if isinstance(value, bytes | bytearray | memoryview):
            return {"$binary": len(value)}
        if isinstance(value, dt.datetime | dt.date | dt.time):
            return value.isoformat()
        if isinstance(value, dt.timedelta):
            return value.total_seconds()
        if isinstance(value, dict):
            return {str(k): cls._json(v) for k, v in value.items()}
        if isinstance(value, list | tuple):
            return [cls._json(v) for v in value]
        if isinstance(value, decimal.Decimal):
            return str(value)
        return str(value)

    def _card_key(self, dataset: Dataset, sha: str) -> str:
        return f"datasets/{dataset.uuid}/_flock/cards/{sha}.md"

    async def readme(self, dataset: Dataset, version: int | None) -> dict[str, Any]:
        """The card at ``version`` (the latest if None): a sidecar file the table metadata
        points to (D7 of flock.hub)."""

        def read() -> dict[str, Any]:
            handle = self._latest(dataset) if version is None else self._pinned(dataset, version)
            if handle is None:
                return {"version": None, "sha": None, "content": None}
            sha = handle.metadata.get(self.CARD_KEY)
            content = None
            if sha:
                client = self._s3(self.upstream_options())
                bucket = self.store.settings.hub_bucket
                with contextlib.suppress(client.exceptions.NoSuchKey):
                    body = client.get_object(Bucket=bucket, Key=self._card_key(dataset, sha))
                    content = body["Body"].read().decode()
            return {"version": handle.version, "sha": sha or None, "content": content}

        return await asyncio.to_thread(read)

    # ------------------------------------------------------------------ writing Lance

    @contextlib.asynccontextmanager
    async def _writing(self, dataset: Dataset):
        lock = self._writes.setdefault(dataset.pk, asyncio.Lock())
        async with lock:
            if dataset.deleted:
                raise NotFound(f"dataset {dataset.org.name}/{dataset.name} not found")
            yield

    def _open_target(self, dataset: Dataset, user: User, version: int | None = None):
        uri, options = self.write_target(dataset, user)
        try:
            return lance.dataset(uri, version=version, storage_options=options), uri, options
        except ValueError, OSError:
            if version is None:
                raise Conflict(
                    f"dataset {dataset.org.name}/{dataset.name} has no versions yet"
                ) from None
            raise NotFound(
                f"dataset {dataset.org.name}/{dataset.name} has no version {version}"
            ) from None

    async def _record(
        self, dataset: Dataset, version: int, user: User, message: str | None
    ) -> None:
        """Note who made a version the API wrote, and its message (Lance can't carry one on
        these operations). The proxy has usually inserted the row already."""
        await self.store.db.execute(
            "INSERT INTO dataset_commits (dataset_pk, version, user_pk, created_at, message) "
            "VALUES ($1, $2, $3, now(), $4) ON CONFLICT (dataset_pk, version) DO UPDATE SET "
            "user_pk = coalesce(dataset_commits.user_pk, EXCLUDED.user_pk), "
            "message = coalesce(dataset_commits.message, EXCLUDED.message)",
            dataset.pk,
            version,
            user.pk,
            message or None,
        )

    async def set_readme(
        self, dataset: Dataset, user: User, content: str, message: str | None = None
    ) -> int:
        """Write a card and point a new version at it; returns that version."""
        data = content.encode()
        sha = hashlib.sha256(data).hexdigest()

        def write() -> int:
            handle, uri, options = self._open_target(dataset, user)
            bucket, key = self._object(uri, f"_flock/cards/{sha}.md")
            self._s3(options).put_object(
                Bucket=bucket, Key=key, Body=data, ContentType="text/markdown"
            )
            handle.update_metadata({self.CARD_KEY: sha})
            return handle.version

        async with self._writing(dataset):
            version = await asyncio.to_thread(write)
            await self._record(dataset, version, user, message)
        return version

    async def tags(self, dataset: Dataset) -> dict[str, int]:
        def read() -> dict[str, int]:
            latest = self._latest(dataset)
            if latest is None:
                return {}
            return {tag: ref["version"] for tag, ref in sorted(latest.tags.list().items())}

        return await asyncio.to_thread(read)

    async def set_tag(self, dataset: Dataset, user: User, tag: str, version: int) -> None:
        def write() -> None:
            handle, _, _ = self._open_target(dataset, user)
            if version not in {v["version"] for v in handle.versions()}:
                raise NotFound(
                    f"dataset {dataset.org.name}/{dataset.name} has no version {version}"
                )
            if tag in handle.tags.list():
                handle.tags.update(tag, version)
            else:
                handle.tags.create(tag, version)

        async with self._writing(dataset):
            await asyncio.to_thread(write)

    async def delete_tag(self, dataset: Dataset, user: User, tag: str) -> None:
        def write() -> None:
            handle, _, _ = self._open_target(dataset, user)
            if tag not in handle.tags.list():
                raise NotFound(f"tag {tag} not found")
            handle.tags.delete(tag)

        async with self._writing(dataset):
            await asyncio.to_thread(write)

    async def restore(
        self, dataset: Dataset, user: User, version: int, message: str | None = None
    ) -> int:
        """Make ``version`` the latest again, as a new version; returns it."""

        def write() -> int:
            handle, _, _ = self._open_target(dataset, user, version)
            handle.restore()
            return handle.version

        async with self._writing(dataset):
            new = await asyncio.to_thread(write)
            await self._record(dataset, new, user, message)
        return new

    async def optimize(self, dataset: Dataset, user: User) -> int:
        """Compact small fragments into a new version (the latest one if there was nothing to
        compact); returns it."""

        def write() -> int:
            handle, _, _ = self._open_target(dataset, user)
            handle.optimize.compact_files()
            return handle.version

        async with self._writing(dataset):
            before = await self.latest_one(dataset)
            version = await asyncio.to_thread(write)
            if before is None or version != before.version:
                await self._record(dataset, version, user, None)
        return version

    async def prune(
        self, dataset: Dataset, user: User, older_than: dt.timedelta
    ) -> tuple[int, int]:
        """Delete versions older than ``older_than`` (except tagged ones and the latest) and
        the files only they used; returns (versions removed, bytes removed). Their history
        rows stay, marked pruned."""

        def write() -> tuple[set[int], Any]:
            handle, _, _ = self._open_target(dataset, user)
            before = {v["version"] for v in handle.versions()}
            stats = handle.cleanup_old_versions(
                older_than=older_than, error_if_tagged_old_versions=False
            )
            return before - {v["version"] for v in handle.versions()}, stats

        async with self._writing(dataset):
            await self.history(dataset)  # summaries first: Lance is about to forget them
            removed, stats = await asyncio.to_thread(write)
            with self._open_lock:
                for key in [k for k in self._open if k[0] == dataset.uuid and k[1] in removed]:
                    del self._open[key]
            if removed:
                await self.store.db.execute(
                    "UPDATE dataset_commits SET pruned = true "
                    "WHERE dataset_pk = $1 AND version = ANY($2)",
                    dataset.pk,
                    sorted(removed),
                )
        return stats.old_versions, stats.bytes_removed
