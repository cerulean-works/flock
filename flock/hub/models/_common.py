"""What the parts of `flock.hub.models` share: references, versions, the registry's API, the
local cache and the S3 client settings."""

import os
import threading
from collections.abc import Callable, Collection
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from flock.hub.s3.client import S3Credentials, S3Error, _Api

PART_SIZE = 64 * 2**20
"""What files are uploaded in when the API doesn't say (D101), and the size of each ranged GET
of a download."""

CONCURRENCY = 32
"""Part requests in flight at once, across every file (`FLOCK_MODELS_CONCURRENCY`)."""

TRAINING_STATE = "training_state/"


class ModelError(S3Error):
    """
    A model couldn't be saved, loaded or changed: Flock refused (`status` and `body` say why),
    a local file changed under an upload, or a download didn't match its sha256.
    """


def cache_root() -> Path:
    """Where downloaded models and upload journals are kept: `FLOCK_CACHE`, else
    `~/.cache/flock`."""
    configured = os.environ.get("FLOCK_CACHE")
    return Path(configured).expanduser() if configured else Path.home() / ".cache" / "flock"


def concurrency() -> int:
    """Requests in flight at once: `FLOCK_MODELS_CONCURRENCY`, else 32."""
    configured = os.environ.get("FLOCK_MODELS_CONCURRENCY", "")
    if not configured:
        return CONCURRENCY
    if not configured.isdigit() or int(configured) < 1:
        raise ValueError(f"FLOCK_MODELS_CONCURRENCY is {configured!r}, not a positive number")
    return int(configured)


@dataclass(frozen=True)
class Ref:
    """A model reference, `org/name` with an optional `@version` (a number, a tag or
    `primary`)."""

    org: str
    name: str
    version: str | None = None

    @classmethod
    def parse(cls, ref: str, version: bool = True) -> Ref:
        """Parses `org/name[@version]`; with `version=False`, `org/name` alone."""
        model, at, number = ref.partition("@")
        org, _, name = model.partition("/")
        if not org or not name or "/" in name or (at and not number) or "@" in number:
            example = "acme/olmo2-1b@12" if version else "acme/olmo2-1b"
            raise ValueError(f"{ref!r} isn't a model reference such as {example}")
        if at and not version:
            raise ValueError(f"{ref!r} names a version: give the model alone, as {model}")
        return cls(org, name, number or None)

    @property
    def model(self) -> str:
        return f"{self.org}/{self.name}"

    @property
    def path(self) -> str:
        """The model's route under `/api/v1/`."""
        return f"orgs/{self.org}/models/{self.name}"

    def __str__(self) -> str:
        return self.model if self.version is None else f"{self.model}@{self.version}"


@dataclass(frozen=True)
class ModelFile:
    """A file of a version: its `path` in the version's folder, `size` in bytes and `sha256`."""

    path: str
    size: int
    sha256: str


