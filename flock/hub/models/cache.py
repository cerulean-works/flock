"""Downloading a version (D103) into the cache, `{cache}/models/{org}/{name}/{digest}/`: each
file through `{path}.partial` with ranged GETs from one pool, checked against its sha256 before
it's renamed into place, under a lock that processes downloading the same version share."""

import fcntl
import fnmatch
import hashlib
import json
import os
import shutil
import threading
from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from flock.hub.models._common import (
    PART_SIZE,
    TRAINING_STATE,
    ModelError,
    ModelFile,
    ModelVersion,
    Pool,
    Ref,
    Registry,
    cache_root,
    concurrency,
    s3_client,
)

if TYPE_CHECKING:
    import torch

    from flock.training.core.models.base import BaseModel

BUCKET = "flock-models"
CHUNK = PART_SIZE  # the bytes of each ranged GET
PARTIAL = ".partial"
LOCK = ".lock"
COMPLETE = ".complete"  # the paths whose sha256 has been checked, as a JSON list

type Patterns = str | Sequence[str] | None
type Progress = Callable[[int], None]
# Aliases are evaluated lazily, so the signature can name classes this module doesn't import.
type Device = str | torch.device | None
type DType = torch.dtype | None
type LoadedModel = BaseModel


def version_folder(version: ModelVersion) -> Path:
    return cache_root() / "models" / version.org / version.name / version.digest


def _patterns(patterns: Patterns) -> list[str]:
    return [patterns] if isinstance(patterns, str) else list(patterns or ())


def select(
    files: Sequence[ModelFile], allow: Patterns, ignore: Patterns, include_training_state: bool
) -> list[ModelFile]:
    """The files to download: those matching an `allow` pattern (all, without any) and no
    `ignore` pattern, and `training_state/` only when asked for."""
    allowed, ignored = _patterns(allow), _patterns(ignore)

    def matches(path: str, patterns: list[str]) -> bool:
        return any(fnmatch.fnmatchcase(path, p) for p in patterns)

    return [
        f
        for f in files
        if (include_training_state or not f.path.startswith(TRAINING_STATE))
        and (not allowed or matches(f.path, allowed))
        and not matches(f.path, ignored)
    ]


