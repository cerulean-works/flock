"""Models (014, D97-D109): numbered, immutable versions in each org's ``flock-models`` bucket.

Postgres is the registry: it numbers versions, tags them and links them to runs. The bytes are
in S3, one folder per upload attempt (``{name}/u/{ulid}/``, D99), written by the client through
the proxy while the upload is open and checked here when it's committed (D100). Deleting moves a
version's folder to the trash bucket that deleted datasets wait in (D93, D109), and a sweep
expires idle uploads, removes stray folders, empties the trash and applies retention.
"""

import asyncio
import contextlib
import dataclasses
import hashlib
import logging
import re
import secrets
import time
from collections.abc import Awaitable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

import httpx
import orjson

from app.api.src.core.datasets import TAG
from app.api.src.core.errors import BadGateway, Conflict, Forbidden, Invalid, NotFound
from app.api.src.s3_proxy.upstream import DELETE_BATCH, ListedObject, Upstream, UpstreamError

if TYPE_CHECKING:
    import asyncpg

    from app.api.src.core.accounts import User
    from app.api.src.core.s3 import Bucket
    from app.api.src.core.store import Org, Run, Store

log = logging.getLogger(__name__)

MIB = 1024 * 1024
GIB = 1024 * MIB
# Model names (D98): one segment of lowercase letters, digits, `.`, `-` and `_`.
NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,99}")
CHECKPOINT = re.compile(r"checkpoint-\d+")
# The model an eval run's config names (D106), as eval's provenance writes it: a number, never a
# tag, which a later move would make name another version.
EVAL_MODEL = re.compile(r"flock:([^/@]+)/([^/@]+)@([1-9][0-9]{0,8})")
SHA256 = re.compile(r"[0-9a-f]{64}")
# A ULID's alphabet (Crockford's base32), and the folder of an upload under flock-models.
CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
UPLOAD_FOLDER = re.compile(r"([^/]+)/u/([0-9A-HJKMNP-TV-Z]{26})/")
INDEX = "model.safetensors.index.json"
SINGLE = "model.safetensors"
REQUIRED = ("config.json", "flock.json")
MANIFEST = "manifest.json"
STATES = ("open", "committed", "aborted", "expired")


def new_ulid() -> str:
    """A ULID: 48 bits of milliseconds, then 80 random bits, in 26 characters that sort by
    time."""
    value = (int(time.time() * 1000) << 80) | secrets.randbits(80)
    return "".join(CROCKFORD[(value >> shift) & 31] for shift in range(125, -1, -5))


def check_name(name: str) -> None:
    if not NAME.fullmatch(name):
        raise Invalid(
            "Model names are up to 100 lowercase letters, digits, dots, hyphens and underscores, "
            "starting with a letter or digit."
        )


def is_checkpoint(tag: str) -> bool:
    return CHECKPOINT.fullmatch(tag) is not None


def check_tag(tag: str) -> None:
    """D96's rules for tag names, and ``primary`` taken by the primary version (D107)."""
    if not TAG.fullmatch(tag):
        raise Invalid(
            "Tag names are up to 100 letters, digits, dots, hyphens and underscores, starting "
            "with a letter or digit."
        )
    if tag.isdigit():
        raise Invalid("Tag names can't be only digits, since those are version numbers.")
    if tag.lower() == "primary":
        raise Invalid("primary names the primary version, so it can't be a tag.")


def check_file_path(path: str) -> None:
    """Raises ``Invalid`` unless ``path`` may name a file of a version (D99)."""
    if not path or len(path.encode()) > 800:
        raise Invalid("File paths are 1 to 800 bytes.")
    if any(ord(c) < 32 or ord(c) == 127 for c in path) or "\\" in path:
        raise Invalid(f"{path} holds a control character or a backslash.")
    if any(segment in ("", ".", "..") for segment in path.split("/")):
        raise Invalid(f"{path} isn't a relative path of folders and a file name.")
    if path == MANIFEST:
        raise Invalid(f"{MANIFEST} is written by Flock once the upload is committed.")


def digest(files: Iterable[dict[str, Any]]) -> str:
    """The version's digest (D98): the sha256 of ``{path}\\t{size}\\t{sha256}\\n`` per file,
    sorted by path."""
    lines = "".join(
        f"{f['path']}\t{f['size']}\t{f['sha256']}\n" for f in sorted(files, key=lambda f: f["path"])
    )
    return hashlib.sha256(lines.encode()).hexdigest()


class Incomplete(Conflict):
    """The upload's objects aren't what was declared (HTTP 409): the paths, by what's wrong."""

    def __init__(self, message: str, **paths: list[str]) -> None:
        super().__init__(message)
        self.paths = {k: v for k, v in paths.items() if v}


class TagTaken(Conflict):
    """The tag is on another version (HTTP 409); ``version`` is its number."""

    def __init__(self, tag: str, version: int, checkpoint: bool = False) -> None:
        why = " Checkpoint tags don't move." if checkpoint else ""
        super().__init__(f"{tag} is on version {version}.{why}")
        self.version = version


class Taken(Conflict):
    """A live model already has the name a deleted one would be restored to (HTTP 409)."""


@dataclass(frozen=True)
class Model:
    pk: int
    org: int
    name: str
    created_by: int | None
    created_at: float
    last_number: int
    primary_version: int | None
    primary_set_by: int | None
    primary_set_at: float | None
    deleted_at: float | None
    deleted_by: int | None


@dataclass(frozen=True)
class Version:
    pk: int
    number: int
    digest: str
    total_bytes: int
    metadata: str
    step: int | None
    created_by: int | None
    created_at: float
    ulid: str
    run: str | None  # entity/project/id of the run that made it
    tags: list[str]
    eval_runs: int


@dataclass(frozen=True)
class Upload:
    pk: int
    ulid: str
    model: int
    user: int | None
    state: str
    files: str
    metadata: str
    tags: list[str]
    make_primary: bool
    run: str | None
    step: int | None
    dedupe_key: str | None
    created_at: float
    last_activity_at: float


MODEL_COLUMNS = (
    "m.pk, m.org_pk, m.name, m.created_by, extract(epoch FROM m.created_at)::float8, "
    "m.last_number, m.primary_version_pk, m.primary_set_by, "
    "extract(epoch FROM m.primary_set_at)::float8, extract(epoch FROM m.deleted_at)::float8, "
    "m.deleted_by"
)
RUN_PATH = (
    "(SELECT o.name || '/' || p.name || '/' || r.name FROM runs r "
    "JOIN projects p ON p.pk = r.project_pk JOIN orgs o ON o.pk = p.org_pk "
    "WHERE r.pk = {column} AND r.deleted_at IS NULL)"
)
VERSION_COLUMNS = (
    "v.pk, v.number, v.digest, v.total_bytes, v.metadata::text, v.step, v.created_by, "
    "extract(epoch FROM v.created_at)::float8, u.ulid, "
    f"{RUN_PATH.format(column='v.run_pk')}, "
    "coalesce((SELECT array_agg(t.name ORDER BY t.name) FROM model_tags t "
    "WHERE t.version_pk = v.pk), '{}'), "
    "(SELECT count(*) FROM model_version_runs e WHERE e.version_pk = v.pk)::int"
)
VERSION_FROM = "model_versions v JOIN model_uploads u ON u.pk = v.upload_pk"
UPLOAD_COLUMNS = (
    "u.pk, u.ulid, u.model_pk, u.user_pk, u.state, u.files::text, u.metadata::text, u.tags, "
    f"u.make_primary, {RUN_PATH.format(column='u.run_pk')}, u.step, u.dedupe_key, "
    "extract(epoch FROM u.created_at)::float8, extract(epoch FROM u.last_activity_at)::float8"
)


