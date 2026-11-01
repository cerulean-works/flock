"""Pushing a version (D101, D102): a local folder, a journal beside it, parts sent through the
proxy from one pool, and resuming from the journal and what S3 already holds.

Every push starts from a folder. `save_to_hub` writes a model to
`{cache}/uploads/{ulid}/model/` first; a folder pushed as it is stays where it is. Either way
`{cache}/uploads/{ulid}/journal.json` is written before any byte is sent, and the upload's
folder is removed once the commit answers.
"""

import base64
import hashlib
import json
import logging
import math
import os
import secrets
import shutil
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Any

from flock.hub.models._common import (
    PART_SIZE,
    ModelError,
    ModelVersion,
    Pool,
    Ref,
    Registry,
    cache_root,
    concurrency,
    s3_client,
)
from flock.hub.models.files import walk

if TYPE_CHECKING:
    import wandb

    from flock.training.core.models.base import BaseModel

log = logging.getLogger(__name__)

JOURNAL = "journal.json"
STAGED = "model"  # where save_to_hub writes a model, inside its upload's folder
STAGING = ".staging-"  # a model being written, before its upload is opened
BUCKET = "flock-models"
FORMAT = 1
STALE_STAGING = 86400.0  # staging folders older than this were left by a crash

type Progress = Callable[[int], None]
# Aliases are evaluated lazily, so the signature can name classes this module doesn't import.
type ModelOrFolder = BaseModel | str | os.PathLike[str]
type RunOrPath = wandb.Run | str | None


def uploads_root() -> Path:
    return cache_root() / "uploads"


# ---------------------------------------------------------------------- the journal (D102)