@dataclass(frozen=True)
class ModelVersion:
    """
    A version of a model, as Flock answered when it was saved, resolved or listed. Prints as
    `org/name@number`, such as `acme/olmo2-1b@12`.

    Attributes:
        org (`str`):
            The organization.
        name (`str`):
            The model's name in it.
        number (`int`):
            The version number, which never changes or gets reused.
        digest (`str`):
            The sha256 of the version's file list, which names its exact bytes.
        tags (`tuple[str, ...]`):
            Its tags, by name.
        primary (`bool`):
            Whether it's the primary version, the one loaded when no version is given.
        step (`int`, *optional*):
            The training step it was saved at.
        run (`str`, *optional*):
            The run that made it, as `entity/project/id`, when it's a run of the same org.
        size (`int`):
            The bytes of all its files.
        files (`tuple[ModelFile, ...]`, *optional*):
            Each file's path, size and sha256. `None` in [`~hub.models.versions`]'s listing, which doesn't
            include them.
        metadata (`dict`):
            The `metadata` it was saved with.
        created_at (`float`):
            When it was committed, in seconds since the epoch.
    """

    org: str
    name: str
    number: int
    digest: str
    tags: tuple[str, ...] = ()
    primary: bool = False
    step: int | None = None
    run: str | None = None
    size: int = 0
    files: tuple[ModelFile, ...] | None = None
    metadata: dict[str, Any] = field(default_factory=dict, compare=False)
    created_at: float = 0.0
    prefix: str = field(default="", repr=False)  # the version's folder in flock-models

    @classmethod
    def from_api(cls, info: dict[str, Any]) -> ModelVersion:
        org, _, name = info["model"].partition("/")
        files = info.get("files")
        return cls(
            org=org,
            name=name,
            number=info["number"],
            digest=info["digest"],
            tags=tuple(info.get("tags", ())),
            primary=info.get("primary", False),
            step=info.get("step"),
            run=info.get("run"),
            size=info.get("total_bytes", 0),
            files=None if files is None else tuple(ModelFile(**f) for f in files),
            metadata=info.get("metadata") or {},
            created_at=info.get("created_at") or 0.0,
            prefix=info.get("prefix", ""),
        )

    def __str__(self) -> str:
        return f"{self.org}/{self.name}@{self.number}"


class Registry(_Api):
    """The model registry's routes, as the signed-in user. Refusals raise [`~hub.models.ModelError`]."""

    def __init__(self) -> None:
        try:
            super().__init__(None, None)
        except S3Error as error:
            raise ModelError(str(error)) from None

    def call(
        self,
        method: str,
        path: str,
        body: Any = None,
        ok: int | Collection[int] = 200,
        params: dict[str, Any] | None = None,
    ) -> Any:
        try:
            return super().call(method, path, body, ok, params)
        except S3Error as error:
            raise ModelError(str(error), error.status, error.body) from None

    def resolve(self, ref: Ref) -> ModelVersion:
        """The version `ref` names, with its files."""
        params = {"ref": ref.version} if ref.version is not None else None
        return ModelVersion.from_api(self.call("GET", f"{ref.path}/resolve", params=params))


def s3_client(org: str, read_only: bool = False, pool: int | None = None) -> Any:
    """A boto3 client on the proxy for `org`'s buckets, renewing its key by itself, with
    botocore's standard retries and room for `pool` connections."""
    from botocore.config import Config

    config = Config(
        retries={"mode": "standard", "max_attempts": 5},
        max_pool_connections=pool or concurrency(),
    )
    try:
        return S3Credentials(org, read_only=read_only).boto3_client(config=config)
    except S3Error as error:
        raise ModelError(str(error), error.status, error.body) from None


class Pool:
    """One pool for every request of a push: tasks may add tasks, and the first failure stops
    the rest and is raised by `wait`."""

    def __init__(self, workers: int) -> None:
        self._executor = ThreadPoolExecutor(workers, thread_name_prefix="flock-models")
        self._pending = 0
        self._done = threading.Condition()
        self.error: BaseException | None = None

    def submit(self, task: Callable[..., None], *args: Any) -> None:
        with self._done:
            if self.error is not None:
                return
            self._pending += 1
        self._executor.submit(self._run, task, args)

    def _run(self, task: Callable[..., None], args: tuple[Any, ...]) -> None:
        try:
            if self.error is None:
                task(*args)
        except BaseException as error:  # noqa: BLE001 - raised again by wait()
            with self._done:
                self.error = self.error or error
        finally:
            with self._done:
                self._pending -= 1
                self._done.notify_all()

    def wait(self) -> None:
        try:
            with self._done:
                self._done.wait_for(lambda: self._pending == 0)
        except BaseException as error:
            self.error = self.error or error
            raise
        finally:
            self._executor.shutdown(wait=True, cancel_futures=True)
        if self.error is not None:
            raise self.error


def human(size: float) -> str:
    """`size` bytes for people: `512 B`, `3.1 MB`, `4.2 GB`."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1000 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1000
    raise AssertionError("unreachable")
