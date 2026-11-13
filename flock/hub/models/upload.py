"""Save, publish and resume a model version using the registry and the S3 proxy.

Both Python and the CLI upload from disk. A new upload declares its files to the registry,
records the returned upload ID locally, sends the files, then commits the version.
"""

import logging
import os
import secrets
import shutil
import threading
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any

from flock.hub.models._common import (
    PART_SIZE,
    ModelError,
    ModelVersion,
    Progress,
    Ref,
    Registry,
    s3_client,
    sha256_file,
)
from flock.hub.models._upload_journal import (
    STAGING_PREFIX,
    Journal,
    UploadFile,
    find_journal,
    journals,
    uploads_root,
)
from flock.hub.models._upload_transfer import UploadTransfer

if TYPE_CHECKING:
    import wandb

    from flock.training.core.models.base import BaseModel

log = logging.getLogger(__name__)

# Aliases are evaluated lazily, so the signature can name classes this module doesn't import.
type ModelOrFolder = BaseModel | str | os.PathLike[str]
type RunOrPath = wandb.Run | str | None


class PendingUpload:
    """
    A version being uploaded in a background thread, from `save_to_hub(..., blocking=False)`.
    The files are already on local disk, so the model can change meanwhile.
    """

    def __init__(self, name: str, job: Callable[[], ModelVersion]) -> None:
        self.name = name
        self._future: Future[ModelVersion] = Future()

        def run() -> None:
            try:
                self._future.set_result(job())
            except BaseException as error:  # noqa: BLE001 - raised by result()
                self._future.set_exception(error)

        # On normal process exit, Python waits for a non-daemon upload thread.
        threading.Thread(target=run, name=f"flock-models-{name}", daemon=False).start()

    def result(self, timeout: float | None = None) -> ModelVersion:
        """
        Waits for the version and returns it. If the worker fails, raises its exception.

        Args:
            timeout (`float`, *optional*):
                Seconds to wait. Waits until the upload ends if not set.

        Raises:
            `TimeoutError`: if `timeout` passes first. The upload keeps running.
        """
        return self._future.result(timeout)

    def done(self) -> bool:
        """Whether the upload has ended, with a version or an error."""
        return self._future.done()

    def __repr__(self) -> str:
        state = "done" if self.done() else "uploading"
        return f"PendingUpload({self.name!r}, {state})"


def _run_path(run: Any) -> tuple[str | None, str | None]:
    """`run` as `entity/project/id`, and its id."""
    if run is None:
        return None, None
    if isinstance(run, str):
        return run, run.rsplit("/", 1)[-1]
    return f"{run.entity}/{run.project}/{run.id}", run.id


