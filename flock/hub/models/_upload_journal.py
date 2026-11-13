"""Local state for an upload. Journal format 1 is shared with earlier Flock clients."""

import json
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any, NotRequired, TypedDict, cast

from flock.hub.models._common import PART_SIZE, ModelError, Ref, cache_root

JOURNAL_NAME = "journal.json"
JOURNAL_FORMAT = 1
STAGED_MODEL = "model"
STAGING_PREFIX = ".staging-"
STALE_STAGING_SECONDS = 86400


def uploads_root() -> Path:
    return cache_root() / "uploads"


class UploadFile(TypedDict):
    size: int
    mtime: float
    sha256: str
    upload_id: str | None
    done: bool


class _JournalData(TypedDict):
    format: int
    model: str
    ulid: str
    source: str
    staged: NotRequired[bool]
    keep_local: NotRequired[bool]
    part_size: NotRequired[int]
    options: NotRequired[dict[str, Any]]
    created_at: float
    files: dict[str, UploadFile]


class Journal:
    """The source files, registry options, and transfer progress for one upload."""

    def __init__(self, folder: Path, data: _JournalData) -> None:
        self.folder = folder
        self._data = data
        self._lock = threading.Lock()

    @classmethod
    def load(cls, folder: Path) -> Journal | None:
        try:
            data = json.loads((folder / JOURNAL_NAME).read_text())
        except OSError, ValueError:
            return None
        if not isinstance(data, dict) or data.get("format") != JOURNAL_FORMAT:
            return None
        return cls(folder, cast(_JournalData, data))

    @classmethod
    def create(
        cls,
        ref: Ref,
        ulid: str,
        part_size: int,
        source: Path,
        files: dict[str, UploadFile],
        options: dict[str, Any],
        *,
        staged: bool,
        keep_local: bool,
    ) -> Journal:
        folder = uploads_root() / ulid
        folder.mkdir(parents=True)
        if staged:
            os.replace(source, folder / STAGED_MODEL)
            source = folder / STAGED_MODEL
        journal = cls(
            folder,
            {
                "format": JOURNAL_FORMAT,
                "model": ref.model,
                "ulid": ulid,
                "source": str(source),
                "staged": staged,
                "keep_local": keep_local,
                "part_size": part_size,
                "options": options,
                "created_at": time.time(),
                "files": files,
            },
        )
        journal._write()
        return journal

    @property
    def ulid(self) -> str:
        return self._data["ulid"]

    @property
    def model(self) -> str:
        return self._data["model"]

    @property
    def ref(self) -> Ref:
        return Ref.parse(self.model, version=False)

    @property
    def source(self) -> Path:
        return Path(self._data["source"])

    @property
    def files(self) -> dict[str, UploadFile]:
        return self._data["files"]

    @property
    def staged(self) -> bool:
        return self._data.get("staged", False)

    @property
    def options(self) -> dict[str, Any]:
        return self._data.get("options", {})

    @property
    def part_size(self) -> int:
        return self._data.get("part_size") or PART_SIZE

    @property
    def upload_path(self) -> str:
        """Registry route for this upload."""
        return f"{self.ref.path}/uploads/{self.ulid}"

    def restart(self, ulid: str, part_size: int) -> None:
        """Move the journal and staged files to a new upload and clear S3 progress."""
        folder = uploads_root() / ulid
        if self.folder != folder:
            os.replace(self.folder, folder)
            self.folder = folder
            if self.staged:
                self._data["source"] = str(folder / STAGED_MODEL)
        self._data["ulid"] = ulid
        self._data["part_size"] = part_size
        for file in self.files.values():
            file.update(upload_id=None, done=False)
        self._write()

    def _write(self) -> None:
        # Readers see a complete journal even if a process stops during a write.
        partial = self.folder / (JOURNAL_NAME + ".partial")
        partial.write_text(json.dumps(self._data, indent=2) + "\n")
        os.replace(partial, self.folder / JOURNAL_NAME)

    def record_transfer(self, path: str, *, upload_id: str | None, done: bool = False) -> None:
        """Record a multipart ID or a completed file before the worker proceeds."""
        with self._lock:
            self.files[path].update(upload_id=upload_id, done=done)
            self._write()

    def changed_file(self) -> str | None:
        """The first source file whose size or modification time changed, or is missing."""
        source = self.source
        for path, entry in self.files.items():
            try:
                stat = (source / path).stat()
            except OSError:
                return path
            if stat.st_size != entry["size"] or stat.st_mtime != entry["mtime"]:
                return path
        return None

    def cleanup(self, *, committed: bool = False) -> None:
        """Remove the journal and any staged files, honoring keep_local after success."""
        if self.folder.parent != uploads_root():
            raise AssertionError(f"{self.folder} isn't an upload's folder")
        if committed and self.staged and self._data.get("keep_local"):
            (self.folder / JOURNAL_NAME).unlink(missing_ok=True)
        else:
            shutil.rmtree(self.folder, ignore_errors=True)


def journals(model: str | None = None) -> list[Journal]:
    """Find local journals by upload ID order and clear stale staging directories."""
    root = uploads_root()
    if not root.is_dir():
        return []
    found = []
    for folder in sorted(root.iterdir()):
        if folder.name.startswith(STAGING_PREFIX):
            try:
                if time.time() - folder.stat().st_mtime > STALE_STAGING_SECONDS:
                    shutil.rmtree(folder, ignore_errors=True)
            except OSError:
                pass
            continue
        journal = Journal.load(folder)
        if journal is not None and (model is None or journal.model == model):
            found.append(journal)
    return found


def find_journal(ulid: str) -> Journal:
    """Find the local journal by full upload ID or a unique prefix."""
    matches = [j for j in journals() if j.ulid.startswith(ulid.upper())]
    if not matches:
        raise ModelError(f"There's no upload {ulid} on this machine.")
    if len(matches) > 1:
        raise ModelError(f"{ulid} could be any of {', '.join(j.ulid for j in matches)}.")
    return matches[0]
