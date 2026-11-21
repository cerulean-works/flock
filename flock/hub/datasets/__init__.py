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

# A dataset of the project pretrain, found by its path
where = datasets.location("eval/mmlu-filtered", project="pretrain")
lance.write_dataset(table, where.uri, storage_options=where.storage_options)
```

Each dataset is global (the org's) or belongs to one project, whose datasets are in
`flock-datasets` too, under a folder of `-projects/` named by the project's id. Paths are
relative to the scope, and `project=` picks it.

Downloading needs only the Hugging Face Hub, and converting only Flock, which signs in as
[`~cli.credentials.resolve`] does.
"""

import shutil
import tempfile
from collections.abc import Collection
from pathlib import Path
from typing import Any

from flock.hub._scope import split_project
from flock.hub.datasets._common import (
    MANIFEST,
    DataFile,
    DatasetError,
    DatasetLocation,
    Manifest,
    call,
    location,
)
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
    project: str | None = None,
) -> list[ConvertedSplit]:
    """
    Imports a Hugging Face dataset to Lance datasets in your org's `flock-datasets` bucket:
    [`download_from_hf`] then [`convert`], on a machine that reaches both the Hub and Flock.

    Args:
        repo (`str`):
            The dataset, as `owner/name`.
        dest (`str`):
            The folder to write to in the scope, such as `web/fineweb-edu`.
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
        project (`str`, *optional*):
            The project to import into, by name or as `org/project`. Defaults to the org's
            global datasets.

    Returns:
        `list[ConvertedSplit]`: each split's config, path, version and rows.

    Raises:
        [`~hub.datasets.DatasetError`]: as [`download_from_hf`] and [`convert`] do.
    """
    if to is not None:
        folder = download_from_hf(repo, to, config=config, splits=splits, revision=revision)
        return convert(folder, dest, org=org, workers=workers, mode=mode, project=project)
    temporary = Path(tempfile.mkdtemp(prefix="flock-hf-"))
    try:
        folder = download_from_hf(repo, temporary, config=config, splits=splits, revision=revision)
        return convert(folder, dest, org=org, workers=workers, mode=mode, project=project)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def list_datasets(
    path: str = "", *, project: str | None = None, org: str | None = None
) -> dict[str, Any]:
    """
    What the Datasets tab shows at a folder, as `GET /api/v1/orgs/{org}/datasets/at` answers,
    with every page joined.

    Args:
        path (`str`, *optional*):
            The folder. Defaults to the scope's root.
        project (`str`, *optional*):
            The project whose datasets to list, by name or as `org/project`. Defaults to the
            org's global datasets.
        org (`str`, *optional*):
            The org. Defaults to your default org.

    Returns:
        `dict[str, Any]`: `kind` (`folder`, or `dataset` when `path` is one), and for a folder
        its `datasets` (with their latest `version`), `folders` and `files`.

    Raises:
        [`~hub.datasets.DatasetError`]: without a sign-in, or when Flock refuses.
    """
    params: dict[str, Any] = {"path": path.strip("/")}
    found = call("GET", "at", project=project, org=org, params=params)
    while found.get("kind") == "folder" and found.get("cursor"):
        params["cursor"] = found["cursor"]
        page = call("GET", "at", project=project, org=org, params=params)
        for key in ("datasets", "folders", "files"):
            found[key] += page[key]
        found["cursor"] = page["cursor"]
    return found


def move(
    paths: Collection[str],
    to: str,
    *,
    to_project: str | None = None,
    to_global: bool = False,
    project: str | None = None,
    org: str | None = None,
) -> list[dict[str, Any]]:
    """
    Moves datasets and folders into the folder `to`, each keeping its last name, as the
    Datasets tab's Move does: `eval/mmlu` moved to `archive` becomes `archive/mmlu`. With
    `to_project` or `to_global`, they move to another scope (D166), from a project to global
    (promoting them) or back. The objects are copied, then deleted at the old path.

    Args:
        paths (`Collection[str]`):
            The datasets and folders, by path in their scope.
        to (`str`):
            The folder to move them into, in the scope they end up in. `""` is its root.
        to_project (`str`, *optional*):
            Move them into this project of the org, by name or as `org/project`.
        to_global (`bool`, *optional*, defaults to `False`):
            Move them to the org's global datasets.
        project (`str`, *optional*):
            The project they belong to now, by name or as `org/project`. Defaults to the org's
            global datasets.
        org (`str`, *optional*):
            The org. Defaults to your default org.

    Returns:
        `list[dict[str, Any]]`: for each path, its `path` (in the old scope), `to` (in the
        new one), whether it `moved`, and if not the `error` (with `taken` set when something
        is already there). One that fails doesn't stop the rest.

    Raises:
        [`~hub.datasets.DatasetError`]: when they're over the size limits together, before
            anything moves (`body["command"]` has the `aws s3 mv` commands to run instead),
            or Flock refuses.
    """
    if to_project is not None and to_global:
        raise ValueError("give to_project or to_global, not both")
    if project is not None:
        org, project = split_project(project, org)
    body: dict[str, Any] = {"paths": [p.strip("/") for p in paths], "to": to.strip("/")}
    if to_global:
        body["to_project"] = None
    elif to_project is not None:
        target_org, body["to_project"] = split_project(to_project, org)
        org = org or target_org
    return call("POST", "move", project=project, org=org, body=body)["results"]


__all__ = [
    "BUCKET",
    "MANIFEST",
    "ConvertedSplit",
    "DataFile",
    "DatasetError",
    "DatasetLocation",
    "Manifest",
    "convert",
    "download_from_hf",
    "import_from_hf",
    "list_datasets",
    "location",
    "move",
]
