"""Datasets (013, D85-D96): the Lance datasets in each org's ``flock-datasets`` bucket.

Nothing here keeps a table of datasets: a dataset is any folder holding a ``_versions/`` folder
(D87), found by listing the bucket. Lance reads go straight to the S3 server with the global key
(D94), in threads, through a small cache of opened datasets. Deleting moves a dataset to a trash
bucket outside every org (D93), renaming copies it within the bucket (D95), and tags are Lance's
own (D96).
"""

import asyncio
import base64
import contextlib
import datetime as dt
import decimal
import logging
import math
import re
import threading
import time
from collections import OrderedDict
from collections.abc import AsyncGenerator, Awaitable, Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Any, ClassVar

import httpx
import orjson

from app.api.src.core.errors import BadGateway, Conflict, Invalid, NotFound
from app.api.src.s3_proxy.upstream import DELETE_BATCH, ListedObject, Upstream, UpstreamError

if TYPE_CHECKING:
    import asyncpg

    from app.api.src.core.accounts import User
    from app.api.src.core.s3 import Bucket
    from app.api.src.core.store import Org, Store

log = logging.getLogger(__name__)

MIB = 1024 * 1024
GIB = 1024 * MIB
MAX_U64 = 2**64 - 1
VERSIONS = "_versions/"
# Lance's default manifest names (V2 paths): ``u64::MAX - version``, 20 digits, so a listing
# sorts newest first. The older naming is ``{version}.manifest``.
V2_MANIFEST = re.compile(r"(\d{20})\.manifest")
OLD_MANIFEST = re.compile(r"(\d+)\.manifest")
# A new dataset path (D95): segments of letters, digits, `.`, `-` and `_`, joined by `/`.
SEGMENT = re.compile(r"[A-Za-z0-9._-]+")
# Tag names (D96): ASCII only, since pylance 14 can't list tags once one isn't.
TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
IMAGE_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


@dataclass(frozen=True)
class Version:
    """A version of a dataset, and its manifest's ETag and time (from S3)."""

    number: int
    etag: str
    modified: float


class TooLarge(Exception):
    """A delete or rename over the limits (HTTP 409), with the command to run instead."""

    def __init__(self, message: str, command: str) -> None:
        super().__init__(message)
        self.message = message
        self.command = command


class Taken(Conflict):
    """Something already exists at the path a dataset would move to (HTTP 409)."""


class TagTaken(Conflict):
    """The tag is already on another version (HTTP 409); ``version`` is that one."""

    def __init__(self, tag: str, version: int) -> None:
        super().__init__(f"{tag} is on version {version}.")
        self.version = version


class LanceFailed(BadGateway):
    """Lance couldn't read or write the dataset (a corrupt manifest, a missing file): 502."""


def newest(found: tuple[ListedObject] | tuple[()] | None) -> int | None:
    """The version the newest manifest of a ``Datasets.latest`` answer names, when the dataset
    uses Lance's default naming (None for the older one, whose listing isn't newest first)."""
    if not found or not V2_MANIFEST.fullmatch(found[0].key.rpartition("/")[2]):
        return None
    return manifest_version(found[0].key)


def manifest_version(key: str) -> int | None:
    """The version a ``_versions/`` key names, or None if it isn't a manifest."""
    name = key.rpartition("/")[2]
    if m := V2_MANIFEST.fullmatch(name):
        return MAX_U64 - int(m[1])
    if m := OLD_MANIFEST.fullmatch(name):
        return int(m[1])
    return None


def check_path(path: str) -> None:
    """Raises ``Invalid`` unless ``path`` names a folder of the bucket (not its root)."""
    if not path or len(path.encode()) > 1024:
        raise Invalid("dataset paths are 1 to 1024 bytes")
    if path.startswith("/") or path.endswith("/") or "//" in path:
        raise Invalid("dataset paths don't start or end with /")
    if any(ord(c) < 32 or ord(c) == 127 for c in path):
        raise Invalid("dataset paths can't hold control characters")


def check_new_path(path: str) -> None:
    """Raises ``Invalid`` unless ``path`` is a path a dataset may be moved to (D95)."""
    if not path or len(path) > 512:
        raise Invalid("Paths are 1 to 512 characters.")
    segments = path.split("/")
    if any(not SEGMENT.fullmatch(s) or s in (".", "..") for s in segments):
        raise Invalid(
            "Paths are folder names of letters, digits, dots, hyphens and underscores, joined by /."
        )
    if "_versions" in segments:
        raise Invalid("Paths can't hold a folder named _versions.")


def check_tag(tag: str) -> None:
    if not TAG.fullmatch(tag):
        raise Invalid(
            "Tag names are up to 100 letters, digits, dots, hyphens and underscores, starting "
            "with a letter or digit."
        )
    if tag.isdigit():
        raise Invalid("Tag names can't be only digits, since those are version numbers.")


def image_type(data: bytes) -> str | None:
    """The image type of ``data`` by its first bytes: PNG, JPEG, GIF or WebP."""
    for magic, mime in IMAGE_MAGIC:
        if data.startswith(magic):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def jsonable(value: Any) -> Any:
    """A Python value from Arrow as JSON can hold it: bytes as their size, times as ISO 8601."""
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<{len(value)} bytes>"
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, (decimal.Decimal, dt.timedelta)):
        return str(value)
    return value


def is_blob(field: Any) -> bool:
    """A Lance blob column, which Lance reads as descriptions (position and size)."""
    name = getattr(field.type, "extension_name", "") or ""
    metadata = field.metadata or {}
    return name.startswith("lance.blob") or metadata.get(b"lance-encoding:blob") == b"true"


def is_binary(typ: Any) -> bool:
    import pyarrow as pa

    return (
        pa.types.is_binary(typ)
        or pa.types.is_large_binary(typ)
        or pa.types.is_fixed_size_binary(typ)
        or pa.types.is_binary_view(typ)
    )


def is_image_struct(typ: Any) -> bool:
    """A struct of ``{bytes, path}``, which is how HF ``datasets`` stores its ``Image``."""
    import pyarrow as pa

    if not pa.types.is_struct(typ):
        return False
    names = {typ.field(i).name: typ.field(i).type for i in range(typ.num_fields)}
    return set(names) == {"bytes", "path"} and is_binary(names["bytes"])