def save_to_hub(
    model: ModelOrFolder,
    name: str,
    *,
    tags: Sequence[str] = (),
    primary: bool = False,
    metadata: Mapping[str, Any] | None = None,
    run: RunOrPath = None,
    step: int | None = None,
    dedupe_key: str | None = None,
    training_state: Mapping[str, Any] | None = None,
    shard_size: int = 5 * 2**30,
    blocking: bool = True,
    resume: bool = True,
    keep_local: bool = False,
) -> ModelVersion | PendingUpload:
    """
    Saves `model` as a new version of `name`, creating the model if it's new.

    A model object is serialized to local disk first. The client opens an upload,
    records its journal, then transfers the files through the S3 proxy. The version
    becomes visible when the registry commits it.

    Args:
        model (`BaseModel`, `str` or `os.PathLike`):
            A Flock model, such as `Olmo2`, whose `config` is a frozen dataclass. Or the path
            of a folder that already holds a version's files, which is pushed as it is.
        name (`str`):
            The model, as `org/name`.
        tags (`Sequence[str]`, *optional*):
            Tags for the new version. A tag on another version moves to it, except a
            `checkpoint-{step}` tag, which is refused.
        primary (`bool`, *optional*, defaults to `False`):
            Make the new version primary once it's committed. A model's first version is
            primary anyway.
        metadata (`Mapping[str, Any]`, *optional*):
            JSON values stored with the version, at most 256 KiB.
        run (`wandb.Run` or `str`, *optional*):
            The run that made the model, or its `entity/project/id`. The registry links the
            version to an existing run in the same org. Model snapshots also record it in
            `flock.json`.
        step (`int`, *optional*):
            The training step, stored with the version and in the model snapshot's `flock.json`.
        dedupe_key (`str`, *optional*):
            A live version of the same model with this key is returned. Defaults to
            `{run id}:{step}` when both `run` and `step` are given.
        training_state (`Mapping[str, Any]`, *optional*):
            Names mapped to state dicts, such as `{"optimizer": optimizer.state_dict()}`. Each
            is saved as `training_state/{name}.safetensors`, which loading skips unless asked.
        shard_size (`int`, *optional*, defaults to 5 GiB):
            Target shard size in bytes. A tensor larger than this gets its own shard.
            Multiple shards are recorded in `model.safetensors.index.json`.
        blocking (`bool`, *optional*, defaults to `True`):
            Wait for the version. With `False`, return a [`~hub.models.PendingUpload`] once the files are
            on local disk, and upload in a background thread.
        resume (`bool`, *optional*, defaults to `True`):
            Finish an interrupted upload of the same model instead of starting another: of the
            same folder with unchanged files, or for a model, of the same `dedupe_key`.
        keep_local (`bool`, *optional*, defaults to `False`):
            Keep the written model in `~/.cache/flock/uploads/{ulid}/model/` once the version
            exists.

    Returns:
        [`~hub.models.ModelVersion`] or [`~hub.models.PendingUpload`]: the version, or with `blocking=False`, the
        upload in progress.

    Raises:
        [`~hub.models.ModelError`]: when Flock refuses the upload or the source changed.
            If transfer has started, the journal is available to resume it.
        `TypeError`: if `model.config` isn't a frozen dataclass.

    Example:

    ```python
    from flock.hub import models

    version = models.save_to_hub(model, "acme/olmo2-1b", tags=["checkpoint-12000"], step=12000)
    print(version)  # acme/olmo2-1b@1
    ```
    """
    ref = Ref.parse(name, version=False)
    run_path, run_id = _run_path(run)
    if dedupe_key is None and run_id is not None and step is not None:
        dedupe_key = f"{run_id}:{step}"
    options = {
        "metadata": dict(metadata or {}),
        "tags": list(tags),
        "primary": primary,
        "run": run_path,
        "step": step,
        "dedupe_key": dedupe_key,
    }
    if isinstance(model, (str, os.PathLike)):
        if training_state:
            raise ValueError("training_state can only be saved with a model, not a folder")
        source = Path(model).expanduser().resolve()
        if not source.is_dir():
            raise ModelError(f"{source} isn't a folder")

        job = partial(push, source, ref, options, resume=resume, keep_local=True)

    else:
        earlier = None
        if resume and dedupe_key is not None:
            earlier = next(
                (
                    journal
                    for journal in journals(ref.model)
                    if journal.staged and journal.options.get("dedupe_key") == dedupe_key
                ),
                None,
            )
        if earlier is not None:
            log.info("Resuming upload %s of %s (%s)", earlier.ulid, ref, dedupe_key)
            job = partial(resume_journal, earlier)
        else:
            from flock.hub.models.files import write_model

            staging = uploads_root() / f"{STAGING_PREFIX}{secrets.token_hex(8)}"
            try:
                write_model(
                    model,
                    staging,
                    shard_size=shard_size,
                    run=run_path,
                    step=step,
                    training_state=training_state,
                )
            except BaseException:
                shutil.rmtree(staging, ignore_errors=True)
                raise

            job = partial(push, staging, ref, options, staged=True, keep_local=keep_local)

    if blocking:
        return job()
    return PendingUpload(ref.model, job)


def push(
    source: Path,
    ref: Ref,
    options: dict[str, Any],
    *,
    staged: bool = False,
    keep_local: bool = False,
    resume: bool = True,
    progress: Progress | None = None,
) -> ModelVersion:
    """Publish a folder from the CLI or Python, reusing an unchanged folder's journal."""
    paths = []
    for file in sorted(source.rglob("*")):
        relative = file.relative_to(source)
        if not any(part.startswith(".") for part in relative.parts) and file.is_file():
            paths.append(relative.as_posix())
    if not paths:
        raise ModelError(f"{source} has no files to push")
    if resume and not staged:
        file_names = set(paths)
        for journal in journals(ref.model):
            if journal.source != source or journal.files.keys() != file_names:
                continue
            if journal.changed_file() is None:
                log.info("Resuming upload %s of %s from %s", journal.ulid, ref, source)
                return resume_journal(journal, progress)

    def describe_file(path: str) -> UploadFile:
        file = source / path
        stat = file.stat()
        return {
            "size": stat.st_size,
            "mtime": stat.st_mtime,
            "sha256": sha256_file(file),
            "upload_id": None,
            "done": False,
        }

    with ThreadPoolExecutor(min(8, os.cpu_count() or 1)) as pool:
        files = dict(zip(paths, pool.map(describe_file, paths), strict=True))

    with Registry() as registry:
        opened = _open_upload(registry, ref, files, options)
        if isinstance(opened, ModelVersion):
            if staged and not keep_local:
                shutil.rmtree(source, ignore_errors=True)
            return opened

        journal = Journal.create(
            ref,
            opened.ulid,
            opened.part_size,
            source,
            files,
            options,
            staged=staged,
            keep_local=keep_local,
        )
        return _send_and_commit(registry, journal, progress, resume=False)


