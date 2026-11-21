"""What the parts of `flock.hub.datasets` share: the error, the downloaded folder's manifest
(`flock-import.json`), the API's datasets routes, and where a dataset lives."""

import json
from collections.abc import Collection
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Self

from flock.hub._scope import PROJECTS, scope_params, split_project
from flock.hub.s3.client import S3Error, _Api

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


def call(
    method: str,
    route: str,
    *,
    project: str | None = None,
    org: str | None = None,
    body: Any = None,
    params: dict[str, Any] | None = None,
    ok: int | Collection[int] = 200,
) -> Any:
    """Calls `orgs/{org}/datasets/{route}` in a scope (D163): `project`, by name in `org` or
    your default org, or as `org/project`, or the org's global datasets without one."""
    try:
        with _Api(None, None) as api:
            if project is not None:
                org, project = split_project(project, org)
            owner = api.default_org(org)
            query = {**scope_params(project), **(params or {})}
            return api.call(method, f"orgs/{owner}/datasets/{route}", body, ok, query)
    except S3Error as error:
        raise DatasetError(str(error), error.status, error.body) from None


def check_global(path: str) -> None:
    """Refuses a global path under `-projects/`, where Flock keeps projects' datasets (D160)."""
    if path.strip("/").split("/")[0] == PROJECTS:
        raise DatasetError(f"{PROJECTS} is where Flock keeps projects' datasets.")


@dataclass(frozen=True)
class DatasetLocation:
    """
    Where a dataset is in S3, from [`~hub.datasets.location`], with what Lance needs to open
    it.

    Attributes:
        uri (`str`):
            The dataset's URI, such as `s3://flock-datasets/eval/mmlu`, or for a project's
            dataset `s3://flock-datasets/-projects/412/eval/mmlu`.
        storage_options (`dict[str, str]`):
            Lance's `storage_options` with your S3 key for the org's buckets. A snapshot: the
            key expires, so ask again for a long job, or see [`~hub.s3.get_process`].
        bucket (`str`):
            The bucket, `flock-datasets`.
        key (`str`):
            The dataset's folder in the bucket, such as `-projects/412/eval/mmlu`.
        path (`str`):
            The dataset's path in its scope, such as `eval/mmlu`.
        project (`str`, *optional*):
            The project the dataset belongs to, or `None` for a global dataset.
    """

    uri: str
    storage_options: dict[str, str] = field(repr=False)
    bucket: str
    key: str
    path: str
    project: str | None = None


def location(path: str, project: str | None = None, org: str | None = None) -> DatasetLocation:
    """
    Where the dataset `path` is, or would be, in S3 (D160): its URI, and storage options with
    your own S3 key. A project's datasets are under a folder named by the project's id, so ask
    here rather than writing the URI out.

    Args:
        path (`str`):
            The dataset's path in its scope, such as `eval/mmlu`. It needn't exist yet.
        project (`str`, *optional*):
            The project the dataset belongs to, by name or as `org/project`. Defaults to the
            org's global datasets.
        org (`str`, *optional*):
            The org. Defaults to your default org.

    Returns:
        [`~hub.datasets.DatasetLocation`]: the URI, storage options, bucket, key, path and project.

    Raises:
        [`~hub.datasets.DatasetError`]: without a sign-in, when the project doesn't exist, or
            when Flock refuses the path.

    Example:

    ```python
    import lance
    from flock.hub import datasets

    where = datasets.location("eval/mmlu-filtered", project="pretrain")
    lance.write_dataset(table, where.uri, storage_options=where.storage_options)
    ```
    """
    from flock.hub import s3

    if project is not None:
        org, project = split_project(project, org)
    found = call("GET", "location", project=project, org=org, params={"path": path.strip("/")})
    try:
        options = s3.credentials(org).storage_options()
    except S3Error as error:
        raise DatasetError(str(error), error.status, error.body) from None
    return DatasetLocation(
        uri=found["uri"],
        storage_options=options,
        bucket=found["bucket"],
        key=found["key"],
        path=found["path"],
        project=found.get("project", project),
    )