def has_binary(typ: Any) -> bool:
    """Whether a column's cells may hold binary data (images), for the page size."""
    import pyarrow as pa

    if is_binary(typ):
        return True
    if pa.types.is_struct(typ):
        return any(has_binary(typ.field(i).type) for i in range(typ.num_fields))
    if pa.types.is_list(typ) or pa.types.is_large_list(typ) or pa.types.is_fixed_size_list(typ):
        return has_binary(typ.value_type)
    return False


def type_name(field: Any) -> str:
    return "blob" if is_blob(field) else str(field.type)


def field_tree(field: Any) -> dict[str, Any]:
    """A schema field as the Schema tab shows it, children included (D91)."""
    import pyarrow as pa

    typ = field.type
    children: list[Any] = []
    if is_blob(field):
        pass
    elif pa.types.is_struct(typ):
        children = [typ.field(i) for i in range(typ.num_fields)]
    elif pa.types.is_list(typ) or pa.types.is_large_list(typ) or pa.types.is_fixed_size_list(typ):
        children = [typ.value_field]
    elif pa.types.is_map(typ):
        children = [typ.key_field, typ.item_field]
    return {
        "name": field.name,
        "type": type_name(field),
        "nullable": field.nullable,
        "metadata": decoded(field.metadata),
        "children": [field_tree(child) for child in children],
    }


def decoded(metadata: dict[bytes, bytes] | None) -> dict[str, str]:
    return {
        k.decode(errors="replace"): v.decode(errors="replace") for k, v in (metadata or {}).items()
    }


def hf_features(schema: Any) -> dict[str, Any]:
    """The HF ``datasets`` features in the schema's metadata (``huggingface``), by column."""
    raw = (schema.metadata or {}).get(b"huggingface")
    if not raw:
        return {}
    try:
        features = orjson.loads(raw).get("info", {}).get("features", {})
    except orjson.JSONDecodeError, AttributeError:
        return {}
    return features if isinstance(features, dict) else {}


def written(ds: Any, fallback: float) -> float:
    """When the version ``ds`` is opened at was written, as its manifest says (S3's time of the
    manifest changes when a dataset is copied, as renames and restores do)."""
    try:
        stamp = ds.get_version()["timestamp"]
    except KeyError, TypeError, ValueError:
        return fallback
    # Lance gives naive local times; ``timestamp()`` reads a naive time as local.
    return stamp.timestamp() if isinstance(stamp, dt.datetime) else fallback


def index_type(type_url: str) -> str:
    """``/lance.table.BTreeIndexDetails`` as ``BTree``."""
    name = type_url.rpartition(".")[2] or type_url
    return name.removesuffix("IndexDetails") or name


class Renderer:
    """Turns Arrow cells into the plain JSON the row viewer shows (D90). ``budget`` is how many
    bytes of binary cells a page may still send; past it they show only their size."""

    def __init__(self, text: int, image: int, budget: int, head: int) -> None:
        self.text = text
        self.image = image
        self.budget = budget
        self.head = head

    def string(self, value: str) -> dict[str, Any]:
        if len(value) > self.text:
            return {"k": "str", "v": value[: self.text], "len": len(value)}
        return {"k": "str", "v": value}

    def binary(self, data: bytes) -> dict[str, Any]:
        size = len(data)
        mime = image_type(data) if size <= self.image else None
        if mime is not None and size <= self.budget:
            self.budget -= size
            return {
                "k": "image",
                "mime": mime,
                "size": size,
                "data": base64.b64encode(data).decode(),
            }
        return {"k": "bin", "size": size}

    def json(self, value: Any) -> dict[str, Any]:
        text = orjson.dumps(jsonable(value)).decode()
        if len(text) > self.text:
            return {"k": "json", "v": text[: self.text], "len": len(text)}
        return {"k": "json", "v": text}

    def cell(self, field: Any, value: Any, feature: Any = None) -> dict[str, Any]:
        import pyarrow as pa

        if value is None:
            return {"k": "null"}
        if is_blob(field):
            return {"k": "blob", "size": value.get("size") if isinstance(value, dict) else None}
        typ = field.type
        if pa.types.is_dictionary(typ):
            typ = typ.value_type
        if pa.types.is_boolean(typ):
            return {"k": "bool", "v": value}
        if pa.types.is_integer(typ):
            out: dict[str, Any] = {"k": "num", "v": value if abs(value) < 2**53 else str(value)}
            names = feature.get("names") if isinstance(feature, dict) else None
            if (
                isinstance(feature, dict)
                and feature.get("_type") == "ClassLabel"
                and isinstance(names, list)
                and 0 <= value < len(names)
            ):
                out["label"] = str(names[value])
            return out
        if pa.types.is_floating(typ):
            return {"k": "num", "v": value if math.isfinite(value) else str(value)}
        if pa.types.is_decimal(typ):
            return {"k": "num", "v": str(value)}
        if pa.types.is_string(typ) or pa.types.is_large_string(typ) or pa.types.is_string_view(typ):
            return self.string(value)
        if pa.types.is_temporal(typ) and not pa.types.is_duration(typ):
            return {"k": "time", "v": value.isoformat()}
        if is_binary(typ):
            return self.binary(value)
        if is_image_struct(typ):
            data = value.get("bytes")
            if data is None:
                return self.string(value.get("path") or "") if value.get("path") else {"k": "null"}
            return self.binary(data)
        if pa.types.is_fixed_size_list(typ) and pa.types.is_floating(typ.value_type):
            head = [v if v is None or math.isfinite(v) else str(v) for v in value[: self.head]]
            return {"k": "vec", "dtype": str(typ.value_type), "len": typ.list_size, "head": head}
        if pa.types.is_nested(typ):
            return self.json(value)
        return self.string(str(jsonable(value)))