@contextmanager
def _locked(folder: Path) -> Generator[None]:
    """Holds the version folder's lock, shared by every process on the machine."""
    with (folder / LOCK).open("a") as file:
        fcntl.flock(file, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(file, fcntl.LOCK_UN)


def _checked(folder: Path) -> set[str]:
    try:
        found = json.loads((folder / COMPLETE).read_text())
    except OSError, ValueError:
        return set()
    return {p for p in found if isinstance(p, str) and (folder / p).is_file()}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(8 * 2**20):
            digest.update(chunk)
    return digest.hexdigest()


class _Fetcher:
    def __init__(self, version: ModelVersion, folder: Path, progress: Progress | None) -> None:
        self.version = version
        self.folder = folder
        self.progress = progress
        self.s3 = s3_client(version.org, read_only=True)
        self.pool = Pool(concurrency())

    def run(self, files: Sequence[ModelFile]) -> None:
        for file in files:
            self.pool.submit(self.file, file)
        self.pool.wait()

    def file(self, file: ModelFile) -> None:
        target = self.folder / file.path
        partial = target.with_name(target.name + PARTIAL)
        target.parent.mkdir(parents=True, exist_ok=True)
        have = partial.stat().st_size if partial.exists() else 0
        if have > file.size:  # not this file's bytes: start again
            partial.unlink()
            have = 0
        partial.touch()
        # Resume from the partial file's size. Chunks are fetched at once but appended in
        # order, so the partial file never has a hole.
        ranges = [(start, min(start + CHUNK, file.size)) for start in range(have, file.size, CHUNK)]
        if not ranges:
            self.finish(file, partial, target)
            return
        order = {"next": 0, "done": threading.Condition()}
        for index, (start, end) in enumerate(ranges):
            self.pool.submit(
                self.chunk, file, partial, target, index, len(ranges), start, end, order
            )

    def chunk(
        self,
        file: ModelFile,
        partial: Path,
        target: Path,
        index: int,
        count: int,
        start: int,
        end: int,
        order: dict[str, Any],
    ) -> None:
        got = self.s3.get_object(
            Bucket=BUCKET, Key=self.version.prefix + file.path, Range=f"bytes={start}-{end - 1}"
        )
        data = got["Body"].read()
        if len(data) != end - start:
            raise ModelError(f"{file.path} came back with {len(data)} bytes, not {end - start}")
        done: threading.Condition = order["done"]
        with done:
            # The chunk before was submitted first, so it's running or done: this never waits
            # on a chunk that hasn't started.
            while order["next"] != index:
                if self.pool.error is not None:
                    return
                done.wait(1)
            with partial.open("ab") as out:
                out.write(data)
            order["next"] += 1
            done.notify_all()
        if self.progress is not None:
            self.progress(len(data))
        if index == count - 1:
            self.finish(file, partial, target)

    def finish(self, file: ModelFile, partial: Path, target: Path) -> None:
        if _sha256(partial) != file.sha256:
            partial.unlink(missing_ok=True)
            raise ModelError(
                f"{file.path} of {self.version} didn't match its sha256, so it was deleted. "
                "Download it again."
            )
        os.replace(partial, target)


def fetch(
    version: ModelVersion, files: Sequence[ModelFile], progress: Progress | None = None
) -> Path:
    """Downloads `files` of `version` into the cache, unless they're there already, and
    returns the version's folder."""
    folder = version_folder(version)
    wanted = {f.path for f in files}
    if wanted <= _checked(folder):
        return folder
    folder.mkdir(parents=True, exist_ok=True)
    with _locked(folder):
        checked = _checked(folder)
        todo = [f for f in files if f.path not in checked]
        if todo:
            _Fetcher(version, folder, progress).run(todo)
        marker = folder / (COMPLETE + PARTIAL)
        marker.write_text(json.dumps(sorted(checked | wanted)))
        os.replace(marker, folder / COMPLETE)
    return folder


def resolve(ref: str) -> ModelVersion:
    """The version `ref` names, with its files."""
    with Registry() as registry:
        return registry.resolve(Ref.parse(ref))


def download(
    ref: str,
    *,
    allow: str | Sequence[str] | None = None,
    ignore: str | Sequence[str] | None = None,
    include_training_state: bool = False,
) -> Path:
    """
    Downloads a version's files into the cache and returns its folder,
    `~/.cache/flock/models/{org}/{name}/{digest}/` (`FLOCK_CACHE` replaces `~/.cache/flock`).

    Files already in the cache aren't downloaded again, and a download that stopped partway
    continues from where it stopped. Every file is checked against its sha256 before it's put
    in place. Processes downloading the same version at once (one per rank) share one
    download. A read-only key is enough.

    Args:
        ref (`str`):
            The version: `org/name@12`, `org/name@checkpoint-12000`, `org/name@primary`, or
            `org/name` for the primary.
        allow (`str` or `Sequence[str]`, *optional*):
            Glob patterns of the files to download. Defaults to every file.
        ignore (`str` or `Sequence[str]`, *optional*):
            Glob patterns of files to skip.
        include_training_state (`bool`, *optional*, defaults to `False`):
            Also download `training_state/`, which is skipped otherwise.

    Returns:
        `Path`: the version's folder in the cache.

    Raises:
        [`~hub.models.ModelError`]: when the version doesn't exist, or a file didn't match its sha256.
    """
    version = resolve(ref)
    assert version.files is not None
    return fetch(version, select(version.files, allow, ignore, include_training_state))


def copy(folder: Path, files: Sequence[ModelFile], to: Path) -> None:
    """Copies `files` from the cache's `folder` into `to`."""
    for file in files:
        target = to / file.path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(folder / file.path, target)


def load_from_hub(
    ref: str,
    *,
    device: Device = None,
    dtype: DType = None,
) -> LoadedModel:
    """
    Downloads a version and builds the model with its weights.

    The model's class and config class come from the version's `flock.json`, and must be in
    `flock.training`, so a download never chooses what code runs. The model is built on the
    meta device, so no random initialization runs, and gets its weights with
    `load_state_dict(assign=True, strict=True)`. `training_state/` isn't downloaded.

    Args:
        ref (`str`):
            The version: `org/name@12`, `org/name@checkpoint-12000`, `org/name@primary`, or
            `org/name` for the primary.
        device (`str` or `torch.device`, *optional*):
            Where the weights go. Defaults to CUDA when it's available, else the CPU.
        dtype (`torch.dtype`, *optional*):
            The floating-point weights' dtype. Defaults to the dtype they were saved in.

    Returns:
        `BaseModel`: the model, such as an `Olmo2`.

    Raises:
        [`~hub.models.ModelError`]: when the version doesn't exist, a file didn't match its sha256, or
            `flock.json` names a class outside `flock.training`.

    Example:

    ```python
    from flock.hub import models

    model = models.load_from_hub("acme/olmo2-1b", device="cuda")  # the primary version
    model = models.load_from_hub("acme/olmo2-1b@checkpoint-12000")
    ```
    """
    from flock.hub.models.files import build

    version = resolve(ref)
    assert version.files is not None
    folder = fetch(version, select(version.files, None, None, False))
    model = build(folder, device, dtype)
    return model


def load_training_state(ref: str, *, device: str | None = None) -> dict[str, Any]:
    """
    Downloads a version's `training_state/` and returns it as [`~hub.models.save_to_hub`] was given it:
    names mapped to their state dicts, such as `{"optimizer": ...}`.

    Args:
        ref (`str`):
            The version, as for [`~hub.models.download`].
        device (`str`, *optional*, defaults to `"cpu"`):
            Where the tensors go.

    Example:

    ```python
    state = models.load_training_state("acme/olmo2-1b@checkpoint-12000")
    optimizer.load_state_dict(state["optimizer"])
    ```
    """
    from flock.hub.models.files import read_training_state

    version = resolve(ref)
    assert version.files is not None
    files = [
        f
        for f in version.files
        if f.path.startswith(TRAINING_STATE) and f.path.endswith(".safetensors")
    ]
    folder = fetch(version, files)
    return {
        f.path.removeprefix(TRAINING_STATE).removesuffix(".safetensors"): read_training_state(
            folder / f.path, device
        )
        for f in files
    }
