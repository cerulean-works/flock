"""What the parts of `flock.hub.models` share: references, versions, the registry's API, the
local cache and the S3 client settings."""

import hashlib
import os
import threading
from collections.abc import Callable, Collection
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from flock.hub._scope import scope_params, split_project, split_shorthand
from flock.hub.s3.client import S3Credentials, S3Error, _Api

PART_SIZE = 64 * 2**20
"""What files are uploaded in when the API doesn't say (D101), and the size of each ranged GET
of a download."""

CONCURRENCY = 32
"""Part requests in flight at once, across every file (`FLOCK_MODELS_CONCURRENCY`)."""

BUCKET = "flock-models"
TRAINING_STATE = "training_state/"

type Progress = Callable[[int], None]


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


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(8 * 2**20):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class Ref:
    """
    A model reference (D162): a model's `name`, a path such as `eval/olmo2-1b`, in `org`'s
    global scope or in its `project`, with an optional `version` (a number, a tag or
    `primary`). Prints as `acme/eval/olmo2-1b@12`, or `pretrain:sweeps/lr-3e-4@12` in a project.
    """

    org: str
    name: str
    version: str | None = None
    project: str | None = None

    @classmethod
    def parse(
        cls,
        ref: str,
        version: bool = True,
        *,
        project: str | None = None,
        org: str | None = None,
        default_org: Callable[[], str] | None = None,
        folder: bool = False,
    ) -> Ref:
        """
        Parses a model reference, `org/path[@version]`, or `project:path[@version]`, or with
        `project` a path inside it, `path[@version]`. The version is everything after the
        path's first `@`. Paths and versions can't hold `:` or `@`.

        Args:
            ref (`str`):
                The reference.
            version (`bool`, *optional*, defaults to `True`):
                Allow a version. With `False`, the model alone.
            project (`str`, *optional*):
                The project, by name in `org` or the default org, or as `org/project`. Not
                with the `project:path` shorthand.
            org (`str`, *optional*):
                The org of a project, for `project` or the shorthand.
            default_org (`Callable[[], str]`, *optional*):
                Called for the org when a project's ref doesn't say, once the ref is checked.
            folder (`bool`, *optional*, defaults to `False`):
                Parse a folder of models, whose path may be empty (a scope's root), and which
                has no version.

        Raises:
            `ValueError`: when `ref` isn't a reference, or names an org that isn't `org`.
        """
        example = (
            "acme/eval or pretrain:sweeps"
            if folder
            else "acme/olmo2-1b@12 or pretrain:sweeps/lr-3e-4@12"
            if version
            else "acme/olmo2-1b or pretrain:sweeps/lr-3e-4"
        )
        invalid = ValueError(f"{ref!r} isn't a model reference such as {example}")
        owner: str | None
        shorthand = split_shorthand(ref)
        if shorthand is not None:
            if project is not None:
                raise ValueError(f"{ref!r} names its project, so project= can't be given too")
            project, text = shorthand
            if not project:
                raise invalid
            owner = org
        elif project is not None:
            owner, project = split_project(project, org)
            text = ref
        else:
            owner, slash, text = ref.partition("/")
            if not owner or "@" in owner or not (text or folder and not slash):
                raise invalid
            if org is not None and owner != org:
                raise ValueError(
                    f"{ref!r} is in the org {owner}, not {org}: a global model is org/path, "
                    f"such as {org}/{ref}"
                )
        path, at, number = text.partition("@")
        segments = path.split("/") if path or not folder else []
        if any(s in ("", ".", "..") or ":" in s for s in segments) or not (segments or folder):
            raise invalid
        if at and (not number or any(c in number for c in "@:/")):
            raise invalid
        if at and (folder or not version):
            raise ValueError(
                f"{ref!r} names a version: give the model alone, as {ref[: -len(number) - 1]}"
            )
        if owner is None:
            if default_org is None:
                raise ValueError(f"{ref!r} needs an org: give org=")
            owner = default_org()
        return cls(owner, path, number or None, project)

    @property
    def model(self) -> str:
        """The model as people write it: `org/path`, or `project:path` in a project."""
        return f"{self.org}/{self.name}" if self.project is None else f"{self.project}:{self.name}"

    @property
    def path(self) -> str:
        """The model's route under `/api/v1/` (D163), with [`params`] beside it."""
        return f"orgs/{self.org}/models/{self.name}/-"

    @property
    def params(self) -> dict[str, str]:
        """The query parameters every route of the model takes: its project, if it has one."""
        return scope_params(self.project)

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
    `org/path@number`, such as `acme/eval/olmo2-1b@12`, or as `project:path@number`, such as
    `pretrain:sweeps/lr-3e-4@7`, for a project's model.

    Attributes:
        org (`str`):
            The organization.
        name (`str`):
            The model's path in its scope, such as `eval/olmo2-1b`.
        project (`str`, *optional*):
            The project the model belongs to, or `None` for a global model.
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
    project: str | None = field(default=None, kw_only=True)
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
        # A server before 022 answers no `org`, and only global `org/name` models.
        owner, _, name = info["model"].partition("/")
        files = info.get("files")
        return cls(
            org=info.get("org") or owner,
            name=info.get("name") or name,
            project=info.get("project"),
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

    @property
    def ref(self) -> Ref:
        """The version's reference, by number."""
        return Ref(self.org, self.name, str(self.number), self.project)

    @property
    def model(self) -> str:
        """The model, as `org/path` or `project:path`."""
        return self.ref.model

    def __str__(self) -> str:
        return str(self.ref)


def default_org() -> str:
    """Your default org, from Flock."""
    with Registry() as registry:
        return registry.default_org(None)


def parse_ref(
    ref: str, version: bool = True, *, project: str | None = None, org: str | None = None
) -> Ref:
    """[`Ref.parse`], asking Flock for your default org only when a project's ref needs it."""
    return Ref.parse(ref, version, project=project, org=org, default_org=default_org)


class Registry(_Api):
    """The model registry's routes, as the signed-in user. Refusals raise [`~hub.models.ModelError`]."""

    def __init__(self) -> None:
        try:
            super().__init__(None, None)
        except S3Error as error:
            raise ModelError(str(error)) from None

    def model_call(
        self,
        method: str,
        ref: Ref,
        operation: str = "",
        body: Any = None,
        ok: int | Collection[int] = 200,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Calls the route `operation` of `ref`'s model (`orgs/{org}/models/{path}/-/{operation}`),
        in its scope."""
        route = f"{ref.path}/{operation}" if operation else ref.path
        return self.call(method, route, body, ok, {**ref.params, **(params or {})} or None)

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
        return ModelVersion.from_api(self.model_call("GET", ref, "resolve", params=params))


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


class TransferPool:
    """Bound concurrent upload or download requests. File tasks may enqueue part tasks.

    The first failure stops queued work. Running tasks finish before `wait` raises it.
    """

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
