"""Datasets in Flock (017): Hugging Face datasets imported as Lance datasets in each org's
`flock-datasets` bucket, and what the Datasets tab shows.

```python
from flock.hub import datasets

# One step, for one config or several
written = datasets.import_from_hf(
    "HuggingFaceFW/fineweb-edu", "web/fineweb-edu", config=["sample-10BT", "sample-100BT"]
)

# Or two, when the download and the conversion run on different machines
folder = datasets.download_from_hf(
    "HuggingFaceFW/fineweb-edu", "/scratch/fineweb-edu", config="sample-10BT"
)
written = datasets.convert(folder, "web/fineweb-edu", workers=64)
```

Downloading needs only the Hugging Face Hub, and converting only Flock, which signs in as
[`~cli.credentials.resolve`] does.
"""

import shutil
import tempfile
from collections.abc import Collection
from pathlib import Path
from typing import Any

from flock.hub.datasets._common import MANIFEST, DataFile, DatasetError, Manifest
from flock.hub.datasets._convert import BUCKET, ConvertedSplit, Mode, convert
from flock.hub.datasets._download import Configs, download_from_hf


def import_from_hf(
    repo: str,
    dest: str,
    *,
    config: Configs = None,
    splits: Collection[str] | None = None,
    revision: str | None = None,
    to: str | Path | None = None,
    org: str | None = None,
    workers: int | None = None,
    mode: Mode = "create",
) -> list[ConvertedSplit]:
    """
    Imports a Hugging Face dataset to Lance datasets in your org's `flock-datasets` bucket:
    [`download_from_hf`] then [`convert`], on a machine that reaches both the Hub and Flock.

    Args:
        repo (`str`):
            The dataset, as `owner/name`.
        dest (`str`):
            The folder of `flock-datasets` to write to, such as `web/fineweb-edu`.
        config (`str`, `Collection[str]` or `Mapping[str, Collection[str] | None]`, *optional*):
            The configs, by name, or as a mapping from each name to its splits (`None` for those
            `splits` picks). Defaults to the only one, the card's default, or `default`. With
            several, each split lands at `{dest}/{config}/{split}`.
        splits (`Collection[str]`, *optional*):
            The splits to import of each config that has them, for configs not given their own.
            Defaults to all. A split no config has is refused, and so is a config left with
            none.
        revision (`str`, *optional*):
            A branch, tag or commit. Defaults to `main`.
        to (`str` or `Path`, *optional*):
            Download here and keep the files. Defaults to a temporary folder that's deleted
            afterwards.
        org (`str`, *optional*):
            The org whose `flock-datasets` to write to. Defaults to your default org.
        workers (`int`, *optional*):
            Processes converting files at once. Defaults to the CPUs this process may use.
        mode (`str`, *optional*, defaults to `"create"`):
            `create`, `overwrite` or `append`, as [`convert`] takes.

    Returns:
        `list[ConvertedSplit]`: each split's config, path, version and rows.

    Raises:
        [`~hub.datasets.DatasetError`]: as [`download_from_hf`] and [`convert`] do.
    """
    if to is not None:
        folder = download_from_hf(repo, to, config=config, splits=splits, revision=revision)
        return convert(folder, dest, org=org, workers=workers, mode=mode)
    temporary = Path(tempfile.mkdtemp(prefix="flock-hf-"))
    try:
        folder = download_from_hf(repo, temporary, config=config, splits=splits, revision=revision)
        return convert(folder, dest, org=org, workers=workers, mode=mode)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def list_datasets(path: str = "", *, org: str | None = None) -> dict[str, Any]:
    """
    What the Datasets tab shows at a folder of `flock-datasets`, as
    `GET /api/v1/orgs/{org}/datasets/at` answers, with every page joined.

    Args:
        path (`str`, *optional*):
            The folder. Defaults to the bucket's root.
        org (`str`, *optional*):
            The org. Defaults to your default org.

    Returns:
        `dict[str, Any]`: `kind` (`folder`, or `dataset` when `path` is one), and for a folder
        its `datasets` (with their latest `version`), `folders` and `files`.

    Raises:
        [`~hub.datasets.DatasetError`]: without a sign-in, or when Flock refuses.
    """
    from flock.hub.s3 import S3Error
    from flock.hub.s3.client import _Api

    path = path.strip("/")
    try:
        with _Api(None, None) as api:
            route = f"orgs/{api.default_org(org)}/datasets/at"
            params: dict[str, Any] = {"path": path}
            found = api.call("GET", route, params=params)
            while found.get("kind") == "folder" and found.get("cursor"):
                params["cursor"] = found["cursor"]
                page = api.call("GET", route, params=params)
                for key in ("datasets", "folders", "files"):
                    found[key] += page[key]
                found["cursor"] = page["cursor"]
    except S3Error as error:
        raise DatasetError(str(error), error.status, error.body) from None
    return found


__all__ = [
    "BUCKET",
    "MANIFEST",
    "ConvertedSplit",
    "DataFile",
    "DatasetError",
    "Manifest",
    "convert",
    "download_from_hf",
    "import_from_hf",
    "list_datasets",
]