class Datasets:
    """The Datasets tab's reads and writes. Every route needs S3 on (``S3.require``)."""

    PAGE: ClassVar[int] = 100  # folder entries per page (D87)
    DETECT_AT_ONCE: ClassVar[int] = 16
    ROW_PAGE: ClassVar[int] = 50  # rows per page (D90), or BINARY_ROW_PAGE with binary columns
    BINARY_ROW_PAGE: ClassVar[int] = 10
    MAX_ROW_PAGE: ClassVar[int] = 100
    CELL_TEXT: ClassVar[int] = 500  # characters of a string or JSON cell in a page
    CELL_FULL: ClassVar[int] = MIB  # of a cell opened in the side sheet
    IMAGE_LIMIT: ClassVar[int] = MIB  # images larger than this show their size
    PAGE_BYTES: ClassVar[int] = 64 * MIB  # binary cell data per page
    VECTOR_HEAD: ClassVar[int] = 8
    VERSION_PAGE: ClassVar[int] = 50
    README_BYTES: ClassVar[int] = MIB
    CACHE: ClassVar[int] = 32  # opened datasets (D94)
    COPY_AT_ONCE: ClassVar[int] = 16
    MOVE_OBJECTS: ClassVar[int] = 10_000  # a delete's or rename's limits (D93, D95)
    MOVE_BYTES: ClassVar[int] = 100 * GIB
    COPY_LIMIT: ClassVar[int] = 5 * GIB  # larger objects are copied in parts
    COPY_PART: ClassVar[int] = 512 * MIB
    TRASH_DAYS: ClassVar[int] = 14
    SWEEP_INTERVAL: ClassVar[float] = 600.0
    STUCK_AFTER: ClassVar[float] = 3600.0  # a move left unfinished this long is settled
    ORPHAN_AFTER: ClassVar[float] = 86400.0  # trash prefixes with no row, removed after this

    def __init__(self, store: Store) -> None:
        self.store = store
        self.settings = store.settings
        self._cache: OrderedDict[tuple[str, str, int, str], Any] = OrderedDict()
        self._cache_lock = threading.Lock()
        self._trash_ready = False
        self._tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------------ lifecycle

    @property
    def trash(self) -> str:
        """The trash bucket upstream (D93): no row in ``s3_buckets``, so no key reaches it."""
        prefix = self.settings.s3_bucket_prefix
        return self.settings.s3_trash_bucket or f"{prefix}flock-trash"

    async def load(self) -> None:
        if self.store.s3.upstream is None:
            return
        self._spawn(self._startup())
        self._spawn(self._sweep_loop())

    async def close(self) -> None:
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()
        with self._cache_lock:
            self._cache.clear()

    def _spawn(self, coro: Awaitable[Any]) -> None:
        async def run() -> None:
            await coro

        task = asyncio.create_task(run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _startup(self) -> None:
        """The trash bucket and every org's managed bucket, if missing (D85, D93)."""
        try:
            await self._ensure_trash()
        except Exception:
            log.exception("could not create the trash bucket %s", self.trash)
        await self.store.s3.ensure_all_managed()

    async def _ensure_trash(self) -> str:
        upstream = self._upstream()
        if not self._trash_ready:
            if not await upstream.bucket_exists(self.trash):
                try:
                    await upstream.create_bucket(self.trash)
                except UpstreamError as e:
                    if e.code not in ("BucketAlreadyExists", "BucketAlreadyOwnedByYou"):
                        raise
            self._trash_ready = True
        return self.trash

    def _upstream(self) -> Upstream:
        return self.store.s3.require()

    @staticmethod
    async def _call[T](call: Awaitable[T]) -> T:
        """An upstream call, its failures as ``BadGateway``."""
        try:
            return await call
        except UpstreamError as e:
            raise BadGateway(f"the S3 server refused: {e}") from None
        except httpx.HTTPError as e:
            raise BadGateway(f"the S3 server couldn't be reached: {e}") from None

    async def bucket(self, org: Org, ensure: bool = False) -> Bucket:
        """The org's flock-datasets; with ``ensure``, created if it has none (the Datasets tab
        heals a failed creation, D85)."""
        s3 = self.store.s3
        s3.require()
        found = await s3.managed_bucket(org)
        if found is None and ensure:
            try:
                found = await s3.ensure_managed(org)
            except (Conflict, BadGateway, Invalid) as e:
                log.warning("could not create %s for %s: %s", s3.DATASETS, org.name, e)
                raise BadGateway(f"The {s3.DATASETS} bucket couldn't be created. {e}") from None
        if found is None:
            raise NotFound(f"{s3.DATASETS} doesn't exist yet")
        return found

    # ------------------------------------------------------------------ Lance (D94)

    def storage_options(self) -> dict[str, str]:
        """Lance's options for reaching the S3 server directly, with the global key."""
        settings = self.settings
        endpoint = settings.s3_upstream_endpoint or ""
        if "://" not in endpoint:
            endpoint = f"https://{endpoint}"
        options = {
            "aws_endpoint": endpoint,
            "aws_access_key_id": settings.s3_upstream_access_key_id or "",
            "aws_secret_access_key": settings.s3_upstream_secret_access_key or "",
            "aws_region": settings.s3_upstream_region,
            "aws_virtual_hosted_style_request": "false",
        }
        if endpoint.startswith("http://"):
            options["allow_http"] = "true"
        return options

    @staticmethod
    def uri(bucket: str, path: str) -> str:
        return f"s3://{bucket}/{path}"

    def _open(self, bucket: str, path: str, version: Version) -> Any:
        """The dataset at ``version``, from the cache or opened now (in a thread). The cache is
        keyed by the manifest's ETag too, so a dataset written again at the same path and
        version number isn't mistaken for the old one."""
        import lance

        key = (bucket, path, version.number, version.etag)
        with self._cache_lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
        ds = lance.dataset(
            self.uri(bucket, path), version=version.number, storage_options=self.storage_options()
        )
        with self._cache_lock:
            self._cache[key] = ds
            while len(self._cache) > self.CACHE:
                self._cache.popitem(last=False)
        return ds

    def _forget(self, bucket: str, path: str) -> None:
        """Drop cached versions of ``path`` (after it was deleted or renamed)."""
        with self._cache_lock:
            for key in [k for k in self._cache if k[0] == bucket and k[1] == path]:
                del self._cache[key]

    @staticmethod
    async def _lance[T](call: Callable[[], T]) -> T:
        """A Lance call in a thread; its failures (other than ours) as 502 with its message."""
        try:
            return await asyncio.to_thread(call)
        except NotFound, Conflict, Invalid, BadGateway:
            raise
        except Exception as e:
            raise LanceFailed(f"Lance couldn't read the dataset: {e}") from e

    async def latest(self, bucket: str, path: str) -> tuple[ListedObject] | tuple[()] | None:
        """The newest manifest of ``path`` from one LIST (D87): ``(object,)``, or ``()`` when
        the folder has ``_versions/`` without a manifest first, None if it isn't a dataset."""
        listing = await self._call(
            self._upstream().list_objects(bucket, f"{path}/{VERSIONS}", None, None, 1)
        )
        if not listing.objects:
            return None
        first = listing.objects[0]
        return (first,) if manifest_version(first.key) is not None else ()

    async def latest_version(self, bucket: str, path: str) -> int | None:
        """The latest version from one LIST, or None when its manifests use the older naming
        (whose listing doesn't sort newest first). Raises ``NotFound`` if not a dataset."""
        found = await self.latest(bucket, path)
        if found is None:
            raise NotFound(f"dataset {path} not found")
        return newest(found)

    async def _manifest(self, bucket: str, path: str, number: int) -> Version | None:
        upstream = self._upstream()
        for name in (f"{MAX_U64 - number:020d}.manifest", f"{number}.manifest"):
            response = await self._call(upstream.head(bucket, f"{path}/{VERSIONS}{name}"))
            if response is not None:
                modified = response.headers.get("last-modified")
                return Version(
                    number,
                    response.headers.get("etag", "").strip('"'),
                    parsedate_to_datetime(modified).timestamp() if modified else 0.0,
                )
        return None

    async def resolve(self, bucket: str, path: str, version: str | None) -> Version:
        """``version`` (a number, a tag, or None for the latest) of the dataset at ``path``.
        The latest is always asked of S3 (D94)."""
        if version is None or version == "":
            found = await self.latest(bucket, path)
            if found is None:
                raise NotFound(f"dataset {path} not found")
            if found and V2_MANIFEST.fullmatch(found[0].key.rpartition("/")[2]):
                first = found[0]
                return Version(
                    manifest_version(first.key) or 0, first.etag.strip('"'), first.last_modified
                )
            # The older naming: ask Lance which version is the latest.
            import lance

            uri, options = self.uri(bucket, path), self.storage_options()
            number = await self._lance(lambda: lance.dataset(uri, storage_options=options).version)
            got = await self._manifest(bucket, path, number)
            if got is None:
                raise NotFound(f"dataset {path} not found")
            return got
        if version.isdigit():
            got = await self._manifest(bucket, path, int(version))
            if got is None:
                if await self.latest(bucket, path) is None:
                    raise NotFound(f"dataset {path} not found")
                raise NotFound(f"version {version} of {path} not found")
            return got
        latest = await self.resolve(bucket, path, None)
        ds = await self._lance(lambda: self._open(bucket, path, latest))

        def tagged() -> int | None:
            tags = ds.tags.list()
            return tags[version]["version"] if version in tags else None

        number = await self._lance(tagged)
        if number is None:
            raise NotFound(f"tag {version} of {path} not found")
        got = await self._manifest(bucket, path, number)
        if got is None:
            raise NotFound(f"version {number} of {path} not found")
        return got

    # ------------------------------------------------------------------ folders (D87)

    async def at(self, org: Org, path: str, cursor: str | None) -> dict[str, Any]:
        """A folder's listing, one level, or ``{kind: "dataset"}`` when ``path`` is one."""
        bucket = await self.bucket(org, ensure=True)
        name = bucket.upstream_name
        if path:
            check_path(path)
            found = await self.latest(name, path)
            if found is not None:
                return {
                    "kind": "dataset",
                    "path": path,
                    "version": newest(found),
                    "updated_at": found[0].last_modified if found else None,
                }
            # Inside a dataset, the browser doesn't descend (its data/, _versions/ and the rest
            # are only in Storage).
            parts = path.split("/")
            ancestors = ["/".join(parts[:i]) for i in range(1, len(parts))]
            inside = await asyncio.gather(*(self.latest(name, a) for a in ancestors))
            if any(found is not None for found in inside):
                raise NotFound(f"folder {path} not found")
        prefix = f"{path}/" if path else ""
        listing = await self._call(
            self._upstream().list_objects(name, prefix, "/", cursor, self.PAGE)
        )
        limit = asyncio.Semaphore(self.DETECT_AT_ONCE)

        async def detect(folder: str) -> tuple[ListedObject] | tuple[()] | None:
            async with limit:
                return await self.latest(name, folder.removesuffix("/"))

        detected = await asyncio.gather(*(detect(f) for f in listing.prefixes))
        datasets, folders = [], []
        for folder, found in zip(listing.prefixes, detected, strict=True):
            sub = folder.removesuffix("/")
            entry = {"name": sub.rpartition("/")[2], "path": sub}
            if found is None:
                folders.append(entry)
                continue
            datasets.append(
                {
                    **entry,
                    "version": newest(found),
                    "updated_at": found[0].last_modified if found else None,
                }
            )
        files = [
            {
                "name": o.key.rpartition("/")[2],
                "key": o.key,
                "size": o.size,
                "last_modified": o.last_modified,
            }
            for o in listing.objects
            if o.key != prefix
        ]
        marker = any(o.key == prefix for o in listing.objects)
        if path and not cursor and not (datasets or folders or files or marker):
            raise NotFound(f"folder {path} not found")
        return {
            "kind": "folder",
            "path": path,
            "datasets": datasets,
            "folders": folders,
            "files": files,
            "cursor": listing.cursor,
        }

    # ------------------------------------------------------------------ the dataset page

    async def info(self, org: Org, path: str, version: str | None) -> dict[str, Any]:
        """The header's numbers, the schema and details of ``path`` at ``version`` (D88, D91)."""
        bucket = await self.bucket(org)
        name = bucket.upstream_name
        check_path(path)
        resolved = await self.resolve(name, path, version)
        latest = await self.latest_version(name, path)

        def read() -> dict[str, Any]:
            ds = self._open(name, path, resolved)
            schema = ds.schema
            fragments = ds.get_fragments()
            size = sum(
                f.file_size_bytes or 0 for fragment in fragments for f in fragment.metadata.files
            )
            tags = sorted(
                ({"name": tag, "version": info["version"]} for tag, info in ds.tags.list().items()),
                key=lambda t: (-t["version"], t["name"]),
            )
            indices = [
                {"name": i.name, "type": index_type(i.type_url), "columns": list(i.field_names)}
                for i in ds.describe_indices()
            ]
            return {
                "timestamp": written(ds, resolved.modified),
                "rows": ds.count_rows(),
                "columns": len(schema),
                "fragments": len(fragments),
                "data_size": size,
                "schema": [field_tree(field) for field in schema],
                "metadata": decoded(schema.metadata),
                "binary": any(has_binary(f.type) and not is_blob(f) for f in schema),
                "details": {
                    "data_storage_version": ds.data_storage_version,
                    "stable_row_ids": bool(ds.has_stable_row_ids),
                    "fragments": len(fragments),
                    "indices": indices,
                },
                "tags": tags,
            }

        info = await self._lance(read)
        return {
            "path": path,
            "version": resolved.number,
            "latest_version": latest,
            **info,
        }

    async def rows(
        self,
        org: Org,
        path: str,
        version: str | None,
        offset: int,
        limit: int | None,
        columns: list[str] | None,
    ) -> dict[str, Any]:
        """One page of rendered cells (D90): hidden columns aren't read."""
        bucket = await self.bucket(org)
        name = bucket.upstream_name
        check_path(path)
        resolved = await self.resolve(name, path, version)

        def read() -> dict[str, Any]:
            ds = self._open(name, path, resolved)
            schema = ds.schema
            names = columns or schema.names
            unknown = [c for c in names if c not in schema.names]
            if unknown:
                raise Invalid(f"no column {unknown[0]}")
            fields = [schema.field(c) for c in names]
            binary = any(has_binary(f.type) and not is_blob(f) for f in fields)
            size = limit or (self.BINARY_ROW_PAGE if binary else self.ROW_PAGE)
            size = min(size, self.MAX_ROW_PAGE)
            total = ds.count_rows()
            features = hf_features(schema)
            renderer = Renderer(self.CELL_TEXT, self.IMAGE_LIMIT, self.PAGE_BYTES, self.VECTOR_HEAD)
            out: list[list[dict[str, Any]]] = []
            if offset < total:
                # One row at a time when cells may be large, so a page never holds more than
                # one row's bytes beyond what it sends.
                scanner = ds.scanner(
                    columns=names, offset=offset, limit=size, batch_size=1 if binary else None
                )
                for batch in scanner.to_batches():
                    values = [batch.column(i).to_pylist() for i in range(batch.num_columns)]
                    for row in range(batch.num_rows):
                        out.append(
                            [
                                renderer.cell(f, values[i][row], features.get(f.name))
                                for i, f in enumerate(fields)
                            ]
                        )
            return {
                "version": resolved.number,
                "offset": offset,
                "limit": size,
                "total": total,
                "columns": [
                    {
                        "name": f.name,
                        "type": type_name(f),
                        **(
                            {"feature": features[f.name].get("_type")}
                            if f.name in features and isinstance(features[f.name], dict)
                            else {}
                        ),
                    }
                    for f in fields
                ],
                "rows": out,
            }

        return await self._lance(read)

    async def cell(
        self, org: Org, path: str, version: str | None, row: int, column: str
    ) -> dict[str, Any]:
        """One cell in full, up to 1 MiB, for the side sheet."""
        bucket = await self.bucket(org)
        name = bucket.upstream_name
        check_path(path)
        resolved = await self.resolve(name, path, version)

        def read() -> dict[str, Any]:
            ds = self._open(name, path, resolved)
            schema = ds.schema
            if column not in schema.names:
                raise NotFound(f"no column {column}")
            if row >= ds.count_rows():
                raise NotFound(f"no row {row}")
            field = schema.field(column)
            table = ds.scanner(columns=[column], offset=row, limit=1).to_table()
            value = table.column(0).to_pylist()[0]
            features = hf_features(schema)
            renderer = Renderer(self.CELL_FULL, self.IMAGE_LIMIT, self.IMAGE_LIMIT, 64)
            cell = renderer.cell(field, value, features.get(column))
            if cell["k"] == "vec":  # the whole vector, as JSON
                cell = renderer.json(value)
            return {"row": row, "column": column, "type": type_name(field), "cell": cell}

        return await self._lance(read)

    async def versions(self, org: Org, path: str, cursor: int | None) -> dict[str, Any]:
        """The history, newest first, 50 a page (D89). ``cursor`` is the version to continue
        below; the next page's is in the answer, null on the last."""
        bucket = await self.bucket(org)
        name = bucket.upstream_name
        check_path(path)
        listed = await self._call(self._upstream().list_all(name, f"{path}/{VERSIONS}"))
        found: dict[int, ListedObject] = {}
        for o in listed:
            number = manifest_version(o.key)
            if number is not None:
                found[number] = o
        if not found:
            raise NotFound(f"dataset {path} not found")
        numbers = sorted(found, reverse=True)
        below = [n for n in numbers if cursor is None or n < cursor]
        page = below[: self.VERSION_PAGE]
        more = len(below) > len(page)
        older = below[len(page)] if more else None

        def version(n: int) -> Version:
            o = found[n]
            return Version(n, o.etag.strip('"'), o.last_modified)

        def read() -> dict[str, Any]:
            import lance

            options = self.storage_options()
            uri = self.uri(name, path)
            tags: dict[int, list[str]] = {}
            for tag, info in self._open(name, path, version(numbers[0])).tags.list().items():
                tags.setdefault(info["version"], []).append(tag)

            def detail(n: int) -> dict[str, Any]:
                ds = lance.dataset(uri, version=n, storage_options=options)
                transaction = ds.read_transaction(n)
                operation = (
                    type(transaction.operation).__name__ if transaction is not None else None
                )
                properties = dict(transaction.transaction_properties or {}) if transaction else {}
                return {
                    "rows": ds.count_rows(),
                    "operation": operation,
                    "properties": properties,
                    "timestamp": written(ds, found[n].last_modified),
                }

            wanted = page + ([older] if older is not None else [])
            with ThreadPoolExecutor(8) as pool:
                details = dict(zip(wanted, pool.map(detail, wanted), strict=True))
            out = []
            for i, n in enumerate(page):
                d = details[n]
                previous = page[i + 1] if i + 1 < len(page) else older
                delta = d["rows"] - details[previous]["rows"] if previous is not None else None
                out.append(
                    {
                        "version": n,
                        "timestamp": d["timestamp"],
                        "tags": sorted(tags.get(n, [])),
                        "operation": d["operation"],
                        "rows": d["rows"],
                        "rows_delta": delta if previous == n - 1 else None,
                        "properties": d["properties"],
                    }
                )
            return {"versions": out, "latest": numbers[0], "cursor": page[-1] if more else None}

        return await self._lance(read)

    async def readme(self, org: Org, path: str) -> dict[str, Any]:
        """The dataset's README.md, its first 1 MiB (D92)."""
        bucket = await self.bucket(org)
        name = bucket.upstream_name
        check_path(path)
        if await self.latest(name, path) is None:
            raise NotFound(f"dataset {path} not found")
        data = await self._call(
            self._upstream().get_range(name, f"{path}/README.md", self.README_BYTES + 1)
        )
        if data is None:
            raise NotFound("This dataset has no README.")
        truncated = len(data) > self.README_BYTES
        text = data[: self.README_BYTES].decode("utf-8", errors="replace")
        return {"text": text, "truncated": truncated}

    # ------------------------------------------------------------------ moving objects

    @contextlib.asynccontextmanager
    async def _locked(
        self, bucket: str, paths: Iterable[str]
    ) -> AsyncGenerator[asyncpg.Connection]:
        """Hold the advisory locks of ``paths`` (D93, D95): renames, deletes and restores of
        the same path wait for each other. Yields the connection, in its transaction."""
        async with self.store.db.pool.acquire() as conn, conn.transaction():
            for path in sorted(set(paths)):
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended($1::text, 0))",
                    f"dataset:{bucket}/{path}",
                )
            yield conn

    def _check_size(self, objects: list[ListedObject], what: str, command: str) -> None:
        total = sum(o.size for o in objects)
        if len(objects) > self.MOVE_OBJECTS or total > self.MOVE_BYTES:
            raise TooLarge(
                f"This dataset is too large to {what} here (more than "
                f"{self.MOVE_OBJECTS:,} objects or {self.MOVE_BYTES // GIB} GiB). "
                f"Run {command} instead.",
                command,
            )

    async def _copy_all(
        self,
        objects: list[ListedObject],
        source: str,
        source_prefix: str,
        target: str,
        target_prefix: str,
    ) -> None:
        """Copy ``objects`` (under ``source_prefix``) to ``target_prefix``, server side, 16 at a
        time: ``_versions/`` last, so the copy isn't a dataset until all its files are there."""
        upstream = self._upstream()
        limit = asyncio.Semaphore(self.COPY_AT_ONCE)

        async def copy(o: ListedObject) -> None:
            key = target_prefix + o.key.removeprefix(source_prefix)
            async with limit:
                if o.size > self.COPY_LIMIT:
                    await upstream.copy_large(source, o.key, o.size, target, key, self.COPY_PART)
                else:
                    await upstream.copy_object(source, o.key, target, key)

        versions = source_prefix + VERSIONS
        rest = [o for o in objects if not o.key.startswith(versions)]
        last = [o for o in objects if o.key.startswith(versions)]
        for group in (rest, last):
            await self._call(asyncio.gather(*(copy(o) for o in group)))

    async def _delete_all(self, bucket: str, keys: list[str], prefix: str) -> None:
        """Delete ``keys`` (under ``prefix``), ``_versions/`` first: once those are gone, what's
        left is loose files, never a broken dataset."""
        upstream = self._upstream()
        versions = prefix + VERSIONS
        first = [k for k in keys if k.startswith(versions)]
        rest = [k for k in keys if not k.startswith(versions)]
        for group in (first, rest):
            for start in range(0, len(group), DELETE_BATCH):
                await self._call(upstream.delete_keys(bucket, group[start : start + DELETE_BATCH]))

    async def _remove_prefix(self, bucket: str, prefix: str, before: float | None = None) -> int:
        """Delete every object under ``prefix`` (only those older than ``before``, if given)."""
        objects = await self._call(self._upstream().list_all(bucket, prefix))
        keys = [o.key for o in objects if before is None or o.last_modified <= before]
        await self._delete_all(bucket, keys, prefix)
        return len(keys)

    async def _exists(self, bucket: str, path: str) -> bool:
        listing = await self._call(self._upstream().list_objects(bucket, f"{path}/", None, None, 1))
        return bool(listing.objects)

    async def _inside_dataset(self, bucket: str, path: str) -> bool:
        parts = path.split("/")
        ancestors = ["/".join(parts[:i]) for i in range(1, len(parts))]
        found = await asyncio.gather(*(self.latest(bucket, a) for a in ancestors))
        return any(f is not None for f in found)

    async def _check_target(self, bucket: str, path: str) -> None:
        """Raises unless a dataset may move to ``path``: nothing there, not inside a dataset."""
        if await self._exists(bucket, path):
            raise Taken(f"Something already exists at {path}.")
        if await self._inside_dataset(bucket, path):
            raise Invalid("The new path can't be inside another dataset.")

    # ------------------------------------------------------------------ renaming (D95)

    async def rename(self, org: Org, path: str, to: str) -> dict[str, Any]:
        """Move the dataset at ``path`` to ``to``: copy, check nothing was written meanwhile,
        then delete the old objects (D95)."""
        bucket = await self.bucket(org)
        name = bucket.upstream_name
        check_path(path)
        check_new_path(to)
        if to == path or to.startswith(f"{path}/"):
            raise Invalid("The new path can't be inside the dataset.")
        async with self._locked(name, [path, to]):
            before = await self.resolve(name, path, None)
            await self._check_target(name, to)
            objects = await self._call(
                self._upstream().list_all(name, f"{path}/", self.MOVE_OBJECTS)
            )
            command = (
                f"aws s3 mv --recursive s3://{self.store.s3.DATASETS}/{path}/ "
                f"s3://{self.store.s3.DATASETS}/{to}/ --profile flock"
            )
            self._check_size(objects, "rename", command)
            await self._copy_all(objects, name, f"{path}/", name, f"{to}/")
            try:
                after = await self.resolve(name, path, None)
            except NotFound:
                after = None
            if after != before:
                await self._remove_prefix(name, f"{to}/")
                raise Conflict("The dataset changed while it was being renamed. Try again.")
            await self._delete_all(name, [o.key for o in objects], f"{path}/")
        self._forget(name, path)
        return {"path": to}

    # ------------------------------------------------------------------ tags (D96)

    async def set_tag(
        self, org: Org, path: str, tag: str, version: int, move: bool
    ) -> dict[str, Any]:
        """Put ``tag`` on ``version``. If it's on another version, it moves only with ``move``;
        otherwise ``TagTaken`` says where it is."""
        bucket = await self.bucket(org)
        name = bucket.upstream_name
        check_path(path)
        check_tag(tag)
        await self.resolve(name, path, str(version))
        import lance

        uri, options = self.uri(name, path), self.storage_options()

        def write() -> None:
            ds = lance.dataset(uri, storage_options=options)
            tags = ds.tags.list()
            if tag in tags:
                current = tags[tag]["version"]
                if current == version:
                    return
                if not move:
                    raise TagTaken(tag, current)
                ds.tags.update(tag, version)
                return
            try:
                ds.tags.create(tag, version)
            except ValueError as e:
                if "already exists" not in str(e):
                    raise
                now = lance.dataset(uri, storage_options=options).tags.list()
                raise TagTaken(tag, now[tag]["version"] if tag in now else version) from None

        await self._lance(write)
        return {"tag": tag, "version": version}

    async def delete_tag(self, org: Org, path: str, tag: str) -> None:
        bucket = await self.bucket(org)
        name = bucket.upstream_name
        check_path(path)
        if await self.latest(name, path) is None:
            raise NotFound(f"dataset {path} not found")
        import lance

        uri, options = self.uri(name, path), self.storage_options()

        def write() -> None:
            ds = lance.dataset(uri, storage_options=options)
            if tag not in ds.tags.list():
                raise NotFound(f"tag {tag} not found")
            ds.tags.delete(tag)

        await self._lance(write)

    # ------------------------------------------------------------------ the trash (D93)

    def _trash_prefix(self, org_pk: int, pk: int) -> str:
        return f"{org_pk:05d}/{pk}/"

    TRASH_COLUMNS: ClassVar[str] = (
        "pk, path, deleted_by, extract(epoch FROM deleted_at)::float8, "
        "extract(epoch FROM moved_at)::float8, latest_version, objects, bytes"
    )

    def _entry(self, record: Any) -> dict[str, Any]:
        pk, path, deleted_by, deleted_at, moved_at, latest, objects, size = record
        return {
            "id": pk,
            "path": path,
            "deleted_by": deleted_by,
            "deleted_at": deleted_at,
            "moved_at": moved_at,
            "expires_at": deleted_at + self.TRASH_DAYS * 86400,
            "latest_version": latest,
            "objects": objects,
            "bytes": size,
        }

    async def delete(self, org: Org, path: str, user: User) -> dict[str, Any]:
        """Move the dataset at ``path`` to the trash (D93): its row first, then the copy
        (``_versions/`` last), then the original's deletion (``_versions/`` first)."""
        bucket = await self.bucket(org)
        name = bucket.upstream_name
        check_path(path)
        async with self._locked(name, [path]) as conn:
            found = await self.latest(name, path)
            if found is None:
                raise NotFound(f"dataset {path} not found")
            objects = await self._call(
                self._upstream().list_all(name, f"{path}/", self.MOVE_OBJECTS)
            )
            command = f"aws s3 rm --recursive s3://{self.store.s3.DATASETS}/{path}/ --profile flock"
            self._check_size(objects, "delete", command)
            trash = await self._call(self._ensure_trash())
            latest = newest(found) if found else None
            if latest is None:
                latest = (await self.resolve(name, path, None)).number
            # Committed at once, so the sweep sees the move even if this request dies; the row
            # stays locked meanwhile, so the sweep leaves a running move alone.
            pk = await self.store.db.fetchval(
                "INSERT INTO dataset_trash (org_pk, path, deleted_by, deleted_at, latest_version, "
                "objects, bytes) VALUES ($1, $2, $3, now(), $4, $5, $6) RETURNING pk",
                org.pk,
                path,
                user.pk,
                latest,
                len(objects),
                sum(o.size for o in objects),
            )
            await conn.execute("SELECT 1 FROM dataset_trash WHERE pk = $1 FOR UPDATE", pk)
            prefix = self._trash_prefix(org.pk, pk)
            await self._copy_all(objects, name, f"{path}/", trash, prefix)
            await self._delete_all(name, [o.key for o in objects], f"{path}/")
            record = await conn.fetchrow(
                f"UPDATE dataset_trash SET moved_at = now() WHERE pk = $1 "
                f"RETURNING {self.TRASH_COLUMNS}",
                pk,
            )
        self._forget(name, path)
        return self._entry(record)

    async def trash_entries(self, org: Org) -> list[dict[str, Any]]:
        """The org's deleted datasets, newest first."""
        records = await self.store.db.fetch(
            f"SELECT {self.TRASH_COLUMNS} FROM dataset_trash WHERE org_pk = $1 "
            "ORDER BY deleted_at DESC, pk DESC",
            org.pk,
        )
        return [self._entry(r) for r in records]

    async def _claim(self, conn: asyncpg.Connection, org: Org, pk: int) -> Any:
        """The trash row ``pk`` of ``org``, locked for this transaction."""
        try:
            record = await conn.fetchrow(
                f"SELECT {self.TRASH_COLUMNS} FROM dataset_trash WHERE pk = $1 AND org_pk = $2 "
                "FOR UPDATE NOWAIT",
                pk,
                org.pk,
            )
        except Exception as e:  # asyncpg's LockNotAvailableError
            if type(e).__name__ == "LockNotAvailableError":
                raise Conflict("This dataset is still being moved. Try again later.") from None
            raise
        if record is None:
            raise NotFound(f"deleted dataset {pk} not found")
        if record[4] is None:
            raise Conflict("This dataset is still being moved. Try again later.")
        return record

    async def restore(self, org: Org, pk: int, to: str | None) -> dict[str, Any]:
        """Copy a deleted dataset back to its path, or to ``to`` (``_versions/`` last), then
        remove it from the trash. ``Taken`` if something now exists at the path."""
        bucket = await self.bucket(org)
        name = bucket.upstream_name
        if to is not None:
            check_new_path(to)
        trash = await self._call(self._ensure_trash())
        async with self.store.db.pool.acquire() as conn, conn.transaction():
            record = await self._claim(conn, org, pk)
            target = to if to is not None else record[1]
            async with self._locked(name, [target]):
                await self._check_target(name, target)
                prefix = self._trash_prefix(org.pk, pk)
                objects = await self._call(self._upstream().list_all(trash, prefix))
                await self._copy_all(objects, trash, prefix, name, f"{target}/")
                await self._delete_all(trash, [o.key for o in objects], prefix)
            await conn.execute("DELETE FROM dataset_trash WHERE pk = $1", pk)
        self._forget(name, target)
        return {"path": target}

    async def delete_now(self, org: Org, pk: int) -> None:
        """Remove a deleted dataset from the trash for good."""
        trash = await self._call(self._ensure_trash())
        async with self.store.db.pool.acquire() as conn, conn.transaction():
            await self._claim(conn, org, pk)
            await self._remove_prefix(trash, self._trash_prefix(org.pk, pk))
            await conn.execute("DELETE FROM dataset_trash WHERE pk = $1", pk)

    async def delete_org(self, conn: asyncpg.Connection, org: Org) -> None:
        """In the org's deletion's transaction: forget its trash entries. Call ``purge_org``
        after it commits, to remove their objects."""
        await conn.execute("DELETE FROM dataset_trash WHERE org_pk = $1", org.pk)

    def purge_org(self, org_pk: int) -> None:
        """Remove a deleted org's trash, in the background (the sweep's orphan pass is the
        fallback if this is cut short)."""
        if self.store.s3.upstream is None:
            return

        async def purge() -> None:
            try:
                await self._remove_prefix(await self._ensure_trash(), f"{org_pk:05d}/")
            except Exception:
                log.exception("could not empty the trash of org %d", org_pk)

        self._spawn(purge())

    async def sweep(self) -> dict[str, int]:
        """Remove entries deleted more than 14 days ago, settle moves left unfinished, and
        remove trash prefixes with no row (D93). Rows are claimed with ``SKIP LOCKED``, so any
        number of replicas can sweep at once."""
        trash = await self._ensure_trash()
        done = {"expired": 0, "settled": 0, "orphans": 0}
        while True:
            async with self.store.db.pool.acquire() as conn, conn.transaction():
                record = await conn.fetchrow(
                    "SELECT pk, org_pk, path, extract(epoch FROM deleted_at)::float8, "
                    "moved_at IS NOT NULL FROM dataset_trash "
                    "WHERE (moved_at IS NOT NULL AND deleted_at < now() - make_interval(days => $1)) "
                    "OR (moved_at IS NULL AND deleted_at < now() - make_interval(secs => $2)) "
                    "ORDER BY pk LIMIT 1 FOR UPDATE SKIP LOCKED",
                    self.TRASH_DAYS,
                    self.STUCK_AFTER,
                )
                if record is None:
                    break
                pk, org_pk, path, deleted_at, moved = record
                prefix = self._trash_prefix(org_pk, pk)
                if moved:
                    await self._remove_prefix(trash, prefix)
                    await conn.execute("DELETE FROM dataset_trash WHERE pk = $1", pk)
                    done["expired"] += 1
                    continue
                await self._settle(conn, pk, org_pk, path, deleted_at, trash, prefix)
                done["settled"] += 1
        done["orphans"] = await self._sweep_orphans(trash)
        return done

    async def _settle(
        self,
        conn: asyncpg.Connection,
        pk: int,
        org_pk: int,
        path: str,
        deleted_at: float,
        trash: str,
        prefix: str,
    ) -> None:
        """A move to the trash whose request died: if the dataset is still at its path, undo
        it (the copy and the row go); otherwise finish it."""
        org = await self.store.org_by_pk(org_pk)
        bucket = await self.store.s3.managed_bucket(org) if org is not None else None
        if bucket is None:
            await self._remove_prefix(trash, prefix)
            await conn.execute("DELETE FROM dataset_trash WHERE pk = $1", pk)
            return
        name = bucket.upstream_name
        if await self.latest(name, path) is not None:
            await self._remove_prefix(trash, prefix)
            await conn.execute("DELETE FROM dataset_trash WHERE pk = $1", pk)
            log.info("undid the unfinished delete of %s (trash %d)", path, pk)
            return
        # Only what was there when it was deleted: anything written since is someone else's.
        await self._remove_prefix(name, f"{path}/", before=deleted_at)
        await conn.execute("UPDATE dataset_trash SET moved_at = now() WHERE pk = $1", pk)
        self._forget(name, path)
        log.info("finished the delete of %s (trash %d)", path, pk)

    async def _sweep_orphans(self, trash: str) -> int:
        """Remove trash prefixes with no row whose objects are all older than a day."""
        upstream = self._upstream()
        rows = {pk for (pk,) in await self.store.db.fetch("SELECT pk FROM dataset_trash")}
        removed = 0
        orgs = await self._call(upstream.list_objects(trash, "", "/"))
        cutoff = time.time() - self.ORPHAN_AFTER
        for org_prefix in orgs.prefixes:
            token = None
            while True:
                entries = await self._call(upstream.list_objects(trash, org_prefix, "/", token))
                for entry in entries.prefixes:
                    pk = entry.removeprefix(org_prefix).removesuffix("/")
                    if pk.isdigit() and int(pk) in rows:
                        continue
                    objects = await self._call(upstream.list_all(trash, entry))
                    if objects and all(o.last_modified <= cutoff for o in objects):
                        await self._delete_all(trash, [o.key for o in objects], entry)
                        removed += 1
                token = entries.cursor
                if token is None:
                    break
        return removed

    async def _sweep_loop(self) -> None:
        while True:
            await asyncio.sleep(self.SWEEP_INTERVAL)
            try:
                done = await self.sweep()
                if any(done.values()):
                    log.info("swept the dataset trash: %s", done)
            except Exception:
                log.exception("sweeping the dataset trash failed")
