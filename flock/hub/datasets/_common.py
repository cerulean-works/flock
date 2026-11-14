"""What the parts of `flock.hub.datasets` share: the error, and the downloaded folder's
manifest (`flock-import.json`)."""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Self

from flock.hub.s3.client import S3Error

MANIFEST = "flock-import.json"
"""The file [`~hub.datasets.download_from_hf`] writes next to the files it downloads."""

FORMATS = {
    ".parquet": "parquet",
    ".jsonl": "json",
    ".json": "json",
    ".ndjson": "json",
    ".csv": "csv",
    ".arrow": "arrow",
}
"""The data files an import reads, by extension, and how each is read."""

COMPRESSIONS = (".gz", ".bz2", ".zst", ".lz4")
"""Compressions JSON Lines and CSV files may have (read by Arrow's input streams)."""


class DatasetError(S3Error):
    """
    A dataset couldn't be downloaded or converted: the repo has no files Flock reads, the files
    don't agree on a schema, the destination exists, or Flock refused.
    """


def file_format(path: str) -> str | None:
    """How the data file `path` is read (`parquet`, `json`, `csv` or `arrow`), or `None` when
    an import doesn't read it."""
    name = path.rpartition("/")[2].lower()
    for compression in COMPRESSIONS:
        if name.endswith(compression):
            found = FORMATS.get(Path(name.removesuffix(compression)).suffix)
            return found if found in ("json", "csv") else None
    return FORMATS.get(Path(name).suffix)


@dataclass(frozen=True)
class DataFile:
    """One file of a split, as `path` (relative to the folder) and how it's read."""

    path: str
    format: str


@dataclass(frozen=True)
class Manifest:
    """
    What a downloaded folder holds, as `flock-import.json` records it.

    Attributes:
        repo (`str`):
            The Hugging Face dataset, as `owner/name`.
        revision (`str`):
            The commit the files were downloaded at.
        configs (`list[str]`):
            Every config of the repo, which decides where splits land.
        splits (`dict[str, dict[str, list[DataFile]]]`):
            Each config downloaded, with each of its splits' files in order.
        card (`str`, *optional*):
            The dataset card, relative to the folder.
        ref (`str`, *optional*):
            `refs/convert/parquet` when the files are the Hub's Parquet conversion.
    """

    repo: str
    revision: str
    configs: list[str]
    splits: dict[str, dict[str, list[DataFile]]]
    card: str | None = None
    ref: str | None = None
    version: int = field(default=2)

    @property
    def files(self) -> list[DataFile]:
        """Every file of every config and split, in order."""
        return [f for splits in self.splits.values() for files in splits.values() for f in files]

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2) + "\n"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        if data.get("version") != 2:
            raise DatasetError(f"{MANIFEST} has a version this Flock doesn't read")
        splits = {
            config: {
                split: [DataFile(f["path"], f["format"]) for f in files]
                for split, files in found.items()
            }
            for config, found in data["splits"].items()
        }
        return cls(
            repo=data["repo"],
            revision=data["revision"],
            configs=list(data["configs"]),
            splits=splits,
            card=data.get("card"),
            ref=data.get("ref"),
        )

    @classmethod
    def read(cls, folder: Path) -> Self:
        """The manifest in `folder`. Raises [`DatasetError`] when there's none."""
        path = folder / MANIFEST
        try:
            data = json.loads(path.read_text())
        except FileNotFoundError:
            raise DatasetError(
                f"{folder} has no {MANIFEST}. Download it with flock datasets download first."
            ) from None
        except ValueError as error:
            raise DatasetError(f"{path} isn't valid JSON: {error}") from None
        try:
            return cls.from_dict(data)
        except (KeyError, TypeError) as error:
            raise DatasetError(f"{path} is missing {error}") from None