class Journal:
    """`journal.json` of one upload: the model, the source folder, the upload's options and
    each file's size, modified time, sha256, multipart upload id and whether it's done."""

    def __init__(self, path: Path, data: dict[str, Any]) -> None:
        self.path = path
        self.data = data
        self._lock = threading.Lock()

    @classmethod
    def load(cls, path: Path) -> Journal | None:
        try:
            data = json.loads(path.read_text())
        except OSError, ValueError:
            return None
        if not isinstance(data, dict) or data.get("format") != FORMAT:
            return None
        return cls(path, data)

    @property
    def folder(self) -> Path:
        return self.path.parent

    @property
    def ulid(self) -> str:
        return self.data["ulid"]

    @property
    def model(self) -> str:
        return self.data["model"]

    @property
    def ref(self) -> Ref:
        return Ref.parse(self.model, version=False)

    @property
    def source(self) -> Path:
        return Path(self.data["source"])

    @property
    def files(self) -> dict[str, dict[str, Any]]:
        return self.data["files"]

    @property
    def prefix(self) -> str:
        return f"{self.ref.name}/u/{self.ulid}/"

    def save(self) -> None:
        with self._lock:
            self._write()

    def _write(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        partial = self.path.with_name(JOURNAL + ".partial")
        partial.write_text(json.dumps(self.data, indent=2) + "\n")
        os.replace(partial, self.path)

    def update(self, path: str, **fields: Any) -> None:
        with self._lock:
            self.files[path].update(fields)
            self._write()

    def sent(self) -> int:
        """Bytes of the files that are done."""
        return sum(f["size"] for f in self.files.values() if f["done"])

    def total(self) -> int:
        return sum(f["size"] for f in self.files.values())


def journals(model: str | None = None) -> list[Journal]:
    """The journals left on this machine (of `model`, if given), oldest first. Staging folders
    a crash left are removed on the way."""
    root = uploads_root()
    if not root.is_dir():
        return []
    found = []
    for folder in sorted(root.iterdir()):
        if folder.name.startswith(STAGING):
            try:
                if time.time() - folder.stat().st_mtime > STALE_STAGING:
                    shutil.rmtree(folder, ignore_errors=True)
            except OSError:
                pass
            continue
        journal = Journal.load(folder / JOURNAL)
        if journal is not None and (model is None or journal.model == model):
            found.append(journal)
    return found


def find_journal(ulid: str) -> Journal:
    """The local journal whose upload's ULID is `ulid` or starts with it."""
    matches = [j for j in journals() if j.ulid.startswith(ulid.upper())]
    if not matches:
        raise ModelError(f"There's no upload {ulid} on this machine.")
    if len(matches) > 1:
        raise ModelError(f"{ulid} could be any of {', '.join(j.ulid for j in matches)}.")
    return matches[0]


def remove(journal: Journal, keep_local: bool = False) -> None:
    """Deletes the upload's local folder, or only its journal with `keep_local` (a staged
    model stays in `{ulid}/model/`)."""
    if journal.folder.parent != uploads_root():
        raise AssertionError(f"{journal.folder} isn't an upload's folder")
    if keep_local and journal.data.get("staged"):
        journal.path.unlink(missing_ok=True)
    else:
        shutil.rmtree(journal.folder, ignore_errors=True)


# ---------------------------------------------------------------------- local files


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(8 * 2**20):
            digest.update(chunk)
    return digest.hexdigest()


def describe(source: Path, paths: Sequence[str]) -> dict[str, dict[str, Any]]:
    """Each file's size, modified time and sha256, read in a few threads."""

    def one(path: str) -> dict[str, Any]:
        stat = (source / path).stat()
        return {"size": stat.st_size, "mtime": stat.st_mtime, "sha256": sha256_of(source / path)}

    with ThreadPoolExecutor(min(8, os.cpu_count() or 1)) as pool:
        described = dict(zip(paths, pool.map(one, paths), strict=True))
    for entry in described.values():
        entry.update(upload_id=None, done=False)
    return described


def unchanged(journal: Journal) -> str | None:
    """The first file that changed or went missing since the journal was written, if any."""
    for path, entry in journal.files.items():
        try:
            stat = (journal.source / path).stat()
        except OSError:
            return path
        if stat.st_size != entry["size"] or stat.st_mtime != entry["mtime"]:
            return path
    return None


def _check_local(journal: Journal) -> None:
    changed = unchanged(journal)
    if changed is not None:
        raise ModelError(
            f"{changed} in {journal.source} changed or is gone since upload {journal.ulid} "
            f"started, so it can't be resumed. Abort it with flock models uploads abort "
            f"{journal.ulid}, and push again."
        )


def _md5(data: bytes) -> str:
    return base64.b64encode(hashlib.md5(data).digest()).decode()


def _read(path: Path, offset: int, size: int) -> bytes:
    with path.open("rb") as file:
        file.seek(offset)
        return file.read(size)


# ---------------------------------------------------------------------- sending (D101)


def _missing(error: Exception) -> bool:
    response = getattr(error, "response", None) or {}
    code = response.get("Error", {}).get("Code")
    status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return code in ("404", "NoSuchKey", "NoSuchUpload", "NotFound") or status == 404


class _Sender:
    """Sends a journal's files that aren't done (D102, step 3), all parts from one pool."""

    def __init__(self, journal: Journal, s3: Any, progress: Progress | None) -> None:
        self.journal = journal
        self.s3 = s3
        self.progress = progress
        self.part_size = journal.data.get("part_size") or PART_SIZE
        self._lock = threading.Lock()

    def key(self, path: str) -> str:
        return self.journal.prefix + path

    def sent(self, size: int) -> None:
        if self.progress is not None:
            self.progress(size)

    def run(self, check: bool) -> None:
        """Sends what's missing; with `check`, first asks S3 what already landed."""
        pool = Pool(concurrency())
        for path, entry in sorted(self.journal.files.items()):
            if not entry["done"]:
                pool.submit(self.file, pool, path, check)
        pool.wait()

    def file(self, pool: Pool, path: str, check: bool) -> None:
        entry = self.journal.files[path]
        if check and self.landed(path):
            self.journal.update(path, done=True, upload_id=None)
            return
        if entry["size"] <= self.part_size:
            pool.submit(self.put, path)
            return
        upload_id, kept = entry["upload_id"], {}
        if upload_id is not None:
            found = self.parts(path, upload_id) if check else None
            if found is None:  # the multipart upload is gone: start the file again
                upload_id = None
            else:
                kept = found
        if upload_id is None:
            created = self.s3.create_multipart_upload(
                Bucket=BUCKET, Key=self.key(path), Metadata={"sha256": entry["sha256"]}
            )
            upload_id = created["UploadId"]
            self.journal.update(path, upload_id=upload_id)
        count = math.ceil(entry["size"] / self.part_size)
        state: dict[str, Any] = {"left": count, "etags": {}}
        for number in range(1, count + 1):
            pool.submit(self.part, path, upload_id, number, kept.get(number), state)

    def landed(self, path: str) -> bool:
        """Whether the whole object is there, with the right size and sha256."""
        entry = self.journal.files[path]
        try:
            head = self.s3.head_object(Bucket=BUCKET, Key=self.key(path))
        except Exception as error:
            if _missing(error):
                return False
            raise
        found = head.get("Metadata", {}).get("sha256")
        return head["ContentLength"] == entry["size"] and found == entry["sha256"]

    def parts(self, path: str, upload_id: str) -> dict[int, str] | None:
        """The ETags (MD5s, on Ceph and AWS) of the parts already in the multipart upload, or
        None when it's gone."""
        etags = {}
        try:
            pages = self.s3.get_paginator("list_parts").paginate(
                Bucket=BUCKET, Key=self.key(path), UploadId=upload_id
            )
            for page in pages:
                for part in page.get("Parts", []):
                    etags[part["PartNumber"]] = part["ETag"].strip('"')
        except Exception as error:
            if _missing(error):
                return None
            raise
        return etags

    def put(self, path: str) -> None:
        entry = self.journal.files[path]
        data = (self.journal.source / path).read_bytes()
        if len(data) != entry["size"]:
            raise ModelError(f"{path} changed while it was being uploaded")
        self.s3.put_object(
            Bucket=BUCKET,
            Key=self.key(path),
            Body=data,
            ContentMD5=_md5(data),
            Metadata={"sha256": entry["sha256"]},
        )
        self.journal.update(path, done=True, upload_id=None)
        self.sent(len(data))

    def part(
        self, path: str, upload_id: str, number: int, etag: str | None, state: dict[str, Any]
    ) -> None:
        offset = (number - 1) * self.part_size
        data = _read(self.journal.source / path, offset, self.part_size)
        md5 = hashlib.md5(data)
        if etag != md5.hexdigest():  # a part that landed with the same bytes is kept
            answer = self.s3.upload_part(
                Bucket=BUCKET,
                Key=self.key(path),
                UploadId=upload_id,
                PartNumber=number,
                Body=data,
                ContentMD5=base64.b64encode(md5.digest()).decode(),
            )
            etag = answer["ETag"].strip('"')
            self.sent(len(data))
        with self._lock:
            state["etags"][number] = etag
            state["left"] -= 1
            last = state["left"] == 0
        if last:
            parts = [{"PartNumber": n, "ETag": f'"{e}"'} for n, e in sorted(state["etags"].items())]
            self.s3.complete_multipart_upload(
                Bucket=BUCKET,
                Key=self.key(path),
                UploadId=upload_id,
                MultipartUpload={"Parts": parts},
            )
            self.journal.update(path, done=True, upload_id=None)


# ---------------------------------------------------------------------- uploads (D100, D102)


def _open(registry: Registry, ref: Ref, files: Mapping[str, Mapping[str, Any]], options: dict):
    """Opens an upload: its answer, or the version that already has the dedupe key."""
    declared = [{"path": p, "size": f["size"]} for p, f in sorted(files.items())]
    answer = registry.call(
        "POST", f"{ref.path}/uploads", {"files": declared, **options}, (200, 201)
    )
    return answer if "ulid" in answer else ModelVersion.from_api(answer)


def _commit(registry: Registry, journal: Journal) -> ModelVersion:
    body = {"files": [{"path": p, "sha256": f["sha256"]} for p, f in sorted(journal.files.items())]}
    path = f"{journal.ref.path}/uploads/{journal.ulid}/commit"
    return ModelVersion.from_api(registry.call("POST", path, body))


def _send(journal: Journal, registry: Registry, check: bool, progress: Progress | None):
    """Sends what's missing (asking S3 first with `check`), commits and cleans up."""
    _Sender(journal, s3_client(journal.ref.org), progress).run(check)
    version = _commit(registry, journal)
    remove(journal, journal.data.get("keep_local", False))
    return version


def _start(
    journal: Journal, registry: Registry, upload: dict[str, Any], progress: Progress | None
) -> ModelVersion:
    """Records the newly opened `upload` in the journal (moving the folder to its ULID), and
    pushes every file."""
    folder = uploads_root() / upload["ulid"]
    if journal.folder != folder:
        os.replace(journal.folder, folder)
        journal.path = folder / JOURNAL
        if journal.data.get("staged"):
            journal.data["source"] = str(folder / STAGED)
    journal.data["ulid"] = upload["ulid"]
    journal.data["part_size"] = upload.get("part_size") or PART_SIZE
    for entry in journal.files.values():
        entry.update(upload_id=None, done=False)
    journal.save()  # before any byte is sent
    return _send(journal, registry, check=False, progress=progress)


def push(
    source: Path,
    ref: Ref,
    options: dict[str, Any],
    *,
    staged: bool = False,
    keep_local: bool = False,
    resume: bool = True,
    progress: Progress | None = None,
    described: dict[str, dict[str, Any]] | None = None,
) -> ModelVersion:
    """
    Pushes the folder `source` as a new version of `ref`, or resumes a journal for the same
    model and folder whose files haven't changed. `staged` says `source` is a staging folder
    that save_to_hub wrote, moved into the upload's folder once it's open.
    """
    paths = list(walk(source))
    if not paths:
        raise ModelError(f"{source} has no files to push")
    if resume and not staged:
        for journal in journals(ref.model):
            same = journal.source == source and sorted(journal.files) == paths
            if same and unchanged(journal) is None:
                log.info("Resuming upload %s of %s from %s", journal.ulid, ref, source)
                return resume_journal(journal, progress=progress)
    files = described or describe(source, paths)
    with Registry() as registry:
        opened = _open(registry, ref, files, options)
        if isinstance(opened, ModelVersion):  # a version has the dedupe key
            if staged and not keep_local:
                shutil.rmtree(source, ignore_errors=True)
            return opened
        folder = uploads_root() / opened["ulid"]
        folder.mkdir(parents=True)
        if staged:
            os.replace(source, folder / STAGED)
            source = folder / STAGED
        data = {
            "format": FORMAT,
            "model": ref.model,
            "ulid": opened["ulid"],
            "source": str(source),
            "staged": staged,
            "keep_local": keep_local,
            "part_size": opened.get("part_size") or PART_SIZE,
            "options": options,
            "created_at": time.time(),
            "files": files,
        }
        journal = Journal(folder / JOURNAL, data)
        journal.save()  # before any byte is sent
        return _send(journal, registry, check=False, progress=progress)


def resume_journal(journal: Journal, progress: Progress | None = None) -> ModelVersion:
    """
    Resumes one upload from its journal, as D102 says: a committed upload is committed again
    (which answers its version), an expired or aborted one is opened again and pushed from the
    start, and an open one gets what S3 doesn't already hold, then the commit.

    Raises:
        [`~hub.models.ModelError`]: when a local file changed or is gone since the upload started.
    """
    version = _resume(journal, progress, forget=False)
    assert version is not None
    return version


def _resume(journal: Journal, progress: Progress | None, forget: bool) -> ModelVersion | None:
    """`resume_journal`; with `forget`, an upload that isn't open and whose local files
    changed is forgotten (None) rather than refused."""
    ref = journal.ref
    with Registry() as registry:
        try:
            state = registry.call("GET", f"{ref.path}/uploads/{journal.ulid}")["state"]
        except ModelError as error:
            if error.status != 404:
                raise
            state = "gone"
        if state == "committed":
            version = _commit(registry, journal)  # committing again answers the version
            remove(journal, journal.data.get("keep_local", False))
            return version
        if forget and state != "open" and unchanged(journal) is not None:
            log.info(
                "Upload %s of %s is %s and its files changed: forgetting it",
                journal.ulid,
                ref,
                state,
            )
            remove(journal)
            return None
        _check_local(journal)
        if state == "open":
            return _send(journal, registry, check=True, progress=progress)
        log.info("Upload %s of %s is %s: pushing it again from the start", journal.ulid, ref, state)
        opened = _open(registry, ref, journal.files, journal.data.get("options", {}))
        if isinstance(opened, ModelVersion):
            remove(journal, journal.data.get("keep_local", False))
            return opened
        return _start(journal, registry, opened, progress)


def abort(journal_or_ulid: Journal | str, model: str | None = None) -> None:
    """Aborts an upload, deleting what it sent, and removes its local folder. With `model`,
    an upload that has no journal on this machine can be aborted too."""
    journal = None
    if isinstance(journal_or_ulid, Journal):
        journal, ulid = journal_or_ulid, journal_or_ulid.ulid
    elif model is None:
        journal = find_journal(journal_or_ulid)
        ulid = journal.ulid
    else:
        ulid = journal_or_ulid
    ref = journal.ref if journal is not None else Ref.parse(model or "", version=False)
    with Registry() as registry:
        registry.call("DELETE", f"{ref.path}/uploads/{ulid}", ok=204)
    if journal is not None:
        remove(journal)


def states(found: Sequence[Journal]) -> list[tuple[Journal, str]]:
    """Each journal with its upload's state (`gone` if Flock doesn't know it). Journals of
    committed uploads are removed, as their versions exist."""
    answered = []
    with Registry() as registry:
        for journal in found:
            try:
                state = registry.call("GET", f"{journal.ref.path}/uploads/{journal.ulid}")["state"]
            except ModelError as error:
                if error.status != 404:
                    raise
                state = "gone"
            if state == "committed":
                remove(journal, journal.data.get("keep_local", False))
            answered.append((journal, state))
    return answered


# ---------------------------------------------------------------------- the Python API (D104)


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

        # Not a daemon: a script that ends without waiting still finishes the upload.
        self._thread = threading.Thread(target=run, name=f"flock-models-{name}")
        self._thread.start()

    def result(self, timeout: float | None = None) -> ModelVersion:
        """
        Waits for the version and returns it.

        Args:
            timeout (`float`, *optional*):
                Seconds to wait. Waits until the upload ends if not set.

        Raises:
            `TimeoutError`: if `timeout` passes first.
            [`~hub.models.ModelError`]: if the upload failed. Its journal stays, so
                [`~hub.models.resume_uploads`] can finish it.
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

    The model is written to `~/.cache/flock/uploads/{ulid}/model/` first (`FLOCK_CACHE`
    replaces `~/.cache/flock`), with a journal beside it, then uploaded through the S3 proxy.
    A version appears only once every file has landed, so nobody loads half of one.

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
            The run that made the model, or its `entity/project/id`. It goes in `flock.json`,
            and links the version to the run when the run is in the same org.
        step (`int`, *optional*):
            The training step, stored with the version and in `flock.json`.
        dedupe_key (`str`, *optional*):
            If a version of the model already has this key, it's returned and nothing is
            uploaded. Defaults to `{run id}:{step}` when `run` and `step` are given, so a
            restarted job doesn't push a step twice.
        training_state (`Mapping[str, Any]`, *optional*):
            Names mapped to state dicts, such as `{"optimizer": optimizer.state_dict()}`. Each
            is saved as `training_state/{name}.safetensors`, which loading skips unless asked.
        shard_size (`int`, *optional*, defaults to 5 GiB):
            The largest weights file, in bytes. A larger state dict is split into shards with
            `model.safetensors.index.json`.
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
        [`~hub.models.ModelError`]: when Flock refuses the upload or a file can't be sent. The journal
            stays, so calling again (or [`~hub.models.resume_uploads`]) picks up where it stopped.
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
    job: Callable[[], ModelVersion]
    if isinstance(model, (str, os.PathLike)):
        if training_state:
            raise ValueError("training_state can only be saved with a model, not a folder")
        source = Path(model).expanduser().resolve()
        if not source.is_dir():
            raise ModelError(f"{source} isn't a folder")

        def job() -> ModelVersion:
            return push(source, ref, options, resume=resume, keep_local=True)

    else:
        earlier = None
        if resume and dedupe_key is not None:
            earlier = next(
                (
                    j
                    for j in journals(ref.model)
                    if j.data.get("staged")
                    and j.data.get("options", {}).get("dedupe_key") == dedupe_key
                ),
                None,
            )
        if earlier is not None:
            log.info("Resuming upload %s of %s (%s)", earlier.ulid, ref, dedupe_key)

            def job() -> ModelVersion:
                return resume_journal(earlier)

        else:
            from flock.hub.models.files import write_model

            staging = uploads_root() / f"{STAGING}{secrets.token_hex(8)}"
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

            def job() -> ModelVersion:
                return push(staging, ref, options, staged=True, keep_local=keep_local)

    if blocking:
        return job()
    return PendingUpload(ref.model, job)


def resume_uploads(name: str) -> list[ModelVersion]:
    """
    Finishes every upload of `name` that a crashed process left on this machine, and returns
    the versions made. Call it when a job starts, before saving anything new.

    Each is resumed as [`~hub.models.save_to_hub`] would: only what didn't land is sent, an upload that
    expired starts again from its local files, and one whose local files are gone is
    forgotten.

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
        version = _resume(journal, None, forget=True)
        if version is not None:
            done.append(version)
    return done