def resume_uploads(name: str) -> list[ModelVersion]:
    """
    Finishes every upload of `name` that a crashed process left on this machine, and returns
    the versions made. Call it when a job starts, before saving anything new.

    Open uploads reuse files and parts already in storage. Expired or aborted uploads
    restart with their original files. If those files changed or are gone, their journals
    are discarded. A committed upload returns its existing version.

    Args:
        name (`str`):
            The model, as `org/name`.

    Raises:
        [`~hub.models.ModelError`]: when a local file changed since its upload started. Abort that upload
            with `flock models uploads abort ULID`.
    """
    ref = Ref.parse(name, version=False)
    done = []
    for journal in journals(ref.model):
        version = _resume_upload(journal, None, discard_stale=True)
        if version is not None:
            done.append(version)
    return done


def resume_journal(journal: Journal, progress: Progress | None = None) -> ModelVersion:
    """Finish one interrupted upload, raising if it still needs changed or missing files."""
    version = _resume_upload(journal, progress, discard_stale=False)
    assert version is not None
    return version


def _resume_upload(
    journal: Journal, progress: Progress | None, *, discard_stale: bool
) -> ModelVersion | None:
    ref = journal.ref
    with Registry() as registry:
        state = _upload_state(registry, journal)
        if state == "committed":
            # The server committed before the client could remove the journal.
            version = _commit(registry, journal)
            journal.cleanup(committed=True)
            return version

        changed = journal.changed_file()
        if changed is not None:
            if discard_stale and state != "open":
                log.info(
                    "Discarding upload %s of %s (%s): %s changed", journal.ulid, ref, state, changed
                )
                journal.cleanup()
                return None
            raise ModelError(
                f"{changed} in {journal.source} changed or is gone since upload {journal.ulid} "
                f"started, so it can't be resumed. Abort it with flock models uploads abort "
                f"{journal.ulid}, and push again."
            )
        if state == "open":
            return _send_and_commit(registry, journal, progress, resume=True)

        log.info("Upload %s of %s is %s: pushing it again from the start", journal.ulid, ref, state)
        opened = _open_upload(registry, ref, journal.files, journal.options)
        if isinstance(opened, ModelVersion):
            journal.cleanup(committed=True)
            return opened
        journal.restart(opened.ulid, opened.part_size)
        return _send_and_commit(registry, journal, progress, resume=False)


def abort(ulid: str, model: str | None = None) -> None:
    """Abort an upload and delete its local journal, if it was found by upload ID."""
    journal = find_journal(ulid) if model is None else None
    path = (
        journal.upload_path
        if journal is not None
        else f"{Ref.parse(model or '', version=False).path}/uploads/{ulid}"
    )
    with Registry() as registry:
        registry.call("DELETE", path, ok=204)
    if journal is not None:
        journal.cleanup()


def interrupted_uploads() -> list[tuple[Journal, str]]:
    """List resumable local uploads and clean up journals whose versions already exist."""
    found = []
    with Registry() as registry:
        for journal in journals():
            state = _upload_state(registry, journal)
            if state == "committed":
                journal.cleanup(committed=True)
            else:
                found.append((journal, state))
    return found


@dataclass(frozen=True)
class _OpenedUpload:
    ulid: str
    part_size: int


def _open_upload(
    registry: Registry, ref: Ref, files: Mapping[str, UploadFile], options: dict[str, Any]
) -> _OpenedUpload | ModelVersion:
    declared = [{"path": path, "size": file["size"]} for path, file in sorted(files.items())]
    answer = registry.call(
        "POST", f"{ref.path}/uploads", {"files": declared, **options}, (200, 201)
    )
    # A successful request can return the existing version for this dedupe key.
    if "ulid" not in answer:
        return ModelVersion.from_api(answer)
    return _OpenedUpload(answer["ulid"], answer.get("part_size") or PART_SIZE)


def _commit(registry: Registry, journal: Journal) -> ModelVersion:
    files = [
        {"path": path, "sha256": file["sha256"]} for path, file in sorted(journal.files.items())
    ]
    return ModelVersion.from_api(
        registry.call("POST", f"{journal.upload_path}/commit", {"files": files})
    )


def _upload_state(registry: Registry, journal: Journal) -> str:
    try:
        return registry.call("GET", journal.upload_path)["state"]
    except ModelError as error:
        if error.status != 404:
            raise
        return "gone"


def _send_and_commit(
    registry: Registry, journal: Journal, progress: Progress | None, *, resume: bool
) -> ModelVersion:
    transfer = UploadTransfer(journal, s3_client(journal.ref.org), progress)
    transfer.send_files(resume=resume)
    version = _commit(registry, journal)
    journal.cleanup(committed=True)
    return version
