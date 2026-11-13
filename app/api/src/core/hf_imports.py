"""Importing a Hugging Face dataset or model from the web app, in the background (D124).

Each import is a row of ``hf_imports``, run by the replica that started it, which saves its
progress there at most once a second and heartbeats it. Any replica marks an import whose
heartbeat stopped interrupted (D81). The access token lives only in the running task: it's
never stored, so an interrupted import is started again from the dialog.

A model streams through: each file is downloaded from the Hub and written to an upload session
of the org's flock-models (D100), its safetensors header rewritten on the way (D123), and the
upload is committed with the same checks as any client's. Nothing touches local disk.

A dataset is downloaded to a temporary folder and converted by the same code as
``flock datasets convert``, in a child process (so a cancel stops it at once) writing straight
to the org's flock-datasets with the API's own S3 key.
"""

import asyncio
import contextlib
import dataclasses
import hashlib
import logging
import multiprocessing
import os
import shlex
import shutil
import signal
import tempfile
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Self
from urllib.parse import quote

import httpx
import orjson

from app.api.src.core.causal_lms import CAUSAL_LMS
from app.api.src.core.datasets import check_new_path
from app.api.src.core.errors import Conflict, Forbidden, Invalid, NotFound
from app.api.src.core.models import Models, check_name, check_tag
from app.api.src.s3_proxy.upstream import UpstreamError

if TYPE_CHECKING:
    from multiprocessing.connection import Connection

    from app.api.src.core.accounts import User
    from app.api.src.core.store import Org, Store
    from flock.hub.datasets import Manifest

log = logging.getLogger(__name__)

MIB = 1024 * 1024
GIB = 1024 * MIB
KINDS = ("dataset", "model")
STATES = ("running", "finished", "failed", "cancelled", "interrupted")
COLUMNS = (
    "pk, org_pk, kind, repo, revision, sha, dest, options::text, state, phase, bytes_total, "
    "bytes_done, files_total, files_done, rows_done, result::text, error, command, started_by, "
    "extract(epoch FROM started_at)::float8, extract(epoch FROM finished_at)::float8"
)


class HubError(Exception):
    """The Hub refused, or the repository can't be imported (``status`` is the HTTP status the
    API answers with)."""

    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


class TooLarge(Exception):
    """A repository over ``FLOCK_HF_IMPORT_MAX_BYTES``, with the ``flock`` command to run instead
    (HTTP 409)."""

    def __init__(self, message: str, command: str, size: int) -> None:
        super().__init__(message)
        self.message = message
        self.command = command
        self.size = size


def human(size: int) -> str:
    """A byte count for people, as the web app shows one (``bytes`` in its utils): ``12 GB``."""
    if size < 1024:
        return f"{size} B"
    value, unit = size / 1024, "KB"
    for bigger in ("MB", "GB", "TB"):
        if value < 1024:
            break
        value, unit = value / 1024, bigger
    return f"{value:.1f} {unit}" if value < 10 else f"{round(value)} {unit}"


def dataset_command(
    org: str, repo: str, dest: str, revision: str, config: str | None, splits: list[str] | None
) -> str:
    """The ``flock datasets import`` command doing what the dialog asked."""
    parts = ["flock", "datasets", "import", repo, dest]
    if config:
        parts += ["--config", config]
    for split in splits or ():
        parts += ["--split", split]
    if revision != "main":
        parts += ["--revision", revision]
    parts += ["--org", org]
    return shlex.join(parts)


def model_command(
    org: str, repo: str, name: str, revision: str, tags: list[str], primary: bool
) -> str:
    """The ``flock models import`` command doing what the dialog asked."""
    parts = ["flock", "models", "import", repo, f"{org}/{name}"]
    if revision != "main":
        parts += ["--revision", revision]
    for tag in tags:
        parts += ["--tag", tag]
    if primary:
        parts.append("--primary")
    return shlex.join(parts)


# ---------------------------------------------------------------------- the Hub


