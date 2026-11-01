"""Run files (D3, D82): what clients upload next to a run (``wandb-metadata.json``,
``config.yaml``, ...), on storage every replica sees.

Two backends, chosen by ``FLOCK_FILES_BACKEND``:

- ``local``: a directory (``FLOCK_FILES_DIR``), one subdirectory per run. Right for one
  replica, or for several on a volume they all mount (a ``ReadWriteMany`` volume such as
  CephFS), since every write is a temporary file renamed into place.
- ``s3``: the bucket ``FLOCK_FILES_BUCKET`` on the upstream S3 server the proxy uses, with
  keys ``runs/<run pk>/<name>``. Files not found there are read from the directory, so a
  server keeps serving while ``scripts/migrate_files.py`` copies the directory over.
"""

import asyncio
import logging
import shutil
from collections.abc import AsyncIterator, Iterable
from pathlib import Path

from app.api.src.s3_proxy.upstream import Upstream, UpstreamError
from app.api.src.settings import Settings

log = logging.getLogger(__name__)

# A file found: a local path (served as is) or its bytes as chunks, with its size.
Found = Path | tuple[AsyncIterator[bytes], int]


class FileStore:
    """Where run files live; ``names`` are checked relative paths (``Store.file_parts``)."""

    async def open(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def put(self, run_pk: int, name: str, data: bytes) -> None:
        raise NotImplementedError

    async def get(self, run_pk: int, name: str) -> Found | None:
        raise NotImplementedError

    async def delete_runs(self, run_pks: Iterable[int]) -> None:
        raise NotImplementedError

    async def runs(self) -> list[int]:
        """Pks of the runs that have files (to find those of runs that don't exist)."""
        raise NotImplementedError


class LocalFiles(FileStore):
    def __init__(self, root: Path) -> None:
        self.root = root

    async def open(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, run_pk: int, name: str) -> Path:
        return self.root / str(run_pk) / name

    async def put(self, run_pk: int, name: str, data: bytes) -> None:
        path = self.path(run_pk, name)

        def write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(path)

        await asyncio.to_thread(write)

    async def get(self, run_pk: int, name: str) -> Found | None:
        path = self.path(run_pk, name)
        return path if await asyncio.to_thread(path.is_file) else None

    async def delete_runs(self, run_pks: Iterable[int]) -> None:
        def remove() -> None:
            for pk in run_pks:
                shutil.rmtree(self.root / str(pk), ignore_errors=True)

        await asyncio.to_thread(remove)

    async def runs(self) -> list[int]:
        def list_runs() -> list[int]:
            if not self.root.is_dir():
                return []
            return [int(p.name) for p in self.root.iterdir() if p.name.isdigit()]

        return await asyncio.to_thread(list_runs)


class S3Files(FileStore):
    PREFIX = "runs/"
    CHUNK = 1 << 16
    DELETE_BATCH = 1000

    def __init__(self, settings: Settings) -> None:
        if not settings.files_bucket:
            raise RuntimeError("FLOCK_FILES_BUCKET must be set with FLOCK_FILES_BACKEND=s3")
        self.bucket = settings.files_bucket
        self.upstream = Upstream(settings)
        # Files written before the move to S3, until scripts/migrate_files.py copies them.
        self.fallback = LocalFiles(settings.files_dir)

    async def open(self) -> None:
        if not await self.upstream.bucket_exists(self.bucket):
            try:
                await self.upstream.create_bucket(self.bucket)
            except UpstreamError as exc:
                if exc.code not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
                    raise  # another replica may have created it meanwhile

    async def close(self) -> None:
        await self.upstream.close()

    def key(self, run_pk: int, name: str) -> str:
        return f"{self.PREFIX}{run_pk}/{name}"

    async def put(self, run_pk: int, name: str, data: bytes) -> None:
        await self.upstream.put_object(self.bucket, self.key(run_pk, name), data)

    async def get(self, run_pk: int, name: str) -> Found | None:
        response = await self.upstream.get_object(self.bucket, self.key(run_pk, name))
        if response is None:
            return await self.fallback.get(run_pk, name)
        size = int(response.headers.get("content-length", 0))

        async def chunks() -> AsyncIterator[bytes]:
            try:
                async for chunk in response.aiter_bytes(self.CHUNK):
                    yield chunk
            finally:
                await response.aclose()

        return chunks(), size

    async def _keys(self, prefix: str) -> list[str]:
        keys, token = [], None
        while True:
            listing = await self.upstream.list_objects(self.bucket, prefix, token=token)
            keys += [item.key for item in listing.objects]
            token = listing.cursor
            if token is None:
                return keys

    async def delete_runs(self, run_pks: Iterable[int]) -> None:
        pks = list(run_pks)
        for pk in pks:
            keys = await self._keys(f"{self.PREFIX}{pk}/")
            for start in range(0, len(keys), self.DELETE_BATCH):
                await self.upstream.delete_keys(
                    self.bucket, keys[start : start + self.DELETE_BATCH]
                )
        await self.fallback.delete_runs(pks)

    async def runs(self) -> list[int]:
        found, token = set(), None
        while True:
            listing = await self.upstream.list_objects(
                self.bucket, self.PREFIX, delimiter="/", token=token
            )
            for prefix in listing.prefixes:
                pk = prefix[len(self.PREFIX) :].rstrip("/")
                if pk.isdigit():
                    found.add(int(pk))
            token = listing.cursor
            if token is None:
                break
        return sorted(found | set(await self.fallback.runs()))


def file_store(settings: Settings) -> FileStore:
    if settings.files_backend == "local":
        return LocalFiles(settings.files_dir)
    if settings.files_backend == "s3":
        return S3Files(settings)
    raise RuntimeError(f"FLOCK_FILES_BACKEND must be local or s3, not {settings.files_backend!r}")