class Models:
    """The model registry's reads and writes. Every route needs S3 on (``S3.require``)."""

    PART_SIZE: ClassVar[int] = 64 * MIB  # what clients upload in (D101)
    MAX_PARTS: ClassVar[int] = 10_000  # S3's limit
    MAX_FILES: ClassVar[int] = 10_000
    METADATA_BYTES: ClassVar[int] = 256 * 1024
    HEADER_LIMIT: ClassVar[int] = 100_000_000  # safetensors' own limit on a header
    INDEX_LIMIT: ClassVar[int] = 64 * MIB
    CHECK_AT_ONCE: ClassVar[int] = 16
    COPY_AT_ONCE: ClassVar[int] = 16
    COPY_LIMIT: ClassVar[int] = 5 * GIB  # larger objects are copied in parts
    COPY_PART: ClassVar[int] = 512 * MIB
    VERSION_PAGE: ClassVar[int] = 100
    EXPIRE_DAYS: ClassVar[int] = 7  # an upload idle this long expires (D102)
    STRAY_AFTER: ClassVar[float] = 86400.0  # folders with no upload row, removed after this
    TRASH_DAYS: ClassVar[int] = 14
    SWEEP_INTERVAL: ClassVar[float] = 600.0

    def __init__(self, store: Store) -> None:
        self.store = store
        self._tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------------ lifecycle

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

    def _spawn(self, coro: Awaitable[Any]) -> None:
        async def run() -> None:
            await coro

        task = asyncio.create_task(run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _startup(self) -> None:
        """Write the ``manifest.json`` of every version whose commit couldn't (D100). The
        buckets themselves are ensured by the Datasets' startup pass, for both kinds."""
        try:
            if written := await self.write_manifests():
                log.info("wrote %d missing model manifests", written)
        except Exception:
            log.exception("writing missing model manifests failed")

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
        """The org's flock-models; with ``ensure``, created if it has none (D97)."""
        s3 = self.store.s3
        s3.require()
        found = await s3.managed_bucket(org, "models")
        if found is None and ensure:
            try:
                found = await s3.ensure_managed(org, "models")
            except (Conflict, BadGateway, Invalid) as e:
                log.warning("could not create %s for %s: %s", s3.MODELS, org.name, e)
                raise BadGateway(f"The {s3.MODELS} bucket couldn't be created. {e}") from None
        if found is None:
            raise NotFound(f"{s3.MODELS} doesn't exist yet")
        return found

    async def _bucket_of(self, org_pk: int) -> str | None:
        """The upstream name of the org's flock-models, if the org and its bucket are live."""
        return await self.store.db.fetchval(
            "SELECT upstream_name FROM s3_buckets b JOIN orgs o ON o.pk = b.org_pk "
            "WHERE b.org_pk = $1 AND b.managed = 'models' AND b.deleted_at IS NULL "
            "AND o.deleted_at IS NULL",
            org_pk,
        )

    async def _ensure_trash(self) -> str:
        return await self.store.datasets._ensure_trash()

    @staticmethod
    def _folder(name: str, ulid: str) -> str:
        return f"{name}/u/{ulid}/"

    @staticmethod
    def _trash_prefix(org_pk: int, version_pk: int) -> str:
        return f"{org_pk:05d}/models/{version_pk}/"

    # ------------------------------------------------------------------ lookups

    @staticmethod
    async def _model(conn: asyncpg.Connection, org: Org, name: str, lock: bool = False) -> Model:
        check_name(name)
        record = await conn.fetchrow(
            f"SELECT {MODEL_COLUMNS} FROM models m "
            "WHERE m.org_pk = $1 AND m.name = $2 AND m.deleted_at IS NULL"
            + (" FOR UPDATE" if lock else ""),
            org.pk,
            name,
        )
        if record is None:
            raise NotFound(f"model {org.name}/{name} not found")
        return Model(*record)

    @staticmethod
    async def _version(conn: asyncpg.Connection, pk: int) -> Version:
        record = await conn.fetchrow(
            f"SELECT {VERSION_COLUMNS} FROM {VERSION_FROM} WHERE v.pk = $1", pk
        )
        assert record is not None
        return Version(*record)

    @staticmethod
    async def _version_pk(conn: asyncpg.Connection, org: Org, model: Model, ref: str | None) -> int:
        """The live version ``ref`` names: a number, a tag, or ``primary`` (also None or ``""``,
        D103)."""
        what = f"{org.name}/{model.name}"
        if ref is None or ref == "" or ref == "primary":
            if model.primary_version is None:
                raise NotFound(f"{what} has no versions")
            return model.primary_version
        if ref.isdigit():
            pk = await conn.fetchval(
                "SELECT pk FROM model_versions "
                "WHERE model_pk = $1 AND number = $2 AND deleted_at IS NULL",
                model.pk,
                int(ref) if len(ref) < 10 else -1,
            )
            if pk is None:
                raise NotFound(f"version {ref} of {what} not found")
            return pk
        pk = await conn.fetchval(
            "SELECT version_pk FROM model_tags WHERE model_pk = $1 AND name = $2", model.pk, ref
        )
        if pk is None:
            raise NotFound(f"tag {ref} of {what} not found")
        return pk

    @staticmethod
    def _info(org: Org, model: Model, version: Version) -> dict[str, Any]:
        return {
            "model": f"{org.name}/{model.name}",
            "name": model.name,
            "number": version.number,
            "digest": version.digest,
            "total_bytes": version.total_bytes,
            "metadata": orjson.loads(version.metadata),
            "tags": version.tags,
            "primary": version.pk == model.primary_version,
            "step": version.step,
            "run": version.run,
            "created_by": version.created_by,
            "created_at": version.created_at,
            "eval_runs": version.eval_runs,
            "bucket": "flock-models",
            "prefix": Models._folder(model.name, version.ulid),
        }

    async def _detail(
        self, conn: asyncpg.Connection, org: Org, model: Model, pk: int
    ) -> dict[str, Any]:
        """A version with its files and the runs that scored it (D103, D106)."""
        info = self._info(org, model, await self._version(conn, pk))
        files = await conn.fetch(
            'SELECT path, size, sha256 FROM model_files WHERE version_pk = $1 ORDER BY path COLLATE "C"',
            pk,
        )
        runs = await conn.fetch(
            f"SELECT {RUN_PATH.format(column='e.run_pk')} FROM model_version_runs e "
            "WHERE e.version_pk = $1 AND e.kind = 'eval' ORDER BY e.run_pk",
            pk,
        )
        info["files"] = [{"path": p, "size": s, "sha256": h} for p, s, h in files]
        info["evals"] = [run for (run,) in runs if run is not None]
        return info

    async def _run_pk(self, org: Org, run: str | None) -> int | None:
        """The run ``entity/project/id`` names, in this org (D106); None for any other."""
        if not run:
            return None
        entity, _, rest = run.partition("/")
        project, _, name = rest.partition("/")
        if entity != org.name or not project or not name or "/" in name:
            return None
        found = await self.store.get_run(entity, project, name)
        return found.pk if found is not None else None

    # ------------------------------------------------------------------ reading (D103)

    async def list_models(self, org: Org) -> list[dict[str, Any]]:
        """The org's models by name: their latest and primary versions."""
        records = await self.store.db.fetch(
            "SELECT m.name, extract(epoch FROM m.created_at)::float8, m.created_by, "
            "(SELECT count(*) FROM model_versions c WHERE c.model_pk = m.pk "
            "AND c.deleted_at IS NULL)::int, l.number, extract(epoch FROM l.created_at)::float8, "
            "p.number, p.total_bytes FROM models m "
            "LEFT JOIN LATERAL (SELECT number, created_at FROM model_versions "
            "WHERE model_pk = m.pk AND deleted_at IS NULL ORDER BY number DESC LIMIT 1) l ON true "
            "LEFT JOIN model_versions p ON p.pk = m.primary_version_pk "
            "WHERE m.org_pk = $1 AND m.deleted_at IS NULL ORDER BY m.name",
            org.pk,
        )
        return [
            {
                "name": name,
                "model": f"{org.name}/{name}",
                "created_at": created_at,
                "created_by": created_by,
                "versions": versions,
                "latest": latest,
                "primary": primary,
                "size": size,
                "updated_at": updated_at if updated_at is not None else created_at,
            }
            for name, created_at, created_by, versions, latest, updated_at, primary, size in (
                records
            )
        ]

    async def model_info(self, org: Org, name: str) -> dict[str, Any]:
        """A model: its primary version and who set it (D107), its latest and its tags."""
        async with self.store.db.pool.acquire() as conn:
            model = await self._model(conn, org, name)
            primary = latest = None
            if model.primary_version is not None:
                primary = await conn.fetchval(
                    "SELECT number FROM model_versions WHERE pk = $1", model.primary_version
                )
            latest, count = await conn.fetchrow(
                "SELECT max(number), count(*)::int FROM model_versions "
                "WHERE model_pk = $1 AND deleted_at IS NULL",
                model.pk,
            )
            tags = await conn.fetch(
                "SELECT t.name, v.number, t.moved_by, extract(epoch FROM t.moved_at)::float8 "
                "FROM model_tags t JOIN model_versions v ON v.pk = t.version_pk "
                "WHERE t.model_pk = $1 ORDER BY t.name",
                model.pk,
            )
        return {
            "name": model.name,
            "model": f"{org.name}/{model.name}",
            "created_at": model.created_at,
            "created_by": model.created_by,
            "versions": count,
            "latest": latest,
            "primary": primary,
            "primary_set_by": model.primary_set_by,
            "primary_set_at": model.primary_set_at,
            "tags": [
                {"name": t, "version": n, "moved_by": by, "moved_at": at} for t, n, by, at in tags
            ],
        }

    async def versions(
        self, org: Org, name: str, before: int | None, checkpoints: bool = False
    ) -> dict[str, Any]:
        """Live versions, newest first, 100 a page below ``before``; with ``checkpoints``, only
        those with a ``checkpoint-{step}`` tag (D107)."""
        async with self.store.db.pool.acquire() as conn:
            model = await self._model(conn, org, name)
            checkpoint = (
                "AND EXISTS (SELECT 1 FROM model_tags t WHERE t.version_pk = v.pk "
                "AND t.name ~ '^checkpoint-[0-9]+$') "
                if checkpoints
                else ""
            )
            records = await conn.fetch(
                f"SELECT {VERSION_COLUMNS} FROM {VERSION_FROM} "
                "WHERE v.model_pk = $1 AND v.deleted_at IS NULL "
                f"AND ($2::int IS NULL OR v.number < $2) {checkpoint}"
                "ORDER BY v.number DESC LIMIT $3",
                model.pk,
                before,
                self.VERSION_PAGE + 1,
            )
        found = [Version(*r) for r in records]
        page = found[: self.VERSION_PAGE]
        return {
            "versions": [self._info(org, model, v) for v in page],
            "cursor": page[-1].number if len(found) > len(page) else None,
        }

    async def version(self, org: Org, name: str, ref: str | None) -> dict[str, Any]:
        """The version ``ref`` names (a number, a tag, or the primary when None), with its
        files: what a download needs (D103)."""
        async with self.store.db.pool.acquire() as conn:
            model = await self._model(conn, org, name)
            pk = await self._version_pk(conn, org, model, ref)
            return await self._detail(conn, org, model, pk)

    # ------------------------------------------------------------------ runs (D106)

    @staticmethod
    async def link_eval(
        conn: asyncpg.Connection, org: Org, run_pk: int, config: dict[str, Any]
    ) -> None:
        """Links the run to the version its config's ``model`` names as an eval run, when that's
        ``flock:{org}/{name}@{number}`` of a live version of a live model in the run's org.
        Anything else, a tag included, links nothing, and the run is stored as usual. A link
        stays when the config changes again."""
        value = config.get("model")
        if isinstance(value, dict):  # how the wandb SDK sends config: {"value": ..., "desc": ...}
            value = value.get("value")
        if not isinstance(value, str):
            return
        ref = EVAL_MODEL.fullmatch(value)
        if ref is None or ref[1] != org.name:
            return
        await conn.execute(
            "INSERT INTO model_version_runs (version_pk, run_pk, kind) "
            "SELECT v.pk, $4, 'eval' FROM model_versions v JOIN models m ON m.pk = v.model_pk "
            "WHERE m.org_pk = $1 AND m.name = $2 AND m.deleted_at IS NULL AND v.number = $3 "
            "AND v.deleted_at IS NULL ON CONFLICT DO NOTHING",
            org.pk,
            ref[2],
            int(ref[3]),
            run_pk,
        )

    async def run_versions(self, run: Run) -> dict[str, Any]:
        """The live versions ``run`` made (``made``, D108's Checkpoints section) and those it
        scored as an eval run (``scored``), each with the runs that scored it, by model name and
        number."""
        org = run.project.org
        width = len(dataclasses.fields(Model))
        found: dict[str, list[tuple[Model, Version]]] = {}
        async with self.store.db.pool.acquire() as conn:
            scored = (
                "v.pk IN (SELECT e.version_pk FROM model_version_runs e "
                "WHERE e.run_pk = $2 AND e.kind = 'eval')"
            )
            for key, which in (("made", "v.run_pk = $2"), ("scored", scored)):
                records = await conn.fetch(
                    f"SELECT {MODEL_COLUMNS}, {VERSION_COLUMNS} FROM {VERSION_FROM} "
                    "JOIN models m ON m.pk = v.model_pk "
                    "WHERE m.org_pk = $1 AND m.deleted_at IS NULL AND v.deleted_at IS NULL "
                    f"AND {which} ORDER BY m.name, v.number",
                    org.pk,
                    run.pk,
                )
                found[key] = [(Model(*r[:width]), Version(*r[width:])) for r in records]
            evals: dict[int, list[str]] = {v.pk: [] for pairs in found.values() for _, v in pairs}
            for pk, path in await conn.fetch(
                f"SELECT e.version_pk, {RUN_PATH.format(column='e.run_pk')} "
                "FROM model_version_runs e WHERE e.version_pk = ANY($1) AND e.kind = 'eval' "
                "ORDER BY e.run_pk",
                list(evals),
            ):
                if path is not None:  # a deleted run
                    evals[pk].append(path)
        return {
            key: [{**self._info(org, m, v), "evals": evals[v.pk]} for m, v in pairs]
            for key, pairs in found.items()
        }

    # ------------------------------------------------------------------ uploads (D99, D100)

    def _check_files(self, files: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not files:
            raise Invalid("An upload needs at least one file.")
        if len(files) > self.MAX_FILES:
            raise Invalid(f"An upload holds at most {self.MAX_FILES:,} files.")
        largest = self.PART_SIZE * self.MAX_PARTS
        seen: set[str] = set()
        for f in files:
            check_file_path(f["path"])
            if f["path"] in seen:
                raise Invalid(f"{f['path']} is declared twice.")
            seen.add(f["path"])
            if f["size"] > largest:
                raise Invalid(f"{f['path']} is larger than {largest // GIB} GiB.")
        missing = [path for path in REQUIRED if path not in seen]
        if SINGLE not in seen and INDEX not in seen:
            missing.append(f"{SINGLE} or {INDEX}")
        if missing:
            raise Invalid(f"A model needs {', '.join(missing)}.")
        return sorted(
            ({"path": f["path"], "size": f["size"]} for f in files), key=lambda f: f["path"]
        )

    async def open_upload(
        self,
        org: Org,
        name: str,
        user: User,
        files: list[dict[str, Any]],
        metadata: dict[str, Any],
        tags: list[str],
        primary: bool,
        run: str | None,
        step: int | None,
        dedupe_key: str | None,
    ) -> tuple[bool, dict[str, Any]]:
        """Open an upload of a new version, creating the model if it's new (D100). Returns
        ``(True, upload)``, or ``(False, version)`` when a live version has ``dedupe_key``."""
        check_name(name)
        declared = self._check_files(files)
        for tag in tags:
            check_tag(tag)
        if len(set(tags)) != len(tags):
            raise Invalid("A tag is given twice.")
        encoded = orjson.dumps(metadata)
        if len(encoded) > self.METADATA_BYTES:
            raise Invalid(f"metadata is at most {self.METADATA_BYTES // 1024} KiB of JSON.")
        await self.bucket(org, ensure=True)
        run_pk = await self._run_pk(org, run)
        async with self.store.db.pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO models (org_pk, name, created_by, created_at) "
                "VALUES ($1, $2, $3, now()) ON CONFLICT (org_pk, name) WHERE deleted_at IS NULL "
                "DO NOTHING",
                org.pk,
                name,
                user.pk,
            )
            model = await self._model(conn, org, name, lock=True)
            if dedupe_key is not None:
                pk = await conn.fetchval(
                    "SELECT pk FROM model_versions "
                    "WHERE model_pk = $1 AND dedupe_key = $2 AND deleted_at IS NULL",
                    model.pk,
                    dedupe_key,
                )
                if pk is not None:
                    return False, await self._detail(conn, org, model, pk)
            # A checkpoint tag that's taken would only fail at the commit, after the bytes.
            for tag in filter(is_checkpoint, tags):
                taken = await conn.fetchval(
                    "SELECT v.number FROM model_tags t JOIN model_versions v ON v.pk = t.version_pk "
                    "WHERE t.model_pk = $1 AND t.name = $2",
                    model.pk,
                    tag,
                )
                if taken is not None:
                    raise TagTaken(tag, taken, checkpoint=True)
            record = await conn.fetchrow(
                "INSERT INTO model_uploads AS u (ulid, model_pk, user_pk, state, files, metadata, "
                "tags, make_primary, run_pk, step, dedupe_key, created_at, last_activity_at) "
                "VALUES ($1, $2, $3, 'open', $4::jsonb, $5::jsonb, $6, $7, $8, $9, $10, now(), "
                f"now()) RETURNING {UPLOAD_COLUMNS}",
                new_ulid(),
                model.pk,
                user.pk,
                orjson.dumps(declared).decode(),
                encoded.decode(),
                tags,
                primary,
                run_pk,
                step,
                dedupe_key,
            )
        return True, self._upload_info(org, model.name, Upload(*record))

    def _upload_info(self, org: Org, name: str, upload: Upload) -> dict[str, Any]:
        return {
            "ulid": upload.ulid,
            "model": f"{org.name}/{name}",
            "state": upload.state,
            "user": upload.user,
            "bucket": "flock-models",
            "prefix": self._folder(name, upload.ulid),
            "part_size": self.PART_SIZE,
            "files": orjson.loads(upload.files),
            "metadata": orjson.loads(upload.metadata),
            "tags": upload.tags,
            "primary": upload.make_primary,
            "run": upload.run,
            "step": upload.step,
            "dedupe_key": upload.dedupe_key,
            "created_at": upload.created_at,
            "last_activity_at": upload.last_activity_at,
            "expires_at": (
                upload.last_activity_at + self.EXPIRE_DAYS * 86400
                if upload.state == "open"
                else None
            ),
        }

    async def _upload(
        self, conn: asyncpg.Connection, org: Org, name: str, ulid: str, lock: bool = False
    ) -> Upload:
        check_name(name)
        record = await conn.fetchrow(
            f"SELECT {UPLOAD_COLUMNS} FROM model_uploads u JOIN models m ON m.pk = u.model_pk "
            "WHERE u.ulid = $1 AND m.org_pk = $2 AND m.name = $3"
            + (" FOR UPDATE OF u" if lock else ""),
            ulid,
            org.pk,
            name,
        )
        if record is None:
            raise NotFound(f"upload {ulid} of {org.name}/{name} not found")
        return Upload(*record)

    async def get_upload(self, org: Org, name: str, ulid: str) -> dict[str, Any]:
        async with self.store.db.pool.acquire() as conn:
            return self._upload_info(org, name, await self._upload(conn, org, name, ulid))

    async def uploads(self, org: Org, name: str, user: User, state: str) -> list[dict[str, Any]]:
        """``user``'s uploads of the model in ``state``, oldest first."""
        check_name(name)
        if state not in STATES:
            raise Invalid(f"state is one of {', '.join(STATES)}")
        records = await self.store.db.fetch(
            f"SELECT {UPLOAD_COLUMNS} FROM model_uploads u JOIN models m ON m.pk = u.model_pk "
            "WHERE m.org_pk = $1 AND m.name = $2 AND m.deleted_at IS NULL AND u.user_pk = $3 "
            "AND u.state = $4 ORDER BY u.pk",
            org.pk,
            name,
            user.pk,
            state,
        )
        return [self._upload_info(org, name, Upload(*r)) for r in records]

    async def commit(
        self, org: Org, name: str, ulid: str, user: User, hashes: dict[str, str]
    ) -> dict[str, Any]:
        """Make an open upload a version (D100): check its objects, their safetensors headers
        and their ``x-amz-meta-sha256``, then number it. Committing again answers the same."""
        bucket = (await self.bucket(org)).upstream_name
        for path, sha in hashes.items():
            if not SHA256.fullmatch(sha):
                raise Invalid(f"the sha256 of {path} isn't 64 lowercase hex digits")
        aborted: str | None = None
        gone = False  # the model was deleted while the upload was open
        answer: tuple[int, dict[str, Any]] | None = None
        async with self.store.db.pool.acquire() as conn, conn.transaction():
            # Held until the version is in: commits of one upload wait for each other, and the
            # sweep doesn't expire it meanwhile.
            upload = await self._upload(conn, org, name, ulid, lock=True)
            if upload.state != "open":
                return await self._answered(conn, org, upload)
            if upload.user != user.pk:
                raise Forbidden("Only who opened an upload can commit it.")
            declared = orjson.loads(upload.files)
            sizes = {f["path"]: f["size"] for f in declared}
            if hashes.keys() != sizes.keys():
                unknown = sorted(hashes.keys() - sizes.keys())
                left = sorted(sizes.keys() - hashes.keys())
                said = [
                    f"{', '.join(unknown)} weren't declared." if unknown else "",
                    f"{', '.join(left)} have no sha256." if left else "",
                ]
                raise Invalid(
                    f"The commit's files aren't the declared ones. {' '.join(said)}".strip()
                )
            prefix = self._folder(name, ulid)
            await self._check_objects(bucket, prefix, sizes)
            await self._check_weights(bucket, prefix, sizes)
            await self._check_hashes(bucket, prefix, hashes)

            model_record = await conn.fetchrow(
                f"SELECT {MODEL_COLUMNS} FROM models m WHERE m.pk = $1 FOR UPDATE", upload.model
            )
            assert model_record is not None
            model = Model(*model_record)
            if model.deleted_at is not None:
                await conn.execute(
                    "UPDATE model_uploads SET state = 'aborted' WHERE pk = $1", upload.pk
                )
                aborted, gone = prefix, True
            else:
                answer = await self._number(conn, org, model, upload, declared, hashes)
                if answer is None:  # a live version had its dedupe key meanwhile
                    aborted = prefix
        if aborted is not None:
            await self._clean(bucket, aborted)
        if answer is None:
            if gone:
                raise Conflict(f"{org.name}/{name} was deleted while this upload was open.")
            async with self.store.db.pool.acquire() as conn:
                return await self._answered(conn, org, await self._upload(conn, org, name, ulid))
        pk, info = answer
        await self._write_manifest(bucket, pk)
        return info

    async def _answered(self, conn: asyncpg.Connection, org: Org, upload: Upload) -> dict[str, Any]:
        """What committing an upload that isn't open answers: its version if it was committed,
        the version with its dedupe key if one is live, else 409."""
        record = await conn.fetchrow(
            f"SELECT {MODEL_COLUMNS} FROM models m WHERE m.pk = $1", upload.model
        )
        assert record is not None
        model = Model(*record)
        pk = None
        if upload.state == "committed":
            pk = await conn.fetchval(
                "SELECT pk FROM model_versions WHERE upload_pk = $1 AND deleted_at IS NULL",
                upload.pk,
            )
        elif upload.dedupe_key is not None:
            pk = await conn.fetchval(
                "SELECT pk FROM model_versions "
                "WHERE model_pk = $1 AND dedupe_key = $2 AND deleted_at IS NULL",
                upload.model,
                upload.dedupe_key,
            )
        if pk is None or model.deleted_at is not None:
            if upload.state == "committed":
                raise NotFound("This upload's version has been deleted.")
            raise Conflict(f"This upload is {upload.state}. Open a new one.")
        return await self._detail(conn, org, model, pk)

    async def _check_objects(self, bucket: str, prefix: str, sizes: dict[str, int]) -> None:
        """Step 1: the objects under ``prefix`` are exactly the declared files, at their sizes,
        with no multipart upload still open."""
        upstream = self._upstream()
        listed, unfinished = await self._call(
            asyncio.gather(upstream.list_all(bucket, prefix), upstream.list_uploads(bucket, prefix))
        )
        found = {o.key.removeprefix(prefix): o.size for o in listed}
        missing = sorted(sizes.keys() - found.keys())
        extra = sorted(found.keys() - sizes.keys())
        resized = sorted(p for p in sizes.keys() & found.keys() if sizes[p] != found[p])
        uploading = sorted({u.key.removeprefix(prefix) for u in unfinished})
        if missing or extra or resized or uploading:
            what = [
                f"{', '.join(paths)} {label}."
                for label, paths in (
                    ("are missing", missing),
                    ("weren't declared", extra),
                    ("don't have the declared size", resized),
                    ("are still uploading", uploading),
                )
                if paths
            ]
            raise Incomplete(
                f"The upload isn't complete. {' '.join(what)}",
                missing=missing,
                extra=extra,
                resized=resized,
                uploading=uploading,
            )

    async def _check_weights(self, bucket: str, prefix: str, sizes: dict[str, int]) -> None:
        """Step 2: each shard is safetensors, and the index names only tensors its shards
        hold (422 otherwise)."""
        upstream = self._upstream()
        shards: set[str] = {SINGLE} if SINGLE in sizes else set()
        weight_map: dict[str, str] = {}
        if INDEX in sizes:
            if sizes[INDEX] > self.INDEX_LIMIT:
                raise Invalid(f"{INDEX} is larger than {self.INDEX_LIMIT // MIB} MiB.")
            data = await self._call(upstream.get_range(bucket, prefix + INDEX, sizes[INDEX] or 1))
            try:
                index = orjson.loads(data or b"")
                weight_map = index["weight_map"]
                if not isinstance(weight_map, dict) or not all(
                    isinstance(k, str) and isinstance(v, str) for k, v in weight_map.items()
                ):
                    raise TypeError
            except orjson.JSONDecodeError, KeyError, TypeError:
                raise Invalid(f"{INDEX} has no weight_map of tensor names to files.") from None
            for shard in sorted(set(weight_map.values())):
                if shard not in sizes:
                    raise Invalid(f"{INDEX} names {shard}, which isn't in the upload.")
                shards.add(shard)
        limit = asyncio.Semaphore(self.CHECK_AT_ONCE)

        async def header(shard: str) -> set[str]:
            async with limit:
                return await self._header(bucket, prefix, shard, sizes[shard])

        ordered = sorted(shards)
        tensors = dict(zip(ordered, await asyncio.gather(*map(header, ordered)), strict=True))
        for tensor, shard in sorted(weight_map.items()):
            if tensor not in tensors[shard]:
                raise Invalid(f"{INDEX} puts {tensor} in {shard}, which doesn't hold it.")

    async def _header(self, bucket: str, prefix: str, path: str, size: int) -> set[str]:
        """The tensor names in a safetensors file's header, read with two ranged GETs."""
        upstream = self._upstream()
        refused = f"{path} isn't a safetensors file, since"
        start = await self._call(upstream.get_range(bucket, prefix + path, 8))
        if start is None or len(start) < 8:
            raise Invalid(f"{refused} it's shorter than 8 bytes.")
        length = int.from_bytes(start, "little")
        if not 2 <= length <= self.HEADER_LIMIT or 8 + length > size:
            raise Invalid(f"{refused} its header length is {length:,} bytes.")
        raw = await self._call(upstream.get_range(bucket, prefix + path, length, 8))
        try:
            header = orjson.loads(raw or b"")
            if not isinstance(header, dict):
                raise TypeError
        except orjson.JSONDecodeError, TypeError:
            raise Invalid(f"{refused} its header isn't a JSON object.") from None
        data = size - 8 - length
        names = set()
        for tensor, entry in header.items():
            if tensor == "__metadata__":
                continue
            try:
                begin, end = entry["data_offsets"]
                if not (
                    isinstance(entry["dtype"], str)
                    and isinstance(entry["shape"], list)
                    and 0 <= begin <= end <= data
                ):
                    raise ValueError
            except KeyError, TypeError, ValueError:
                raise Invalid(f"{refused} tensor {tensor} is malformed.") from None
            names.add(tensor)
        return names

    async def _check_hashes(self, bucket: str, prefix: str, hashes: dict[str, str]) -> None:
        """Step 3: each object's ``x-amz-meta-sha256`` is the sha256 committed (409)."""
        upstream = self._upstream()
        limit = asyncio.Semaphore(self.CHECK_AT_ONCE)

        async def stored(path: str) -> str | None:
            async with limit:
                response = await self._call(upstream.head(bucket, prefix + path))
            return response.headers.get("x-amz-meta-sha256") if response is not None else None

        paths = sorted(hashes)
        found = await asyncio.gather(*map(stored, paths))
        mismatched = [p for p, sha in zip(paths, found, strict=True) if sha != hashes[p]]
        if mismatched:
            raise Incomplete(
                "The sha256 committed doesn't match the object's x-amz-meta-sha256 for "
                f"{', '.join(mismatched)}.",
                mismatched=mismatched,
            )

    async def _number(
        self,
        conn: asyncpg.Connection,
        org: Org,
        model: Model,
        upload: Upload,
        declared: list[dict[str, Any]],
        hashes: dict[str, str],
    ) -> tuple[int, dict[str, Any]] | None:
        """Step 4, with the model locked: the version, its files and tags, the primary, and the
        upload committed. None (the upload aborted) if a live version has its dedupe key."""
        if upload.dedupe_key is not None:
            taken = await conn.fetchval(
                "SELECT pk FROM model_versions "
                "WHERE model_pk = $1 AND dedupe_key = $2 AND deleted_at IS NULL",
                model.pk,
                upload.dedupe_key,
            )
            if taken is not None:
                await conn.execute(
                    "UPDATE model_uploads SET state = 'aborted' WHERE pk = $1", upload.pk
                )
                return None
        moving = []
        for tag in upload.tags:
            current = await conn.fetchval(
                "SELECT v.number FROM model_tags t JOIN model_versions v ON v.pk = t.version_pk "
                "WHERE t.model_pk = $1 AND t.name = $2",
                model.pk,
                tag,
            )
            if current is not None and is_checkpoint(tag):
                raise TagTaken(tag, current, checkpoint=True)
            moving.append(tag)
        files = [{**f, "sha256": hashes[f["path"]]} for f in declared]
        number = model.last_number + 1
        await conn.execute("UPDATE models SET last_number = $2 WHERE pk = $1", model.pk, number)
        pk = await conn.fetchval(
            "INSERT INTO model_versions (model_pk, number, upload_pk, digest, total_bytes, "
            "metadata, run_pk, step, dedupe_key, created_by, created_at) "
            "SELECT $1, $2, pk, $3, $4, metadata, run_pk, step, dedupe_key, user_pk, now() "
            "FROM model_uploads WHERE pk = $5 RETURNING pk",
            model.pk,
            number,
            digest(files),
            sum(int(f["size"]) for f in files),
            upload.pk,
        )
        await conn.executemany(
            "INSERT INTO model_files (version_pk, path, size, sha256) VALUES ($1, $2, $3, $4)",
            [(pk, f["path"], f["size"], f["sha256"]) for f in files],
        )
        for tag in moving:
            await conn.execute(
                "INSERT INTO model_tags (model_pk, name, version_pk, moved_by, moved_at) "
                "VALUES ($1, $2, $3, $4, now()) ON CONFLICT (model_pk, name) DO UPDATE "
                "SET version_pk = $3, moved_by = $4, moved_at = now()",
                model.pk,
                tag,
                pk,
                upload.user,
            )
        if upload.make_primary or model.primary_version is None:
            await conn.execute(
                "UPDATE models SET primary_version_pk = $2, primary_set_by = $3, "
                "primary_set_at = now() WHERE pk = $1",
                model.pk,
                pk,
                upload.user,
            )
        await conn.execute(
            "UPDATE model_uploads SET state = 'committed', last_activity_at = now() WHERE pk = $1",
            upload.pk,
        )
        record = await conn.fetchrow(
            f"SELECT {MODEL_COLUMNS} FROM models m WHERE m.pk = $1", model.pk
        )
        assert record is not None
        return pk, await self._detail(conn, org, Model(*record), pk)

    async def _write_manifest(self, bucket: str, pk: int) -> bool:
        """Step 5: ``manifest.json`` in the version's folder. A failure is logged, and the
        startup pass (or the sweep) writes it later: Postgres stays the record."""
        try:
            async with self.store.db.pool.acquire() as conn:
                record = await conn.fetchrow(
                    "SELECT o.name, m.name, v.number, v.digest, v.metadata::text, u.ulid "
                    "FROM model_versions v JOIN models m ON m.pk = v.model_pk "
                    "JOIN orgs o ON o.pk = m.org_pk JOIN model_uploads u ON u.pk = v.upload_pk "
                    "WHERE v.pk = $1",
                    pk,
                )
                files = await conn.fetch(
                    "SELECT path, size, sha256 FROM model_files WHERE version_pk = $1 "
                    'ORDER BY path COLLATE "C"',
                    pk,
                )
            assert record is not None
            org, name, number, version_digest, metadata, ulid = record
            manifest = {
                "format": 1,
                "model": f"{org}/{name}",
                "number": number,
                "digest": version_digest,
                "files": [{"path": p, "size": s, "sha256": h} for p, s, h in files],
                "metadata": orjson.loads(metadata),
            }
            await self._upstream().put_object(
                bucket,
                self._folder(name, ulid) + MANIFEST,
                orjson.dumps(manifest, option=orjson.OPT_INDENT_2),
                "application/json",
            )
            await self.store.db.execute(
                "UPDATE model_versions SET manifest_at = now() WHERE pk = $1", pk
            )
            return True
        except Exception:
            log.exception("could not write the manifest of model version %d", pk)
            return False

    async def write_manifests(self) -> int:
        """Write every live version's missing ``manifest.json``; returns how many were."""
        records = await self.store.db.fetch(
            "SELECT v.pk, m.org_pk FROM model_versions v JOIN models m ON m.pk = v.model_pk "
            "WHERE v.manifest_at IS NULL AND v.deleted_at IS NULL ORDER BY v.pk"
        )
        written = 0
        for pk, org_pk in records:
            bucket = await self._bucket_of(org_pk)
            if bucket is not None and await self._write_manifest(bucket, pk):
                written += 1
        return written

    async def abort(self, org: Org, name: str, ulid: str, user: User) -> None:
        """Abort an open upload (its opener, or an admin), removing what landed (D102)."""
        bucket = (await self.bucket(org)).upstream_name
        async with self.store.db.pool.acquire() as conn, conn.transaction():
            upload = await self._upload(conn, org, name, ulid, lock=True)
            if upload.state == "committed":
                raise Conflict("This upload is committed: delete the version instead.")
            if upload.state != "open":
                return
            if upload.user != user.pk and self.store.accounts.role(user, org) != "admin":
                raise Forbidden("Only who opened an upload, or an admin, can abort it.")
            await conn.execute(
                "UPDATE model_uploads SET state = 'aborted' WHERE pk = $1", upload.pk
            )
        try:
            await self._clean(bucket, self._folder(name, ulid))
        except Exception:
            log.exception("could not clean up the aborted upload %s", ulid)

    async def _clean(self, bucket: str, prefix: str) -> int:
        """Abort the multipart uploads and delete the objects under ``prefix``; returns how
        many objects."""
        upstream = self._upstream()
        for upload in await self._call(upstream.list_uploads(bucket, prefix)):
            await self._call(upstream.abort_upload(bucket, upload.key, upload.upload_id))
        return await self._remove(bucket, prefix)

    async def _remove(self, bucket: str, prefix: str) -> int:
        upstream = self._upstream()
        keys = [o.key for o in await self._call(upstream.list_all(bucket, prefix))]
        for start in range(0, len(keys), DELETE_BATCH):
            await self._call(upstream.delete_keys(bucket, keys[start : start + DELETE_BATCH]))
        return len(keys)

    # ------------------------------------------------------------------ the primary and tags

    async def set_primary(self, org: Org, name: str, number: int, user: User) -> dict[str, Any]:
        """Make version ``number`` the primary (D107), recording who and when."""
        async with self.store.db.pool.acquire() as conn, conn.transaction():
            model = await self._model(conn, org, name, lock=True)
            pk = await self._version_pk(conn, org, model, str(number))
            previous = None
            if model.primary_version is not None and model.primary_version != pk:
                previous = await conn.fetchval(
                    "SELECT number FROM model_versions WHERE pk = $1", model.primary_version
                )
            if model.primary_version != pk:
                await conn.execute(
                    "UPDATE models SET primary_version_pk = $2, primary_set_by = $3, "
                    "primary_set_at = now() WHERE pk = $1",
                    model.pk,
                    pk,
                    user.pk,
                )
            model = await self._model(conn, org, name)
            return {**await self._detail(conn, org, model, pk), "previous": previous}

    async def set_tag(
        self, org: Org, name: str, tag: str, number: int, move: bool, user: User
    ) -> dict[str, Any]:
        """Put ``tag`` on version ``number``. A tag on another version moves only with ``move``,
        and a ``checkpoint-{step}`` tag never does (D107); otherwise ``TagTaken``."""
        check_tag(tag)
        async with self.store.db.pool.acquire() as conn, conn.transaction():
            model = await self._model(conn, org, name, lock=True)
            pk = await self._version_pk(conn, org, model, str(number))
            current = await conn.fetchrow(
                "SELECT t.version_pk, v.number FROM model_tags t "
                "JOIN model_versions v ON v.pk = t.version_pk WHERE t.model_pk = $1 AND t.name = $2",
                model.pk,
                tag,
            )
            if current is not None and current[0] != pk and (not move or is_checkpoint(tag)):
                raise TagTaken(tag, current[1], checkpoint=is_checkpoint(tag))
            if current is None or current[0] != pk:
                await conn.execute(
                    "INSERT INTO model_tags (model_pk, name, version_pk, moved_by, moved_at) "
                    "VALUES ($1, $2, $3, $4, now()) ON CONFLICT (model_pk, name) DO UPDATE "
                    "SET version_pk = $3, moved_by = $4, moved_at = now()",
                    model.pk,
                    tag,
                    pk,
                    user.pk,
                )
        return {"tag": tag, "version": number}

    async def delete_tag(self, org: Org, name: str, tag: str) -> None:
        async with self.store.db.pool.acquire() as conn, conn.transaction():
            model = await self._model(conn, org, name, lock=True)
            deleted = await conn.fetchval(
                "DELETE FROM model_tags WHERE model_pk = $1 AND name = $2 RETURNING name",
                model.pk,
                tag,
            )
        if deleted is None:
            raise NotFound(f"tag {tag} of {org.name}/{name} not found")

    # ------------------------------------------------------------------ deleting (D109)

    @staticmethod
    async def _refusal(conn: asyncpg.Connection, model: Model, pk: int, what: str) -> str | None:
        """Why the version ``pk`` can't be deleted, if it can't: it's the primary, or it has a
        tag other than ``checkpoint-{step}``."""
        if pk == model.primary_version:
            return f"{what} is the primary version. Make another version primary first."
        tags = [
            t
            for (t,) in await conn.fetch(
                "SELECT name FROM model_tags WHERE version_pk = $1 ORDER BY name", pk
            )
            if not is_checkpoint(t)
        ]
        if tags:
            return f"{what} is tagged {', '.join(tags)}. Move or remove the tags first."
        return None

    @staticmethod
    async def _mark_deleted(conn: asyncpg.Connection, pk: int, user: int | None) -> None:
        """Mark a version deleted: its checkpoint tags go with it, kept for a restore."""
        tags = await conn.fetch("DELETE FROM model_tags WHERE version_pk = $1 RETURNING name", pk)
        await conn.execute(
            "UPDATE model_versions SET deleted_at = now(), deleted_by = $2, "
            "deleted_tags = $3 WHERE pk = $1",
            pk,
            user,
            sorted(t for (t,) in tags),
        )

    async def delete_version(self, org: Org, name: str, number: int, user: User) -> dict[str, Any]:
        """Delete version ``number``: it's gone at once, and its files move to the trash in the
        background, where admins can restore it for 14 days (D109)."""
        async with self.store.db.pool.acquire() as conn, conn.transaction():
            model = await self._model(conn, org, name, lock=True)
            pk = await self._version_pk(conn, org, model, str(number))
            if (why := await self._refusal(conn, model, pk, f"Version {number}")) is not None:
                raise Conflict(why)
            await self._mark_deleted(conn, pk, user.pk)
        self._spawn(self._move_quietly(pk))
        entries = await self._version_entries(org, [pk])
        return entries[0]

    async def delete_model(self, org: Org, name: str, user: User) -> dict[str, Any]:
        """Delete a model with every version in it (D109). Its tags and primary stay, for a
        restore; its open uploads are aborted."""
        async with self.store.db.pool.acquire() as conn, conn.transaction():
            model = await self._model(conn, org, name, lock=True)
            await conn.execute(
                "UPDATE models SET deleted_at = now(), deleted_by = $2 WHERE pk = $1",
                model.pk,
                user.pk,
            )
            pks = await conn.fetch(
                "UPDATE model_versions SET deleted_at = now(), deleted_by = $2 "
                "WHERE model_pk = $1 AND deleted_at IS NULL RETURNING pk",
                model.pk,
                user.pk,
            )
        # After the model's deletion commits: a commit holding an upload's lock then sees the
        # model deleted, and aborts its upload itself.
        ulids = await self.store.db.fetch(
            "UPDATE model_uploads SET state = 'aborted' WHERE model_pk = $1 AND state = 'open' "
            "RETURNING ulid",
            model.pk,
        )
        bucket = await self._bucket_of(org.pk)

        async def clean() -> None:
            for (ulid,) in ulids:
                if bucket is not None:
                    with contextlib.suppress(Exception):
                        await self._clean(bucket, self._folder(name, ulid))
            for (pk,) in pks:
                await self._move_quietly(pk)

        self._spawn(clean())
        entries = await self._model_entries(org, [model.pk])
        return entries[0]

    async def _move_quietly(self, pk: int) -> None:
        try:
            await self._move_to_trash(pk)
        except Exception:
            log.exception("could not move model version %d to the trash; the sweep will", pk)

    async def _copy_all(
        self,
        objects: list[ListedObject],
        source: str,
        source_prefix: str,
        target: str,
        target_prefix: str,
    ) -> None:
        """Copy ``objects`` server side, 16 at a time, keeping their ``x-amz-meta-*``."""
        upstream = self._upstream()
        limit = asyncio.Semaphore(self.COPY_AT_ONCE)

        async def copy(o: ListedObject) -> None:
            key = target_prefix + o.key.removeprefix(source_prefix)
            async with limit:
                if o.size <= self.COPY_LIMIT:
                    await upstream.copy_object(source, o.key, target, key)
                    return
                response = await upstream.head(source, o.key)
                metadata = {
                    header.removeprefix("x-amz-meta-"): value
                    for header, value in (response.headers.items() if response else ())
                    if header.startswith("x-amz-meta-")
                }
                await upstream.copy_large(
                    source, o.key, o.size, target, key, self.COPY_PART, metadata
                )

        await self._call(asyncio.gather(*(copy(o) for o in objects)))

    async def _move_to_trash(self, pk: int) -> bool:
        """Move a deleted version's folder to ``{org pk:05}/models/{pk}/`` of the trash bucket,
        copying everything before deleting anything, so it can be run again after a crash.
        Returns whether this call did it (False if done, or another is doing it)."""
        trash = await self._ensure_trash()
        async with self.store.db.pool.acquire() as conn, conn.transaction():
            record = await conn.fetchrow(
                "SELECT m.org_pk, m.name, u.ulid FROM model_versions v "
                "JOIN models m ON m.pk = v.model_pk JOIN model_uploads u ON u.pk = v.upload_pk "
                "WHERE v.pk = $1 AND v.deleted_at IS NOT NULL AND v.trashed_at IS NULL "
                "FOR UPDATE OF v SKIP LOCKED",
                pk,
            )
            if record is None:
                return False
            org_pk, name, ulid = record
            bucket = await self._bucket_of(org_pk)
            if bucket is not None:  # else the org's deletion purges the bucket
                source = self._folder(name, ulid)
                objects = await self._call(self._upstream().list_all(bucket, source))
                await self._copy_all(objects, bucket, source, trash, self._trash_prefix(org_pk, pk))
                await self._remove(bucket, source)
            await conn.execute("UPDATE model_versions SET trashed_at = now() WHERE pk = $1", pk)
        return True

    # ------------------------------------------------------------------ the trash (D109)

    async def _version_entries(
        self, org: Org, pks: list[int] | None = None
    ) -> list[dict[str, Any]]:
        """Deleted versions of live models (or those of ``pks``), newest first."""
        records = await self.store.db.fetch(
            "SELECT v.pk, m.name, v.number, v.deleted_by, extract(epoch FROM v.deleted_at)::float8, "
            "v.trashed_at IS NOT NULL, v.total_bytes, v.deleted_tags, "
            "(SELECT count(*) FROM model_version_runs e WHERE e.version_pk = v.pk)::int "
            "FROM model_versions v JOIN models m ON m.pk = v.model_pk "
            "WHERE m.org_pk = $1 AND v.deleted_at IS NOT NULL "
            "AND ($2::bigint[] IS NULL OR v.pk = ANY($2)) "
            "AND ($2::bigint[] IS NOT NULL OR m.deleted_at IS NULL) "
            "ORDER BY v.deleted_at DESC, v.pk DESC",
            org.pk,
            pks,
        )
        return [
            {
                "kind": "version",
                "id": pk,
                "name": name,
                "model": f"{org.name}/{name}",
                "number": number,
                "deleted_by": deleted_by,
                "deleted_at": deleted_at,
                "expires_at": deleted_at + self.TRASH_DAYS * 86400,
                "moved": moved,
                "bytes": size,
                "tags": tags,
                "eval_runs": evals,
            }
            for pk, name, number, deleted_by, deleted_at, moved, size, tags, evals in records
        ]

    async def _model_entries(self, org: Org, pks: list[int] | None = None) -> list[dict[str, Any]]:
        """Deleted models (or those of ``pks``), newest first, with the versions deleted with
        them."""
        records = await self.store.db.fetch(
            "SELECT m.pk, m.name, m.deleted_by, extract(epoch FROM m.deleted_at)::float8, "
            "count(v.pk)::int, coalesce(sum(v.total_bytes), 0)::bigint, "
            "coalesce(bool_and(v.trashed_at IS NOT NULL), true) "
            "FROM models m LEFT JOIN model_versions v "
            "ON v.model_pk = m.pk AND v.deleted_at = m.deleted_at "
            "WHERE m.org_pk = $1 AND m.deleted_at IS NOT NULL "
            "AND ($2::bigint[] IS NULL OR m.pk = ANY($2)) "
            "GROUP BY m.pk ORDER BY m.deleted_at DESC, m.pk DESC",
            org.pk,
            pks,
        )
        return [
            {
                "kind": "model",
                "id": pk,
                "name": name,
                "model": f"{org.name}/{name}",
                "deleted_by": deleted_by,
                "deleted_at": deleted_at,
                "expires_at": deleted_at + self.TRASH_DAYS * 86400,
                "moved": moved,
                "versions": versions,
                "bytes": size,
            }
            for pk, name, deleted_by, deleted_at, versions, size, moved in records
        ]

    async def trash_entries(self, org: Org) -> list[dict[str, Any]]:
        """The org's deleted models and deleted versions of live models, newest first."""
        entries = await self._model_entries(org) + await self._version_entries(org)
        return sorted(entries, key=lambda e: (-e["deleted_at"], e["kind"], -e["id"]))

    @staticmethod
    async def _lock_nowait(conn: asyncpg.Connection, query: str, *args: Any) -> list[Any]:
        """``query``'s rows, locked ``NOWAIT``: 409 while a move to the trash holds them."""
        try:
            return await conn.fetch(query, *args)
        except Exception as e:  # asyncpg's LockNotAvailableError
            if type(e).__name__ == "LockNotAvailableError":
                raise Conflict("This is still being moved to the trash. Try again later.") from None
            raise

    async def _restore_files(
        self, org_pk: int, bucket: str, trash: str, name: str, versions: list[tuple[int, str]]
    ) -> None:
        """Copy deleted versions' files from the trash back to their folders."""
        upstream = self._upstream()
        for pk, ulid in versions:
            prefix = self._trash_prefix(org_pk, pk)
            objects = await self._call(upstream.list_all(trash, prefix))
            await self._copy_all(objects, trash, prefix, bucket, self._folder(name, ulid))

    async def _forget_trash(self, trash: str, org_pk: int, pks: Iterable[int]) -> None:
        """Remove restored versions' trash copies (the orphan pass is the fallback)."""
        for pk in pks:
            try:
                await self._remove(trash, self._trash_prefix(org_pk, pk))
            except Exception:
                log.exception("could not remove the trash of model version %d", pk)

    async def restore_version(self, org: Org, pk: int) -> dict[str, Any]:
        """Restore a deleted version of a live model, with its number and its checkpoint tags
        that are still free (D109)."""
        bucket = (await self.bucket(org)).upstream_name
        trash = await self._call(self._ensure_trash())
        async with self.store.db.pool.acquire() as conn, conn.transaction():
            model_pk = await conn.fetchval(
                "SELECT m.pk FROM model_versions v JOIN models m ON m.pk = v.model_pk "
                "WHERE v.pk = $1 AND m.org_pk = $2 AND v.deleted_at IS NOT NULL",
                pk,
                org.pk,
            )
            if model_pk is None:
                raise NotFound(f"deleted model version {pk} not found")
            record = await conn.fetchrow(
                f"SELECT {MODEL_COLUMNS} FROM models m WHERE m.pk = $1 FOR UPDATE", model_pk
            )
            assert record is not None
            model = Model(*record)
            if model.deleted_at is not None:
                raise Conflict(f"{org.name}/{model.name} is deleted. Restore the model instead.")
            (record,) = await self._lock_nowait(
                conn,
                "SELECT v.trashed_at IS NOT NULL, v.deleted_tags, v.dedupe_key, u.ulid "
                "FROM model_versions v JOIN model_uploads u ON u.pk = v.upload_pk WHERE v.pk = $1 "
                "FOR UPDATE OF v NOWAIT",
                pk,
            )
            moved, tags, dedupe_key, ulid = record
            if not moved:
                raise Conflict("This is still being moved to the trash. Try again later.")
            await self._restore_files(org.pk, bucket, trash, model.name, [(pk, ulid)])
            if dedupe_key is not None and await conn.fetchval(
                "SELECT 1 FROM model_versions WHERE model_pk = $1 AND dedupe_key = $2 "
                "AND deleted_at IS NULL",
                model.pk,
                dedupe_key,
            ):
                dedupe_key = None  # saved again since: the live one answers for it
            await conn.execute(
                "UPDATE model_versions SET deleted_at = NULL, deleted_by = NULL, "
                "trashed_at = NULL, deleted_tags = '{}', dedupe_key = $2 WHERE pk = $1",
                pk,
                dedupe_key,
            )
            await conn.executemany(
                "INSERT INTO model_tags (model_pk, name, version_pk, moved_at) "
                "VALUES ($1, $2, $3, now()) ON CONFLICT DO NOTHING",
                [(model.pk, tag, pk) for tag in tags],
            )
            info = await self._detail(conn, org, model, pk)
        await self._forget_trash(trash, org.pk, [pk])
        return info

    async def restore_model(self, org: Org, pk: int) -> dict[str, Any]:
        """Restore a deleted model with the versions deleted with it. ``Taken`` if a live
        model has its name now."""
        bucket = (await self.bucket(org)).upstream_name
        trash = await self._call(self._ensure_trash())
        async with self.store.db.pool.acquire() as conn, conn.transaction():
            records = await self._lock_nowait(
                conn,
                f"SELECT {MODEL_COLUMNS} FROM models m "
                "WHERE m.pk = $1 AND m.org_pk = $2 AND m.deleted_at IS NOT NULL FOR UPDATE NOWAIT",
                pk,
                org.pk,
            )
            if not records:
                raise NotFound(f"deleted model {pk} not found")
            model = Model(*records[0])
            if await conn.fetchval(
                "SELECT 1 FROM models WHERE org_pk = $1 AND name = $2 AND deleted_at IS NULL",
                org.pk,
                model.name,
            ):
                raise Taken(f"A model named {model.name} exists now.")
            versions = await self._lock_nowait(
                conn,
                "SELECT v.pk, v.trashed_at IS NOT NULL, u.ulid FROM model_versions v "
                "JOIN model_uploads u ON u.pk = v.upload_pk "
                "WHERE v.model_pk = $1 AND v.deleted_at = (SELECT deleted_at FROM models "
                "WHERE pk = $1) FOR UPDATE OF v NOWAIT",
                pk,
            )
            if not all(moved for _, moved, _ in versions):
                raise Conflict("This is still being moved to the trash. Try again later.")
            restored = [(v, ulid) for v, _, ulid in versions]
            await self._restore_files(org.pk, bucket, trash, model.name, restored)
            await conn.execute(
                "UPDATE model_versions SET deleted_at = NULL, deleted_by = NULL, "
                "trashed_at = NULL WHERE pk = ANY($1)",
                [v for v, _ in restored],
            )
            await conn.execute(
                "UPDATE models SET deleted_at = NULL, deleted_by = NULL WHERE pk = $1", pk
            )
        await self._forget_trash(trash, org.pk, (v for v, _ in restored))
        return await self.model_info(org, model.name)

    async def delete_version_now(self, org: Org, pk: int) -> None:
        """Remove a deleted version from the trash for good."""
        trash = await self._call(self._ensure_trash())
        async with self.store.db.pool.acquire() as conn, conn.transaction():
            records = await self._lock_nowait(
                conn,
                "SELECT v.trashed_at IS NOT NULL FROM model_versions v "
                "JOIN models m ON m.pk = v.model_pk "
                "WHERE v.pk = $1 AND m.org_pk = $2 AND v.deleted_at IS NOT NULL "
                "FOR UPDATE OF v NOWAIT",
                pk,
                org.pk,
            )
            if not records:
                raise NotFound(f"deleted model version {pk} not found")
            if not records[0][0]:
                raise Conflict("This is still being moved to the trash. Try again later.")
            await self._remove(trash, self._trash_prefix(org.pk, pk))
            await conn.execute("DELETE FROM model_versions WHERE pk = $1", pk)

    async def delete_model_now(self, org: Org, pk: int) -> None:
        """Remove a deleted model, and every deleted version of it, for good."""
        trash = await self._call(self._ensure_trash())
        async with self.store.db.pool.acquire() as conn, conn.transaction():
            records = await self._lock_nowait(
                conn,
                "SELECT m.pk FROM models m "
                "WHERE m.pk = $1 AND m.org_pk = $2 AND m.deleted_at IS NOT NULL FOR UPDATE NOWAIT",
                pk,
                org.pk,
            )
            if not records:
                raise NotFound(f"deleted model {pk} not found")
            await self._purge_model(conn, trash, org.pk, pk)

    async def _purge_model(
        self, conn: asyncpg.Connection, trash: str, org_pk: int, pk: int
    ) -> None:
        """With the deleted model ``pk`` locked: remove its versions' trash and its row."""
        versions = await self._lock_nowait(
            conn,
            "SELECT pk, trashed_at IS NOT NULL FROM model_versions WHERE model_pk = $1 "
            "FOR UPDATE NOWAIT",
            pk,
        )
        if not all(moved for _, moved in versions):
            raise Conflict("This is still being moved to the trash. Try again later.")
        for version, _ in versions:
            await self._remove(trash, self._trash_prefix(org_pk, version))
        await conn.execute("DELETE FROM models WHERE pk = $1", pk)

    async def delete_org(self, conn: asyncpg.Connection, org: Org) -> None:
        """In the org's deletion's transaction: forget its models. Their objects go with its
        buckets, and their trash with the dataset trash's ``purge_org``."""
        await conn.execute("DELETE FROM models WHERE org_pk = $1", org.pk)

    # ------------------------------------------------------------------ the sweep (D102, D109)

    async def sweep(self) -> dict[str, int]:
        """Expire idle uploads, remove stray folders, apply retention, finish moves to the
        trash, empty it after 14 days and remove trash prefixes with no row. Rows are claimed
        with ``SKIP LOCKED``, so any number of replicas can sweep at once."""
        done = {"expired": await self._expire()}
        done["strays"] = await self._sweep_strays()
        done["retained"] = await self._retain()
        done["moved"] = await self._move_pending()
        done["purged"] = await self._purge_trash()
        done["orphans"] = await self._sweep_orphans()
        done["manifests"] = await self.write_manifests()
        return done

    async def _expire(self) -> int:
        """Uploads with no activity for 7 days become ``expired``, and what landed goes."""
        expired = 0
        while True:
            async with self.store.db.pool.acquire() as conn, conn.transaction():
                record = await conn.fetchrow(
                    "SELECT u.pk, u.ulid, m.name, m.org_pk FROM model_uploads u "
                    "JOIN models m ON m.pk = u.model_pk WHERE u.state = 'open' "
                    "AND u.last_activity_at < now() - make_interval(days => $1) "
                    "ORDER BY u.pk LIMIT 1 FOR UPDATE OF u SKIP LOCKED",
                    self.EXPIRE_DAYS,
                )
                if record is None:
                    return expired
                pk, ulid, name, org_pk = record
                await conn.execute("UPDATE model_uploads SET state = 'expired' WHERE pk = $1", pk)
            # After the state commits, so the proxy stops taking writes first. A failure here
            # leaves the folder to the stray pass.
            bucket = await self._bucket_of(org_pk)
            if bucket is not None:
                try:
                    await self._clean(bucket, self._folder(name, ulid))
                except Exception:
                    log.exception("could not clean up the expired upload %s", ulid)
            expired += 1

    async def _sweep_strays(self) -> int:
        """Remove upload folders (and multipart uploads) of aborted and expired uploads, and
        those older than a day with no upload row at all; returns how many folders."""
        upstream = self._upstream()
        buckets = await self.store.db.fetch(
            "SELECT b.upstream_name FROM s3_buckets b JOIN orgs o ON o.pk = b.org_pk "
            "WHERE b.managed = 'models' AND b.deleted_at IS NULL AND o.deleted_at IS NULL "
            "ORDER BY b.pk"
        )
        cutoff = time.time() - self.STRAY_AFTER
        removed = 0
        for (bucket,) in buckets:
            folders: list[str] = []
            for model in await self._prefixes(bucket, ""):
                folders.extend(await self._prefixes(bucket, f"{model}u/"))
            unfinished = await self._call(upstream.list_uploads(bucket))
            ulids = {m[2] for f in folders if (m := UPLOAD_FOLDER.fullmatch(f))}
            ulids |= {m[2] for u in unfinished if (m := UPLOAD_FOLDER.match(u.key))}
            records = await self.store.db.fetch(
                "SELECT ulid, state FROM model_uploads WHERE ulid = ANY($1)", sorted(ulids)
            )
            states: dict[str, str] = {ulid: state for ulid, state in records}
            for upload in unfinished:
                found = UPLOAD_FOLDER.match(upload.key)
                if found is None:
                    continue
                state = states.get(found[2])
                if state == "open" or (state is None and upload.initiated > cutoff):
                    continue
                await self._call(upstream.abort_upload(bucket, upload.key, upload.upload_id))
            for folder in folders:
                found = UPLOAD_FOLDER.fullmatch(folder)
                state = states.get(found[2]) if found else None
                if found is None or state in ("open", "committed"):
                    continue
                if state is None:
                    objects = await self._call(upstream.list_all(bucket, folder))
                    if not objects or any(o.last_modified > cutoff for o in objects):
                        continue
                await self._remove(bucket, folder)
                removed += 1
        return removed

    async def _retain(self) -> int:
        """Retention (D109): in orgs that set it, delete the versions older than the newest N
        untagged ones of each model (untagged: no tag but checkpoint ones, not the primary),
        unless they have eval runs. They go to the trash like any deleted version."""
        candidates = await self.store.db.fetch(
            "SELECT c.pk, c.model_pk FROM ("
            "SELECT v.pk, v.model_pk, o.model_retention AS keep, row_number() OVER "
            "(PARTITION BY v.model_pk ORDER BY v.number DESC) AS rank FROM model_versions v "
            "JOIN models m ON m.pk = v.model_pk JOIN orgs o ON o.pk = m.org_pk "
            "WHERE o.model_retention IS NOT NULL AND o.deleted_at IS NULL "
            "AND m.deleted_at IS NULL AND v.deleted_at IS NULL "
            "AND v.pk IS DISTINCT FROM m.primary_version_pk AND NOT EXISTS (SELECT 1 FROM "
            "model_tags t WHERE t.version_pk = v.pk AND t.name !~ '^checkpoint-[0-9]+$')"
            ") c WHERE c.rank > c.keep AND NOT EXISTS "
            "(SELECT 1 FROM model_version_runs e WHERE e.version_pk = c.pk) ORDER BY c.pk"
        )
        deleted = 0
        for pk, model_pk in candidates:
            async with self.store.db.pool.acquire() as conn, conn.transaction():
                record = await conn.fetchrow(
                    f"SELECT {MODEL_COLUMNS} FROM models m WHERE m.pk = $1 "
                    "AND m.deleted_at IS NULL FOR UPDATE",
                    model_pk,
                )
                if record is None:
                    continue
                model = Model(*record)
                # Checked again with the model locked: a tag or the primary may have moved.
                live = await conn.fetchval(
                    "SELECT 1 FROM model_versions WHERE pk = $1 AND deleted_at IS NULL", pk
                )
                evaluated = await conn.fetchval(
                    "SELECT 1 FROM model_version_runs WHERE version_pk = $1 LIMIT 1", pk
                )
                if not live or evaluated or await self._refusal(conn, model, pk, "it"):
                    continue
                await self._mark_deleted(conn, pk, None)
            deleted += 1
        return deleted

    async def _move_pending(self) -> int:
        """Finish moving deleted versions to the trash: those whose request died, and those
        retention just deleted."""
        pks = await self.store.db.fetch(
            "SELECT pk FROM model_versions WHERE deleted_at IS NOT NULL AND trashed_at IS NULL "
            "ORDER BY pk"
        )
        moved = 0
        for (pk,) in pks:
            if await self._move_to_trash(pk):
                moved += 1
        return moved

    async def _purge_trash(self) -> int:
        """Remove deleted versions and models after 14 days in the trash."""
        trash = await self._ensure_trash()
        purged = 0
        while True:
            async with self.store.db.pool.acquire() as conn, conn.transaction():
                record = await conn.fetchrow(
                    "SELECT v.pk, m.org_pk FROM model_versions v "
                    "JOIN models m ON m.pk = v.model_pk WHERE v.trashed_at IS NOT NULL "
                    "AND v.deleted_at < now() - make_interval(days => $1) "
                    "AND v.deleted_at IS DISTINCT FROM m.deleted_at "
                    "ORDER BY v.pk LIMIT 1 FOR UPDATE OF v SKIP LOCKED",
                    self.TRASH_DAYS,
                )
                if record is None:
                    break
                pk, org_pk = record
                await self._remove(trash, self._trash_prefix(org_pk, pk))
                await conn.execute("DELETE FROM model_versions WHERE pk = $1", pk)
            purged += 1
        skipped: list[int] = []
        while True:
            async with self.store.db.pool.acquire() as conn, conn.transaction():
                record = await conn.fetchrow(
                    "SELECT pk, org_pk FROM models WHERE deleted_at IS NOT NULL "
                    "AND deleted_at < now() - make_interval(days => $1) AND pk <> ALL($2) "
                    "ORDER BY pk LIMIT 1 FOR UPDATE SKIP LOCKED",
                    self.TRASH_DAYS,
                    skipped,
                )
                if record is None:
                    break
                pk, org_pk = record
                try:
                    async with conn.transaction():
                        await self._purge_model(conn, trash, org_pk, pk)
                except Conflict:  # a version is still being moved: next time
                    skipped.append(pk)
                    continue
            purged += 1
        return purged

    async def _sweep_orphans(self) -> int:
        """Remove ``{org}/models/{pk}/`` prefixes of the trash whose version isn't deleted (a
        restore that didn't finish removing its copy, or no row at all) and whose objects are
        all older than a day."""
        upstream = self._upstream()
        trash = await self._ensure_trash()
        deleted = {
            pk
            for (pk,) in await self.store.db.fetch(
                "SELECT pk FROM model_versions WHERE deleted_at IS NOT NULL"
            )
        }
        cutoff = time.time() - self.STRAY_AFTER
        removed = 0
        for org_prefix in await self._prefixes(trash, ""):
            for entry in await self._prefixes(trash, f"{org_prefix}models/"):
                pk = entry.removeprefix(f"{org_prefix}models/").removesuffix("/")
                if pk.isdigit() and int(pk) in deleted:
                    continue
                objects = await self._call(upstream.list_all(trash, entry))
                if objects and all(o.last_modified <= cutoff for o in objects):
                    await self._remove(trash, entry)
                    removed += 1
        return removed

    async def _prefixes(self, bucket: str, prefix: str) -> list[str]:
        """Every folder one level under ``prefix``, over all pages."""
        upstream = self._upstream()
        found: list[str] = []
        token = None
        while True:
            page = await self._call(upstream.list_objects(bucket, prefix, "/", token))
            found.extend(page.prefixes)
            token = page.cursor
            if token is None:
                return found

    async def _sweep_loop(self) -> None:
        while True:
            await asyncio.sleep(self.SWEEP_INTERVAL)
            try:
                done = await self.sweep()
                if any(done.values()):
                    log.info("swept models: %s", done)
            except Exception:
                log.exception("sweeping models failed")