class Hub:
    """The Hub's API, file downloads and dataset viewer, with an import's access token.

    ``transport`` stands in for the network in tests."""

    SMALL_LIMIT: ClassVar[int] = 64 * MIB  # config.json and safetensors indexes
    CHUNK: ClassVar[int] = 4 * MIB

    def __init__(
        self,
        endpoint: str,
        datasets_server: str,
        token: str | None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.datasets_server = datasets_server.rstrip("/")
        self.token = token
        headers = {"authorization": f"Bearer {token}"} if token else {}
        # httpx drops the authorization header when a download redirects to another host.
        self.client = httpx.AsyncClient(
            headers=headers,
            follow_redirects=True,
            timeout=httpx.Timeout(30.0, read=300.0),
            transport=transport,
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    def _refusal(self, response: httpx.Response, what: str, revision: str | None) -> HubError:
        code = response.headers.get("x-error-code", "")
        if code == "GatedRepo":
            return HubError(
                f"{what} is gated. Accept its terms on the Hub, then add an access token.", 403
            )
        if code == "RevisionNotFound":
            return HubError(f"{what} has no revision {revision}.", 404)
        if code == "EntryNotFound":
            return HubError(f"A file of {what} is missing on the Hub.", 404)
        if response.status_code == 401 and self.token and code != "RepoNotFound":
            return HubError("The Hub refused the access token.", 401)
        if response.status_code in (401, 404) or code == "RepoNotFound":
            return HubError(
                f"{what} isn't on the Hub, or it's private. Add an access token for a private "
                "repository.",
                404,
            )
        if response.status_code == 403:
            return HubError(f"The access token can't read {what}.", 403)
        return HubError(f"The Hub answered {response.status_code} for {what}.", 502)

    async def _get(
        self, url: str, what: str, revision: str | None = None, **kw: Any
    ) -> httpx.Response:
        try:
            response = await self.client.get(url, **kw)
        except httpx.HTTPError as e:
            raise HubError(f"The Hub couldn't be reached. {e}", 502) from None
        if response.status_code not in (200, 206):
            raise self._refusal(response, what, revision)
        return response

    async def info(self, kind: str, repo: str, revision: str) -> dict[str, Any]:
        """A repository at ``revision``: its commit (``sha``) and files (``siblings``, with
        their ``size``)."""
        url = f"{self.endpoint}/api/{kind}s/{repo}/revision/{quote(revision, safe='')}"
        response = await self._get(url, repo, revision, params={"blobs": "true"})
        try:
            data = response.json()
            if not isinstance(data, dict) or not isinstance(data.get("sha"), str):
                raise TypeError
        except ValueError, TypeError:
            raise HubError(f"The Hub's answer for {repo} isn't what Flock expects.", 502) from None
        return data

    def url(self, kind: str, repo: str, revision: str, path: str) -> str:
        base = f"{self.endpoint}/datasets" if kind == "dataset" else self.endpoint
        return f"{base}/{repo}/resolve/{quote(revision, safe='')}/{quote(path)}"

    async def small(self, url: str, what: str) -> bytes:
        """A small file, whole (refused past ``SMALL_LIMIT``)."""
        async with self.stream(url, what) as (response, _):
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data += chunk
                if len(data) > self.SMALL_LIMIT:
                    raise HubError(f"{what} is larger than {self.SMALL_LIMIT // MIB} MiB.")
            return bytes(data)

    async def range(self, url: str, what: str, start: int, length: int) -> bytes:
        """``length`` bytes of a file from ``start``."""
        headers = {"range": f"bytes={start}-{start + length - 1}"}
        response = await self._get(url, what, headers=headers)
        if response.status_code == 200 and "content-range" not in response.headers:
            return response.content[start : start + length]  # the server ignored the range
        return response.content

    @contextlib.asynccontextmanager
    async def stream(
        self, url: str, what: str, start: int = 0
    ) -> AsyncGenerator[tuple[httpx.Response, int]]:
        """A file's bytes from ``start``, streamed, and how many leading bytes to skip (when the
        server ignored the range)."""
        headers = {"range": f"bytes={start}-"} if start else {}
        try:
            async with self.client.stream("GET", url, headers=headers) as response:
                if response.status_code not in (200, 206):
                    await response.aread()
                    raise self._refusal(response, what, None)
                skip = start if start and response.status_code == 200 else 0
                yield response, skip
        except httpx.HTTPError as e:
            raise HubError(f"The download of {what} from the Hub failed. {e}", 502) from None

    async def viewer(self, route: str, repo: str) -> dict[str, Any] | None:
        """An answer of the dataset viewer API, or None when it has none for ``repo``."""
        try:
            response = await self.client.get(
                f"{self.datasets_server}/{route}", params={"dataset": repo}
            )
        except httpx.HTTPError:
            return None
        if response.status_code in (401, 403, 404) and response.headers.get("x-error-code") in (
            "GatedRepo",
            "RepoNotFound",
        ):
            raise self._refusal(response, repo, None)
        if response.status_code != 200:
            return None
        try:
            data = response.json()
        except ValueError:
            return None
        return data if isinstance(data, dict) else None


def siblings(info: dict[str, Any]) -> dict[str, int]:
    """A repository's files and their sizes, from ``Hub.info``."""
    found = {}
    for entry in info.get("siblings") or ():
        if isinstance(entry, dict) and isinstance(entry.get("rfilename"), str):
            size = entry.get("size")
            if not isinstance(size, int):
                lfs = entry.get("lfs")
                size = lfs.get("size") if isinstance(lfs, dict) else None
            found[entry["rfilename"]] = size if isinstance(size, int) else 0
    return found


# ---------------------------------------------------------------------- plans


@dataclass
class ModelPlan:
    """What a model import downloads: the repository's config, its index if sharded, and each
    weight and ride-along file with its size."""

    sha: str
    hf_config: dict[str, Any]
    index: dict[str, Any] | None
    weights: list[tuple[str, int]]
    others: list[tuple[str, int]]

    @property
    def size(self) -> int:
        return sum(s for _, s in self.weights) + sum(s for _, s in self.others)


@dataclass
class DatasetPlan:
    """What a dataset import downloads: the manifest ``flock datasets download`` would write,
    each file's size, and where each split lands."""

    manifest: Manifest
    sizes: dict[str, int]
    paths: dict[str, str]
    card_revision: str

    @property
    def size(self) -> int:
        return sum(self.sizes.values())


# ---------------------------------------------------------------------- jobs


@dataclass(eq=False)
class HFImport:
    pk: int
    org_pk: int
    kind: str
    repo: str
    revision: str
    sha: str | None
    dest: str
    options: dict[str, Any]
    state: str
    phase: str | None
    bytes_total: int
    bytes_done: int
    files_total: int
    files_done: int
    rows_done: int
    result: dict[str, Any] | None
    error: str | None
    command: str
    started_by: int | None
    started_at: float
    finished_at: float | None = None
    task: asyncio.Task | None = field(default=None, repr=False)
    saved_at: float = field(default=0.0, repr=False)

    @classmethod
    def from_record(cls, record: Any) -> HFImport:
        values = list(record)
        values[7] = orjson.loads(values[7])
        values[15] = orjson.loads(values[15]) if values[15] is not None else None
        return cls(*values)

    @property
    def progress(self) -> float:
        """How far along it is, from 0 to 1: a dataset's download is the first half and its
        conversion the second, a model's is its bytes uploaded."""
        if self.state == "finished":
            return 1.0
        downloaded = self.bytes_done / self.bytes_total if self.bytes_total else 0.0
        if self.kind == "model":
            return min(downloaded, 1.0)
        if self.phase == "converting":
            converted = self.files_done / self.files_total if self.files_total else 0.0
            return 0.5 + 0.5 * min(converted, 1.0)
        return 0.5 * min(downloaded, 1.0)

    def info(self) -> dict[str, Any]:
        return {
            "id": self.pk,
            "kind": self.kind,
            "repo": self.repo,
            "revision": self.revision,
            "sha": self.sha,
            "dest": self.dest,
            **self.options,
            "state": self.state,
            "phase": self.phase,
            "bytes_total": self.bytes_total,
            "bytes_done": self.bytes_done,
            "files_total": self.files_total,
            "files_done": self.files_done,
            "rows_done": self.rows_done,
            "progress": self.progress,
            "result": self.result,
            "error": self.error,
            "command": self.command,
            "started_by": self.started_by,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class Cancelled(Exception):
    """Raised in a job when a cancel from another replica is seen."""


class HFImports:
    """The Hugging Face imports of every org (``store.hf_imports``)."""

    SAVE_INTERVAL: ClassVar[float] = 1.0
    HEARTBEAT: ClassVar[float] = 10.0
    STALE_AFTER: ClassVar[float] = 60.0
    LIST_LIMIT: ClassVar[int] = 100
    LIST_DAYS: ClassVar[int] = 7
    POLL: ClassVar[float] = 0.2  # seconds between looks at a conversion's child process

    def __init__(self, store: Store) -> None:
        self.store = store
        self.jobs: dict[int, HFImport] = {}  # this replica's running imports, by pk
        # Stands in for the network in tests (the Hub, its files and the dataset viewer).
        self.transport: httpx.AsyncBaseTransport | None = None

    @property
    def settings(self) -> Any:
        return self.store.settings

    def hub(self, token: str | None) -> Hub:
        s = self.settings
        return Hub(s.hf_endpoint, s.hf_datasets_server, token, self.transport)

    # ------------------------------------------------------------------ lifecycle

    async def load(self) -> None:
        await self.sweep()

    async def sweep(self) -> None:
        """Mark imports whose replica stopped heartbeating interrupted (D81)."""
        await self.store.db.execute(
            "UPDATE hf_imports SET state = 'interrupted', phase = NULL, finished_at = now() "
            "WHERE state = 'running' AND (heartbeat_at IS NULL "
            "OR heartbeat_at < now() - make_interval(secs => $1))",
            self.STALE_AFTER,
        )

    async def close(self) -> None:
        """Stop this replica's running imports (on shutdown); they're left interrupted."""
        for job in list(self.jobs.values()):
            await self._stop(job, "interrupted")

    async def _stop(self, job: HFImport, state: str) -> None:
        task = job.task
        if task is None or task.done():
            return
        job.state = state
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    # ------------------------------------------------------------------ reads

    async def get(self, org: Org, pk: int) -> HFImport:
        job = self.jobs.get(pk)
        if job is not None and job.org_pk == org.pk:
            return job
        record = await self.store.db.fetchrow(
            f"SELECT {COLUMNS} FROM hf_imports WHERE pk = $1 AND org_pk = $2", pk, org.pk
        )
        if record is None:
            raise NotFound(f"import {pk} not found")
        return HFImport.from_record(record)

    async def recent(self, org: Org) -> list[HFImport]:
        """The org's imports of the last week, newest first."""
        records = await self.store.db.fetch(
            f"SELECT {COLUMNS} FROM hf_imports WHERE org_pk = $1 "
            "AND started_at > now() - make_interval(days => $2) ORDER BY pk DESC LIMIT $3",
            org.pk,
            self.LIST_DAYS,
            self.LIST_LIMIT,
        )
        return [self.jobs.get(r[0]) or HFImport.from_record(r) for r in records]

    # ------------------------------------------------------------------ previews

    async def preview_model(self, org: Org, repo: str, revision: str, token: str | None) -> dict:
        """What the model dialog shows: the commit, files and size, and whether the import is
        refused (and why)."""
        async with self.hub(token) as hub:
            info = await hub.info("model", repo, revision)
            files = siblings(info)
            answer: dict[str, Any] = {
                "kind": "model",
                "repo": repo,
                "revision": revision,
                "sha": info["sha"],
                "model_type": None,
                "refusal": None,
                "size": sum(files.values()),
                "max_bytes": self.settings.hf_import_max_bytes,
            }
            try:
                plan = await self._model_plan(hub, repo, revision, info)
            except HubError as e:
                if e.status != 422:
                    raise
                answer["refusal"] = e.message
            else:
                answer["model_type"] = plan.hf_config.get("model_type")
                answer["size"] = plan.size
            answer["too_large"] = answer["size"] > self.settings.hf_import_max_bytes
            return answer

    async def preview_dataset(self, org: Org, repo: str, revision: str, token: str | None) -> dict:
        """What the dataset dialog shows: each config's splits with their sizes, from the dataset
        viewer API, else from the files a download would take (for the default config only)."""
        async with self.hub(token) as hub:
            await hub.info("dataset", repo, revision)  # a missing or gated repo, said plainly
            splits = await hub.viewer("splits", repo) if revision == "main" else None
            configs: dict[str, list[dict[str, Any]]] = {}
            for entry in (splits or {}).get("splits") or ():
                if isinstance(entry, dict) and entry.get("config") and entry.get("split"):
                    configs.setdefault(entry["config"], []).append(
                        {"name": entry["split"], "size": None, "rows": None}
                    )
            sizes: dict[str, int] = {}
            if configs:
                found = await hub.viewer("size", repo)
                size = (found or {}).get("size") or {}
                for entry in size.get("splits") or ():
                    for split in configs.get(entry.get("config"), ()):
                        if split["name"] == entry.get("split"):
                            split["size"] = entry.get("num_bytes_parquet_files")
                            split["rows"] = entry.get("num_rows")
                for entry in size.get("configs") or ():
                    if isinstance(entry.get("num_bytes_original_files"), int):
                        sizes[entry.get("config")] = entry["num_bytes_original_files"]
                listed = [
                    {
                        "name": name,
                        "splits": found_splits,
                        "size": sizes.get(name)
                        or sum(s["size"] or 0 for s in found_splits)
                        or None,
                    }
                    for name, found_splits in configs.items()
                ]
            else:
                plan = await self._dataset_plan(hub, repo, revision, None, None, None, token)
                manifest = plan.manifest
                listed = [
                    {
                        "name": name,
                        "splits": [
                            {
                                "name": split,
                                "size": sum(plan.sizes.get(f.path, 0) for f in files),
                                "rows": None,
                            }
                            for split, files in manifest.splits.items()
                        ]
                        if name == manifest.config
                        else None,
                        "size": plan.size if name == manifest.config else None,
                    }
                    for name in manifest.configs
                ]
        names = [c["name"] for c in listed]
        default = "default" if len(names) > 1 and "default" in names else next(iter(names), None)
        return {
            "kind": "dataset",
            "repo": repo,
            "revision": revision,
            "configs": listed,
            "default_config": default,
            "max_bytes": self.settings.hf_import_max_bytes,
        }

    async def _model_plan(
        self, hub: Hub, repo: str, revision: str, info: dict[str, Any]
    ) -> ModelPlan:
        """Check a model repository can be imported, and list what to download."""
        from flock.hub.models import ModelError
        from flock.hub.models.files import INDEX, SINGLE
        from flock.hub.models.hf import CONFIG, RIDE_ALONG, choose_files, weight_files

        sha = info["sha"]
        files = siblings(info)
        try:
            choose_files(list(files), repo)
        except ModelError as e:
            raise HubError(str(e)) from None
        raw = await hub.small(hub.url("model", repo, sha, CONFIG), f"{repo}'s {CONFIG}")
        try:
            hf_config = orjson.loads(raw)
            if not isinstance(hf_config, dict):
                raise TypeError
        except orjson.JSONDecodeError, TypeError:
            raise HubError(f"{repo}'s {CONFIG} isn't a JSON object.") from None
        if hf_config.get("auto_map"):
            raise HubError(
                f"{repo} needs code from its repository to load, which Flock doesn't run."
            )
        model_type = hf_config.get("model_type")
        if not isinstance(model_type, str) or model_type not in CAUSAL_LMS:
            raise HubError(
                f"{repo} is a {model_type or 'model with no model_type'}, which isn't a causal "
                "language model that Flock can train."
            )
        index = None
        if INDEX in files:
            raw = await hub.small(hub.url("model", repo, sha, INDEX), f"{repo}'s {INDEX}")
            try:
                index = orjson.loads(raw)
                if not isinstance(index, dict):
                    raise TypeError
                names = weight_files(index, f"{repo}'s {INDEX}")
            except orjson.JSONDecodeError, TypeError, ModelError:
                raise HubError(f"{repo}'s {INDEX} has no weight_map.") from None
        else:
            names = [SINGLE]
        missing = [n for n in names if n not in files]
        if missing:
            raise HubError(f"{repo}'s {INDEX} names {missing[0]}, which isn't in the repository.")
        others = [(n, files[n]) for n in RIDE_ALONG if n in files]
        return ModelPlan(sha, hf_config, index, [(n, files[n]) for n in names], others)

    async def _dataset_plan(
        self,
        hub: Hub,
        repo: str,
        revision: str,
        config: str | None,
        splits: list[str] | None,
        dest: str | None,
        token: str | None,
    ) -> DatasetPlan:
        """The files ``flock datasets download`` would take, with their sizes, and where each
        split lands under ``dest``."""
        from flock.hub.datasets import DatasetError
        from flock.hub.datasets._convert import split_paths
        from flock.hub.datasets._download import plan

        try:
            manifest = await asyncio.to_thread(
                plan,
                repo,
                config=config,
                splits=splits,
                revision=revision,
                token=token or False,
                endpoint=self.settings.hf_endpoint,
            )
        except DatasetError as e:
            raise HubError(str(e)) from None
        info = await hub.info("dataset", repo, manifest.revision)
        files = siblings(info)
        wanted = [f.path for split in manifest.splits.values() for f in split]
        sizes = {path: files.get(path, 0) for path in wanted}
        card_revision = revision if manifest.ref is not None else manifest.revision
        paths = split_paths(manifest, dest) if dest is not None else {}
        return DatasetPlan(manifest, sizes, paths, card_revision)

    # ------------------------------------------------------------------ starting

    async def _running_at(self, org: Org, kind: str, dest: str) -> bool:
        return bool(
            await self.store.db.fetchval(
                "SELECT true FROM hf_imports WHERE org_pk = $1 AND kind = $2 AND dest = $3 "
                "AND state = 'running'",
                org.pk,
                kind,
                dest,
            )
        )

    async def start_model(
        self,
        org: Org,
        user: User,
        repo: str,
        revision: str,
        name: str,
        tags: list[str],
        primary: bool,
        token: str | None,
    ) -> HFImport:
        """Check the model and start importing it as a new version of ``name``. Raises
        ``HubError`` when the Hub refuses or the repository can't be imported, and ``TooLarge``
        past the size limit."""
        check_name(name)
        for tag in tags:
            check_tag(tag)
        if len(set(tags)) != len(tags):
            raise Invalid("A tag is given twice.")
        command = model_command(org.name, repo, name, revision, tags, primary)
        if await self._running_at(org, "model", name):
            raise Conflict(f"An import into {name} is already running.")
        await self.store.models.bucket(org, ensure=True)
        hub = self.hub(token)
        try:
            info = await hub.info("model", repo, revision)
            plan = await self._model_plan(hub, repo, revision, info)
            self._check_size(plan.size, "model", command)
            options = {"tags": tags, "primary": primary}
            job = await self._insert(org, user, "model", repo, revision, name, options, command)
        except BaseException:
            await hub.close()
            raise
        job.sha = plan.sha
        job.bytes_total = plan.size
        job.task = asyncio.get_running_loop().create_task(
            self._run(job, self._model(job, org, user, hub, plan), hub)
        )
        return job

    async def start_dataset(
        self,
        org: Org,
        user: User,
        repo: str,
        revision: str,
        config: str | None,
        splits: list[str] | None,
        dest: str,
        token: str | None,
    ) -> HFImport:
        """Check the dataset and start importing it to ``dest``. Raises ``HubError`` when the
        Hub refuses or the dataset can't be imported, ``TooLarge`` past the size limit, and
        ``Conflict`` when a dataset is already at a split's path."""
        dest = dest.strip("/")
        check_new_path(dest)
        if await self._running_at(org, "dataset", dest):
            raise Conflict(f"An import into {dest} is already running.")
        bucket = (await self.store.datasets.bucket(org, ensure=True)).upstream_name
        hub = self.hub(token)
        try:
            await hub.info("dataset", repo, revision)
            plan = await self._dataset_plan(hub, repo, revision, config, splits, dest, token)
            manifest = plan.manifest
            command = dataset_command(
                org.name, repo, dest, revision, manifest.config, splits and list(splits)
            )
            self._check_size(plan.size, "dataset", command)
            await self._check_free(bucket, dest, plan.paths)
            options = {"config": manifest.config, "splits": list(manifest.splits)}
            job = await self._insert(org, user, "dataset", repo, revision, dest, options, command)
        except BaseException:
            await hub.close()
            raise
        job.sha = manifest.revision
        job.bytes_total = plan.size
        job.files_total = sum(len(f) for f in manifest.splits.values())
        job.task = asyncio.get_running_loop().create_task(
            self._run(job, self._dataset(job, org, hub, plan, bucket), hub)
        )
        return job

    def _check_size(self, size: int, kind: str, command: str) -> None:
        limit = self.settings.hf_import_max_bytes
        if size > limit:
            raise TooLarge(
                f"This {kind} is {human(size)}, more than the {human(limit)} this server "
                "imports. Import it with the flock command instead.",
                command,
                size,
            )

    async def _check_free(self, bucket: str, dest: str, paths: dict[str, str]) -> None:
        datasets = self.store.datasets
        taken = [p for p in paths.values() if await datasets.latest(bucket, p) is not None]
        if taken:
            raise Conflict(f"There's already a dataset at {taken[0]}. Choose another folder.")
        if await datasets.latest(bucket, dest) is not None:
            raise Conflict(f"There's already a dataset at {dest}. Choose another folder.")
        if await datasets._inside_dataset(bucket, dest):
            raise Conflict(f"{dest} is inside a dataset. Choose another folder.")

    async def _insert(
        self,
        org: Org,
        user: User,
        kind: str,
        repo: str,
        revision: str,
        dest: str,
        options: dict[str, Any],
        command: str,
    ) -> HFImport:
        record = await self.store.db.fetchrow(
            "INSERT INTO hf_imports (org_pk, kind, repo, revision, dest, options, state, "
            "command, started_by, started_at, heartbeat_at) "
            "VALUES ($1, $2, $3, $4, $5, $6::jsonb, 'running', $7, $8, now(), now()) "
            f"RETURNING {COLUMNS}",
            org.pk,
            kind,
            repo,
            revision,
            dest,
            orjson.dumps(options).decode(),
            command,
            user.pk,
        )
        job = HFImport.from_record(record)
        self.jobs[job.pk] = job
        return job

    # ------------------------------------------------------------------ cancelling

    async def cancel(self, org: Org, pk: int, user: User) -> HFImport:
        """Stop a running import (who started it, or an admin). An import another replica runs
        stops at its next save."""
        job = await self.get(org, pk)
        if job.started_by != user.pk and self.store.accounts.role(user, org) != "admin":
            raise Forbidden("Only who started an import, or an admin, can cancel it.")
        if job.state != "running":
            return job
        if pk in self.jobs:
            await self._stop(self.jobs[pk], "cancelled")
            return job
        await self.store.db.execute(
            "UPDATE hf_imports SET cancel_requested = true, state = 'cancelled', phase = NULL, "
            "finished_at = now() WHERE pk = $1 AND state = 'running'",
            pk,
        )
        return await self.get(org, pk)

    async def cancel_org(self, org: Org) -> None:
        """Stop the org's imports (when it's deleted)."""
        for job in [j for j in self.jobs.values() if j.org_pk == org.pk]:
            await self._stop(job, "cancelled")
        await self.store.db.execute(
            "UPDATE hf_imports SET cancel_requested = true, state = 'cancelled', phase = NULL, "
            "finished_at = now() WHERE org_pk = $1 AND state = 'running'",
            org.pk,
        )

    # ------------------------------------------------------------------ running

    async def _save(self, job: HFImport, force: bool = False) -> None:
        now = time.time()
        if not force and now - job.saved_at < self.SAVE_INTERVAL:
            return
        job.saved_at = now
        # A cancel from another replica wins: its state stays, and this import stops.
        cancelled = await self.store.db.fetchval(
            """
            UPDATE hf_imports SET state = CASE WHEN cancel_requested THEN state ELSE $2 END,
                phase = $3, sha = $4, bytes_total = $5, bytes_done = $6, files_total = $7,
                files_done = $8, rows_done = $9, result = $10::jsonb, error = $11,
                finished_at = coalesce(finished_at, to_timestamp($12)), heartbeat_at = now()
            WHERE pk = $1
            RETURNING cancel_requested
            """,
            job.pk,
            job.state,
            job.phase,
            job.sha,
            job.bytes_total,
            job.bytes_done,
            job.files_total,
            job.files_done,
            job.rows_done,
            orjson.dumps(job.result).decode() if job.result is not None else None,
            job.error,
            job.finished_at,
        )
        if cancelled and job.state == "running":
            job.state = "cancelled"
            raise Cancelled

    async def _heartbeat(self, job: HFImport) -> None:
        while True:
            await asyncio.sleep(self.HEARTBEAT)
            try:
                await self._save(job, force=True)
            except Cancelled:
                if job.task is not None:
                    job.task.cancel()  # cancelled from another replica
                return
            except Exception:
                log.exception("could not save the state of Hugging Face import %d", job.pk)

    async def _run(self, job: HFImport, work: Any, hub: Hub) -> None:
        heartbeat = asyncio.get_running_loop().create_task(self._heartbeat(job))
        try:
            await self._save(job, force=True)
            await work
            job.state = "finished"
        except asyncio.CancelledError, Cancelled:
            if job.state == "running":
                job.state = "cancelled"
        except NotFound as e:  # the org, or its bucket, was deleted
            job.state = "failed"
            job.error = str(e)
        except Exception as e:
            known = (HubError, Conflict, Invalid, Forbidden)
            if not isinstance(e, known) and not _is_dataset_error(e):
                log.exception("Hugging Face import %d of %s failed", job.pk, job.repo)
            job.state = "failed"
            job.error = _message(e)
        finally:
            work.close()  # never started, if the first save failed
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            await hub.close()
            job.phase = None
            job.finished_at = time.time()
            try:
                await self._save(job, force=True)
            except Cancelled:
                pass  # cancelled from another replica, which saved its state
            except Exception:
                log.exception("could not save the state of Hugging Face import %d", job.pk)
            if self.jobs.get(job.pk) is job:
                del self.jobs[job.pk]
            log.info(
                "Hugging Face import %d of %s into %s %s", job.pk, job.repo, job.dest, job.state
            )

    # ------------------------------------------------------------------ models

    async def _model(self, job: HFImport, org: Org, user: User, hub: Hub, plan: ModelPlan) -> None:
        from flock.hub.models.files import INDEX
        from flock.hub.models.headers import (
            hf_to_flock,
            rename_header,
            rename_index,
            tensors,
        )
        from flock.hub.models.hf import (
            CONFIG,
            dedupe_key,
            dump,
            provenance,
            version_config,
            weight_dtype,
        )

        job.phase = "uploading"
        repo, sha = job.repo, plan.sha
        limit = asyncio.Semaphore(max(1, self.settings.hf_import_workers))

        async def header(name: str) -> tuple[int, bytes]:
            """The old header's length (with its 8-byte prefix) and the renamed one."""
            async with limit:
                url, what = hub.url("model", repo, sha, name), f"{repo}'s {name}"
                start = await hub.range(url, what, 0, 8)
                if len(start) < 8:
                    raise HubError(f"{name} of {repo} isn't a safetensors file.")
                length = int.from_bytes(start, "little")
                if length > Models.HEADER_LIMIT:
                    raise HubError(f"{name} of {repo} isn't a safetensors file.")
                raw = await hub.range(url, what, 8, length)
                try:
                    return 8 + length, rename_header(raw, hf_to_flock)
                except ValueError as e:
                    raise HubError(f"{name} of {repo} can't be renamed. {e}.") from None

        names = [name for name, _ in plan.weights]
        headers = dict(zip(names, await asyncio.gather(*map(header, names)), strict=True))
        found = [info for name in names for info in tensors(headers[name][1][8:]).values()]
        small = {
            CONFIG: dump(version_config(plan.hf_config, repo)),
            "flock.json": dump(provenance(repo, sha, weight_dtype(found))),
        }
        if plan.index is not None:
            small[INDEX] = dump(rename_index(plan.index, hf_to_flock))
        sizes = dict(plan.others)
        for name, size in plan.weights:
            old, new = headers[name]
            sizes[name] = size - old + len(new)
        sizes.update({name: len(data) for name, data in small.items()})
        job.bytes_total = sum(sizes.values())
        await self._save(job, force=True)

        models = self.store.models
        opened, upload = await models.open_upload(
            org,
            job.dest,
            user,
            [{"path": p, "size": s} for p, s in sizes.items()],
            {},
            job.options["tags"],
            job.options["primary"],
            None,
            None,
            dedupe_key(repo, sha),
        )
        if not opened:  # this commit was imported into the model before
            job.bytes_done = job.bytes_total
            job.result = {"model": job.dest, "version": upload["number"], "existing": True}
            return
        ulid = upload["ulid"]
        bucket = (await models.bucket(org)).upstream_name
        prefix = upload["prefix"]
        try:
            hashes: dict[str, str] = {}

            async def send(path: str) -> None:
                async with limit:
                    if path in small:
                        data = small[path]
                        hashes[path] = await self._put(job, bucket, prefix + path, data)
                        return
                    url, what = hub.url("model", repo, sha, path), f"{repo}'s {path}"
                    old, new = headers.get(path, (0, b""))
                    async with hub.stream(url, what, old) as (response, skip):
                        hashes[path] = await self._put_stream(
                            job, bucket, prefix + path, sizes[path], new, response, skip, what
                        )

            async with asyncio.TaskGroup() as group:
                for path in sizes:
                    group.create_task(send(path))
            job.phase = "committing"
            await self._save(job, force=True)
            version = await models.commit(org, job.dest, ulid, user, hashes)
        except BaseException:
            try:
                await asyncio.shield(models.abort(org, job.dest, ulid, user))
            except Exception:
                log.exception("could not abort the upload of Hugging Face import %d", job.pk)
            raise
        job.result = {"model": job.dest, "version": version["number"]}

    async def _put(self, job: HFImport, bucket: str, key: str, data: bytes) -> str:
        sha = hashlib.sha256(data).hexdigest()
        await self.store.s3.require().put_object(bucket, key, data, None, {"sha256": sha})
        job.bytes_done += len(data)
        await self._save(job)
        return sha

    async def _put_stream(
        self,
        job: HFImport,
        bucket: str,
        key: str,
        size: int,
        first: bytes,
        response: httpx.Response,
        skip: int,
        what: str,
    ) -> str:
        """Write ``first`` then the response's bytes to ``key``, as one PUT or a multipart
        upload, with their sha256 as ``x-amz-meta-sha256``; returns it. Progress counts bytes
        as they arrive from the Hub."""
        upstream = self.store.s3.require()
        part_size = Models.PART_SIZE
        digest = hashlib.sha256()
        buffer = bytearray(first)
        written = 0
        upload_id: str | None = None
        etags: list[str] = []

        async def flush(data: bytes) -> None:
            nonlocal upload_id
            if upload_id is None:
                upload_id = await upstream.create_upload(bucket, key, None)
            etags.append(await upstream.upload_part(bucket, key, upload_id, len(etags) + 1, data))

        job.bytes_done += len(first)
        try:
            async for chunk in response.aiter_bytes():
                if skip:
                    dropped = min(skip, len(chunk))
                    chunk, skip = chunk[dropped:], skip - dropped
                buffer += chunk
                job.bytes_done += len(chunk)
                await self._save(job)
                while len(buffer) > part_size:
                    part = bytes(buffer[:part_size])
                    del buffer[:part_size]
                    digest.update(part)
                    written += len(part)
                    await flush(part)
            if written + len(buffer) != size:
                raise HubError(f"{what} changed on the Hub while it was imported.", 502)
            rest = bytes(buffer)
            digest.update(rest)
            sha = digest.hexdigest()
            if upload_id is None:
                await upstream.put_object(bucket, key, rest, None, {"sha256": sha})
                return sha
            if rest:
                await flush(rest)
            await upstream.complete_upload(bucket, key, upload_id, etags)
            upload_id = None
            # Parts carry no metadata and the sha256 is only known now: copy the object onto
            # itself with it, server side.
            if size > Models.COPY_LIMIT:
                await upstream.copy_large(
                    bucket, key, size, bucket, key, Models.COPY_PART, {"sha256": sha}
                )
            else:
                await upstream.copy_object(bucket, key, bucket, key, {"sha256": sha})
            return sha
        except UpstreamError as e:
            raise HubError(f"The S3 server refused {key}. {e}", 502) from None
        finally:
            if upload_id is not None:
                with contextlib.suppress(Exception):
                    await asyncio.shield(upstream.abort_upload(bucket, key, upload_id))

    # ------------------------------------------------------------------ datasets

    async def _dataset(
        self, job: HFImport, org: Org, hub: Hub, plan: DatasetPlan, bucket: str
    ) -> None:
        from flock.hub.datasets import MANIFEST
        from flock.hub.datasets._download import CARD

        manifest = plan.manifest
        folder = await asyncio.to_thread(lambda: Path(tempfile.mkdtemp(prefix="flock-hf-")))
        try:
            job.phase = "downloading"
            await self._save(job, force=True)
            limit = asyncio.Semaphore(max(1, self.settings.hf_import_workers))

            async def fetch(path: str, revision: str) -> None:
                async with limit:
                    target = folder / path
                    await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
                    url = hub.url("dataset", job.repo, revision, path)
                    async with hub.stream(url, f"{job.repo}'s {path}") as (response, _):
                        with target.open("wb") as file:
                            async for chunk in response.aiter_bytes():
                                await asyncio.to_thread(file.write, chunk)
                                job.bytes_done += len(chunk)
                                await self._save(job)

            async with asyncio.TaskGroup() as group:
                for files in manifest.splits.values():
                    for file in files:
                        group.create_task(fetch(file.path, manifest.revision))
                if manifest.card is not None:
                    group.create_task(fetch(CARD, plan.card_revision))
            await asyncio.to_thread((folder / MANIFEST).write_text, manifest.to_json())

            job.phase = "converting"
            await self._save(job, force=True)
            job.result = {"datasets": await self._convert(job, folder, bucket)}
            job.rows_done = sum(d["rows"] for d in job.result["datasets"])
        finally:
            await asyncio.to_thread(shutil.rmtree, folder, True)

    def _s3(self) -> dict[str, Any]:
        """How the conversion reaches the S3 server: the API's own key."""
        s = self.settings
        return {
            "storage_options": self.store.datasets.storage_options(),
            "endpoint": s.s3_upstream_endpoint or "",
            "region": s.s3_upstream_region,
            "access_key_id": s.s3_upstream_access_key_id or "",
            "secret_access_key": s.s3_upstream_secret_access_key or "",
        }

    async def _convert(self, job: HFImport, folder: Path, bucket: str) -> list[dict[str, Any]]:
        """Run the conversion in a child process, reading its progress until it ends."""
        context = multiprocessing.get_context("spawn")
        receive, send = context.Pipe(duplex=False)
        workers = max(1, min(self.settings.hf_import_workers, job.files_total or 1))
        child = context.Process(
            target=convert_child,
            args=(send, str(folder), job.dest, bucket, self._s3(), workers),
            name=f"flock-hf-import-{job.pk}",
        )
        child.start()
        send.close()
        outcome: tuple[str, Any] | None = None
        try:
            while outcome is None:
                while receive.poll():
                    try:
                        message = receive.recv()
                    except EOFError:
                        break
                    if message[0] == "file":
                        job.files_done += 1
                        job.rows_done += message[1]
                        await self._save(job)
                    else:
                        outcome = message
                        break
                if outcome is None:
                    if not child.is_alive() and not receive.poll():
                        break
                    await asyncio.sleep(self.POLL)
        finally:
            if child.is_alive():
                _kill(child)
            await asyncio.to_thread(child.join, 10)
            receive.close()
        if outcome is None:
            raise HubError("The conversion stopped unexpectedly, so nothing was written.", 500)
        if outcome[0] == "error":
            raise ConversionFailed(outcome[1])
        return outcome[1]


class ConversionFailed(Exception):
    """The conversion refused the files, with its message."""


def _is_dataset_error(e: Exception) -> bool:
    return isinstance(e, ConversionFailed) or type(e).__name__ == "DatasetError"


def _message(e: Exception) -> str:
    if isinstance(e, (HubError, Conflict, Forbidden)):
        return e.message
    if isinstance(e, ExceptionGroup):
        return _message(e.exceptions[0])
    return str(e) or type(e).__name__


def _kill(child: multiprocessing.process.BaseProcess) -> None:
    """Kill a conversion's child process and the workers it started (its process group)."""
    pid = child.pid
    if pid is None:
        return
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError, PermissionError:
        child.kill()  # it hadn't made its group yet, so it has no workers either


def convert_child(
    send: Connection, folder: str, dest: str, bucket: str, s3: dict[str, Any], workers: int
) -> None:
    """A conversion, in a child process of its own group (so a cancel kills its workers too):
    ``_convert.run`` to ``bucket`` with the API's key, telling the parent each file written and
    how it ended."""
    os.setpgrp()
    os.environ.setdefault("LANCE_LOG", "error")
    try:
        import pyarrow.fs as pafs

        from flock.hub.datasets import DatasetError
        from flock.hub.datasets._convert import Target, run

        endpoint = s3["endpoint"] if "://" in s3["endpoint"] else f"https://{s3['endpoint']}"
        scheme, _, rest = endpoint.partition("://")
        filesystem = pafs.S3FileSystem(
            access_key=s3["access_key_id"],
            secret_key=s3["secret_access_key"],
            region=s3["region"],
            scheme=scheme,
            endpoint_override=rest.rstrip("/"),
            force_virtual_addressing=False,
            allow_bucket_creation=False,
            allow_bucket_deletion=False,
            check_directory_existence_before_creation=False,
        )
        target = Target(f"s3://{bucket}", filesystem, bucket, s3["storage_options"])
        try:
            converted = run(
                folder,
                dest,
                target,
                workers=workers,
                on_file=lambda written: send.send(("file", written.rows)),
            )
        except DatasetError as e:
            send.send(("error", str(e)))
            return
        send.send(
            (
                "done",
                [{k: v for k, v in dataclasses.asdict(c).items() if k != "uri"} for c in converted],
            )
        )
    except Exception as e:  # noqa: BLE001 - said to the parent rather than lost
        send.send(("error", f"The conversion failed. {e}"))
    finally:
        send.close()
